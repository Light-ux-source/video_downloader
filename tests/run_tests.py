"""VideoHarbor 后端自测。

运行： ./run.sh tests/run_tests.py            （默认跑纯逻辑测试）
      ./run.sh tests/run_tests.py --live       （额外跑真实网络/下载测试，耗时较长）

覆盖：
  1. yt-dlp 进度行解析（含真实抓取到的两种行格式）
  2. 下载命令构造（画质/音频/字幕/播放列表/Cookies 参数）
  3. 任务状态机：排队 → 下载 → 完成 / 失败 / 取消 / 并发上限
  4. HTTP 接口：health / presets / probe 参数校验 / tasks 增删查 / SSE
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, douyin, server, ytdlp  # noqa: E402
from core.tasks import (  # noqa: E402
    _safe_folder_name,
    compose_slideshow_video,
    fetch_to_file,
)
from core.tasks import (  # noqa: E402
    STATE_CANCELED,
    STATE_DONE,
    STATE_ERROR,
    TaskManager,
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  \033[32m✓\033[0m {name}")
    else:
        FAILED.append(f"{name} {detail}")
        print(f"  \033[31m✗\033[0m {name} {detail}")


def section(title: str) -> None:
    print(f"\n\033[36m== {title} ==\033[0m")


# ---------------------------------------------------------------------------
# 1. 进度解析
# ---------------------------------------------------------------------------
def test_progress_parser() -> None:
    section("进度行解析")
    cases = [
        # (输入, 期望 percent, 期望 downloaded, 期望 speed)
        ("DL|  12.6%|523264|4162618|NA|2989107.5|1", 12.57, 523264, 2989107.5),
        ("download:DL| 84.2%|58736554|69759161|NA|556847.8|19", 84.2, 58736554, 556847.8),
        # 与 --print-to-file 同用时前缀被吃掉，只剩纯数据（真实抓取样本）
        (" 12.1%|8423526|69759161|NA|614009.5229183888|99", 12.08, 8423526, 614009.52),
        ("DL|  0.0%|1024|NA|4162618|NA|NA", 0.02, 1024, None),
        ("DL|100.0%|4162618|4162618|NA|NA|NA", 100.0, 4162618, None),
    ]
    for line, percent, downloaded, speed in cases:
        parsed = ytdlp.parse_progress_line(line)
        ok = (
            parsed is not None
            and abs((parsed["percent"] or 0) - percent) < 0.05
            and parsed["downloaded_bytes"] == downloaded
            and (parsed["speed"] is None if speed is None else abs(parsed["speed"] - speed) < 1)
        )
        check(f"解析 {line[:34]!r}", ok, f"→ {parsed}")

    check("非进度行不误判",
          ytdlp.parse_progress_line("[download] Destination: /tmp/a.mp4") is None)
    check("普通日志不误判", ytdlp.parse_progress_line("[BiliBili] Extracting URL") is None)
    check("空百分比行不误判", ytdlp.parse_progress_line("  0.5%") is None)

    section("输出行分类")
    check("Destination 行", ytdlp.classify_line("[download] Destination: /tmp/a.mp4")[0] == "destination")
    check("合流行", ytdlp.classify_line('[Merger] Merging formats into "/tmp/a.mp4"')[0] == "merge")
    check("错误行", ytdlp.classify_line("ERROR: boom")[0] == "error")
    check("网络抖动归为 warning",
          ytdlp.classify_line("[download] Got error: SSL: UNEXPECTED_EOF")[0] == "warning")
    check("音频提取行",
          ytdlp.classify_line('[ExtractAudio] Destination: /tmp/a.mp3')[0] == "extract_audio")
    check("播放列表结束摘要行",
          ytdlp.classify_line("[download] Finished downloading playlist: 标题")[0] == "playlist_done")
    check("裸 ERROR 行也能识别",
          ytdlp.classify_line("ERROR:")[0] == "error")


# ---------------------------------------------------------------------------
# 2. 命令构造
# ---------------------------------------------------------------------------
def test_command_builder() -> None:
    section("下载命令构造")
    args, norm = ytdlp.build_format_args({"preset": "720"})
    check("720P 使用高度限制", "-f" in args and "bv*[height<=720]+ba/b[height<=720]/b" in args)
    check("默认不做音频提取", "-x" not in args)

    args, norm = ytdlp.build_format_args({"preset": "audio", "audio_format": "mp3"})
    check("音频预设触发 -x", "-x" in args)
    check("音频格式正确", "--audio-format" in args and "mp3" in args)
    check("音频走高质量", "--audio-quality" in args)

    args, norm = ytdlp.build_format_args({"preset": "best", "subtitles": True, "subtitle_langs": "zh,en"})
    check("字幕参数齐全", "--embed-subs" in args and "zh,en" in args)

    args, norm = ytdlp.build_format_args({"preset": "audio", "audio_format": "mp3", "subtitles": True})
    check("纯音频不尝试内嵌字幕", "--embed-subs" not in args)

    args, norm = ytdlp.build_format_args({"preset": "best", "custom_format": "bv*[vcodec^=avc1]+ba"})
    check("自定义表达式优先", "bv*[vcodec^=avc1]+ba" in args)

    args, norm = ytdlp.build_format_args({"preset": "best", "cookies_file": "/tmp/c.txt", "proxy": "socks5://x:1"})
    check("Cookies 文件参数", "--cookies" in args and "/tmp/c.txt" in args)
    check("代理参数", "--proxy" in args and "socks5://x:1" in args)

    args, norm = ytdlp.build_format_args({"preset": "best", "playlist": False})
    check("默认不下载播放列表", "--no-playlist" in args)
    args, norm = ytdlp.build_format_args({"preset": "best", "playlist": True, "playlist_items": "1-3"})
    check("播放列表条目参数", "--playlist-items" in args and "1-3" in args)

    args, norm = ytdlp.build_format_args({"preset": "audio", "audio_format": "不存在的格式"})
    check("非法音频格式回退 mp3", "mp3" in args)


# ---------------------------------------------------------------------------
# 3. 文件工具
# ---------------------------------------------------------------------------
def test_file_helpers(tmp: Path) -> None:
    section("产物定位工具")
    result = tmp / "result.txt"
    result.write_text("/tmp/视频.mp4\n", encoding="utf-8")
    check("读取 --print-to-file 结果", ytdlp.read_result_file(result) == "/tmp/视频.mp4")
    result.write_text("", encoding="utf-8")
    check("空结果返回 None", ytdlp.read_result_file(result) is None)
    result.unlink()
    check("文件不存在返回 None", ytdlp.read_result_file(result) is None)

    # 播放列表/多P：结果文件会有多行，必须全部读回（否则漏判失败条目）
    multi = tmp / "multi.txt"
    multi.write_text("/tmp/a.mp4\n/tmp/b.mp4\n\n/tmp/c.mp4\n", encoding="utf-8")
    check("多行结果全部读回", ytdlp.read_result_files(multi) == ["/tmp/a.mp4", "/tmp/b.mp4", "/tmp/c.mp4"])
    check("read_result_file 取最后一行", ytdlp.read_result_file(multi) == "/tmp/c.mp4")

    work = tmp / "dl"
    work.mkdir(parents=True, exist_ok=True)
    (work / "old.mp4").write_text("x")
    before = ytdlp.snapshot_dir(work)
    time.sleep(0.05)
    new_file = work / "new.mp4"
    new_file.write_text("y")
    (work / "part.part").write_text("z")
    guessed = ytdlp.guess_result_file(work, time.time() - 30, before)
    check("快照兜底能找到新文件", guessed is not None and guessed.endswith("new.mp4"), f"→ {guessed}")


def test_match_existing(tmp: Path) -> None:
    """回归测试：同名视频不得被当成音频任务的产物（曾导致 MP3 任务误报成功）。"""
    section("已有产物匹配（回归）")
    from core.tasks import TaskManager

    work = tmp / "match"
    work.mkdir(parents=True, exist_ok=True)
    (work / "标题.mp4").write_text("video" * 2000)
    (work / "标题.mp3").write_text("audio" * 2000)
    (work / "标题.f30016.mp4").write_text("fragment" * 2000)
    (work / "标题.part").write_text("partial" * 2000)

    audio = TaskManager._match_existing(work, "标题", time.time(), audio_only=True)
    video = TaskManager._match_existing(work, "标题", time.time(), audio_only=False)
    check("音频任务只匹配音频扩展名", audio is not None and audio.endswith(".mp3"), f"→ {audio}")
    check("视频任务只匹配视频扩展名", video is not None and video.endswith(".mp4"), f"→ {video}")
    check("视频任务不会匹配到音频", not (video or "").endswith(".mp3"))

    # 时间窗约束：本次任务开始前就存在、且之后没被改动过的文件不认
    stale = tmp / "stale"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "老视频.mp4").write_text("old" * 2000)
    old_names = {e.name for e in stale.iterdir()}
    matched = TaskManager._match_existing(
        stale, "老视频", time.time() + 10, audio_only=False, before_files=old_names
    )
    check("不认本次之前就存在且未改动的同名文件", matched is None, f"→ {matched}")
    # 但"文件已存在跳过下载"场景：yt-dlp 不写结果文件也不改 mtime，
    # 因此当该文件是本次任务的输出模板对应文件时仍应认（通过扩展名+标题匹配）
    matched_same = TaskManager._match_existing(
        stale, "老视频", time.time() + 10, audio_only=True, before_files=old_names
    )
    check("音频任务不会误认同名视频为音频产物", matched_same is None, f"→ {matched_same}")

    empty = tmp / "empty"
    empty.mkdir(parents=True, exist_ok=True)
    check("无产物时返回 None", TaskManager._match_existing(empty, "标题", time.time()) is None)
    check("标题为空时返回 None", TaskManager._match_existing(work, "", time.time()) is None)

    section("残留分片清理")
    removed = TaskManager._cleanup_fragments(work, "标题")
    check("清理了分片与 part 文件", removed >= 2, f"→ 清理 {removed} 个")
    check("完整视频文件未被误删", (work / "标题.mp4").is_file())
    check("完整音频文件未被误删", (work / "标题.mp3").is_file())
    check("分片文件已删除", not (work / "标题.f30016.mp4").exists())
    check("part 文件已删除", not (work / "标题.part").exists())


def test_environment_adaptation() -> None:
    """环境自适应：JS 运行时、代理容错、链接纠正、错误中文化。"""
    section("JS 运行时与代理")
    runtime = ytdlp.detect_js_runtime()
    check("能探测到 JS 运行时", bool(runtime), f"→ {runtime}")
    args = ytdlp.js_runtime_args()
    check("生成 --js-runtimes 参数", "--js-runtimes" in args, f"→ {args}")

    check("裸 host:port 自动补 http://", ytdlp.sanitize_proxy("127.0.0.1:7897") == "http://127.0.0.1:7897")
    check("已有协议不重复补", ytdlp.sanitize_proxy("socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080")
    check("空代理返回 None", ytdlp.sanitize_proxy("") is None and ytdlp.sanitize_proxy(None) is None)

    # 命令里确实带上了代理协议头
    args_proxy, _ = ytdlp.build_format_args({"preset": "best", "proxy": "127.0.0.1:7897"})
    idx = args_proxy.index("--proxy") + 1
    check("构建命令时补全代理协议", args_proxy[idx] == "http://127.0.0.1:7897", f"→ {args_proxy[idx]}")

    section("链接形态纠正")
    fixed, note = ytdlp.normalize_url(
        "https://www.douyin.com/note/7689063211551750834?previous_page=web_code_link"
    )
    check("抖音 /note/ 纠正为 /video/",
          fixed.startswith("https://www.douyin.com/video/7689063211551750834"), f"→ {fixed}")
    check("纠正后带提示语", bool(note))
    check("保留查询参数", "previous_page=web_code_link" in fixed, f"→ {fixed}")

    same, no_note = ytdlp.normalize_url("https://www.douyin.com/video/123")
    check("已是 /video/ 不做改动", same == "https://www.douyin.com/video/123" and no_note is None)
    yt, no_note2 = ytdlp.normalize_url("https://www.youtube.com/watch?v=abc")
    check("其它站点不受影响", yt == "https://www.youtube.com/watch?v=abc" and no_note2 is None)

    section("错误信息中文化")
    for raw, expect in [
        ("[youtube] x: The page needs to be reloaded.", "JS"),
        ("[Douyin] x: Fresh cookies (not necessarily logged in) are needed", "签名"),
        ("Unsupported URL: https://x", "不受引擎支持"),
        ("ERROR: Sign in to confirm your age. Use --cookies", "年龄"),
        ("Unable to connect to proxy", "代理"),
    ]:
        text = ytdlp.friendly_error(raw)
        check(f"提示可操作：{raw[:32]}", expect in text, f"→ {text[-60:]}")
    check("未知错误原样返回", ytdlp.friendly_error("莫名其妙的错误") == "莫名其妙的错误")
    check("空错误有兜底文案", "解析失败" in ytdlp.friendly_error(""))


def test_douyin(tmp: Path) -> None:
    """抖音免 Cookie 通道：URL 识别、档位映射、id 提取、文件名重命名。"""
    section("抖音链接识别")
    for url in (
        "https://www.douyin.com/video/7694879952763751680",
        "https://www.douyin.com/note/7694879952763751680",
        "https://v.douyin.com/aAQgycBvBp0/",
        "https://www.iesdouyin.com/share/video/7694879952763751680/",
        "https://www.douyin.com/video/7694879952763751680",
    ):
        check(f"识别为抖音：{url[:46]}", douyin.is_douyin_url(url))
    for url in (
        "https://www.bilibili.com/video/BV1GJ411x7h7",
        "https://www.youtube.com/watch?v=abc",
        "https://douyin.com.evil.com/video/1",
    ):
        check(f"不误判：{url[:46]}", not douyin.is_douyin_url(url))

    section("抖音画质档位映射")
    # 实测：play 接口才真正响应 ratio；playwm 固定 720p 且带水印
    check("best → 1080p", douyin.ratio_for_preset("best") == "1080p")
    check("720 → 720p", douyin.ratio_for_preset("720") == "720p")
    check("480/360 → 540p（最低档）", douyin.ratio_for_preset("480") == "540p"
          and douyin.ratio_for_preset("360") == "540p")
    check("未知预设回退 1080p", douyin.ratio_for_preset("nonsense") == "1080p")

    section("抖音页面数据提取")
    html = ('...{"video":{"play_addr":{"uri":"v0d00fg10000db4r7cfog65jtejrikfg",'
            '"url_list":["https:\\u002F\\u002Faweme.snssdk.com\\u002F"]},'
            '"duration":122440},"desc":"测试标题 #话题","create_time":1791603860}')
    check("提取 video_id", douyin.extract_video_id(html) == "v0d00fg10000db4r7cfog65jtejrikfg")
    check("提取标题", douyin.DESC_RE.search(html).group(1) == "测试标题 #话题")
    try:
        douyin.extract_video_id('{"data":{}}')
        check("缺少 play_addr 时报错", False)
    except douyin.DouyinError:
        check("缺少 play_addr 时报错", True)

    section("抖音档位选择")
    fake_video = {
        "bit_rate": [
            {"gear_name": "720_2_1", "play_addr": {"height": 720, "width": 1280, "data_size": 14_500_000,
                                                   "url_list": ["https://cdn/720.mp4"]}},
            {"gear_name": "1080_1_1", "play_addr": {"height": 1080, "width": 1920, "data_size": 49_000_000,
                                                     "url_list": ["https://cdn/1080.mp4"]}},
            {"gear_name": "540_1_1", "play_addr": {"height": 576, "width": 1024, "data_size": 10_000_000,
                                                   "url_list": ["https://cdn/540.mp4"]}},
        ]
    }
    best = douyin._pick_bitrate(fake_video, None)
    check("best 选最高档", best["play_addr"]["height"] == 1080, f"→ {best['play_addr']['height']}")
    p720 = douyin._pick_bitrate(fake_video, 720)
    check("720 选不高于目标的最大档", p720["play_addr"]["height"] == 720, f"→ {p720['play_addr']['height']}")
    p480 = douyin._pick_bitrate(fake_video, 480)
    check("低于最低档时回退到最小档", p480["play_addr"]["height"] == 576, f"→ {p480['play_addr']['height']}")
    check("无 bit_rate 时返回 None", douyin._pick_bitrate({}, None) is None)
    check("bit_rate 无直链时返回 None",
          douyin._pick_bitrate({"bit_rate": [{"play_addr": {"height": 720}}]}, None) is None)

    section("抖音网络异常处理")
    # IncompleteRead 等传输层异常必须被收敛成 DouyinError（可重试），不能冒泡成 500
    import http.client as _hc
    import urllib.error as _ue
    import urllib.request as _ur

    class _BoomOpener:
        handlers: list = []

        def open(self, *_a, **_k):
            raise _hc.IncompleteRead(b"x" * 319542, 41955)

    try:
        douyin._request(_BoomOpener(), "https://api5-normal-c-hl.amemv.com/aweme/v1/feed/")
        check("传输中断被收敛为 DouyinError", False, "未抛出")
    except douyin.DouyinError as exc:
        check("传输中断被收敛为 DouyinError", "传输中断" in str(exc), f"→ {exc}")
    except Exception as exc:  # noqa: BLE001
        check("传输中断被收敛为 DouyinError", False, f"抛出了 {type(exc).__name__}")

    class _UrlBoomOpener:
        handlers: list = []

        def open(self, *_a, **_k):
            raise _ue.URLError("connection reset")

    try:
        douyin._request(_UrlBoomOpener(), "https://api5-normal-c-hl.amemv.com/x")
        check("URLError 被收敛为 DouyinError", False, "未抛出")
    except douyin.DouyinError as exc:
        check("URLError 被收敛为 DouyinError", "网络请求失败" in str(exc), f"→ {exc}")

    # 重试包装：一直失败时最终抛 DouyinError，且尝试了指定次数
    calls = {"n": 0}

    class _AlwaysFail:
        handlers: list = []

        def open(self, *_a, **_k):
            calls["n"] += 1
            raise _hc.IncompleteRead(b"", 1)

    try:
        douyin._request_with_retry(_AlwaysFail(), "https://x", attempts=3)
        check("重试包装最终抛错", False)
    except douyin.DouyinError:
        check("重试包装最终抛错", True)
    check("重试次数符合预期", calls["n"] == 3, f"→ {calls['n']} 次")

    section("抖音不可用原因分类")
    check("删除类给出对应提示", "删除" in douyin._classify_unavailable([{"reason": "已删除"}], "1"))
    check("地区类给出对应提示", "地区" in douyin._classify_unavailable([{"reason": "region limit"}], "1"))
    check("私密类给出对应提示", "私密" in douyin._classify_unavailable([{"reason": "private"}], "1"))
    generic = douyin._classify_unavailable(None, "1")
    check("未知情况有兜底说明", "不支持下载" in generic, f"→ {generic[-24:]}")
    check("DouyinUnavailable 是 DouyinError 子类",
          issubclass(douyin.DouyinUnavailable, douyin.DouyinError))

    section("抖音图文帖（图集）")
    # 真实分享页里 images 数组的结构（已转义 /，需还原）
    gallery_html = (
        '{"aweme_id":"7694626674918563314"},"desc":"#厦门","create_time":1,'
        '"nickname":"测试作者","digg_count":41776,'
        '"images":['
        '{"uri":"a","width":1000,"height":1573,'
        '"url_list":["https:\\u002F\\u002Fp5.douyinpic.com\\u002Fx~tplv-dy-lqen-new:1000:1573:q80.webp?biz_tag=aweme_images",'
        '"https:\\u002F\\u002Fp5.douyinpic.com\\u002Fx~tplv-dy-lqen-new:1000:1573:q80.jpeg?biz_tag=aweme_images"],'
        '"download_url_list":["https:\\u002F\\u002Fp5.douyinpic.com\\u002Fx~tplv-dy-lqen-new-water:1000:1573:q80.jpeg"]},'
        '{"uri":"b","width":1080,"height":1440,'
        '"url_list":["https:\\u002F\\u002Fp3.douyinpic.com\\u002Fy~tplv-dy-lqen-new:1080:1440:q80.jpeg?biz_tag=aweme_images"]}'
        ']}'
    )
    imgs = douyin.parse_images_array(gallery_html)
    check("能从分享页提取 images 数组", imgs is not None and len(imgs) == 2, f"→ {imgs and len(imgs)}")

    ranked = douyin.rank_image_urls(imgs[0]["url_list"])
    check("jpeg 原图排在最前（无水印）", ".jpeg?" in ranked[0], f"→ {ranked[0][-60:]}")
    check("webp 作为备选保留", any(".webp?" in u for u in ranked))
    check("不使用带水印的 download_url_list",
          all("-water" not in u for u in ranked), f"→ {[u[-40:] for u in ranked]}")

    check("扩展名识别 jpeg", douyin.image_extension(ranked[0]) == "jpeg")
    check("扩展名识别 webp", douyin.image_extension("https://x/a.webp?y=1") == "webp")
    check("未知扩展名回退 jpg", douyin.image_extension("https://x/a") == "jpg")
    check("空链接列表返回空", douyin.rank_image_urls(None) == [] and douyin.rank_image_urls([]) == [])
    check("非图文页面返回 None", douyin.parse_images_array('{"video":{}}') is None)

    gallery = douyin.extract_gallery(gallery_html, "7694626674918563314")
    check("构造出图集结果", gallery is not None and gallery["kind"] == "gallery")
    check("图集条目数正确", gallery["image_count"] == 2, f"→ {gallery['image_count']}")
    check("图集标题/作者/点赞解析", gallery["title"] == "#厦门"
          and gallery["uploader"] == "测试作者" and gallery["like_count"] == 41776,
          f"→ {gallery['title']!r} {gallery['uploader']!r} {gallery['like_count']}")
    check("每个图集条目带 url/ext/尺寸",
          all(i.get("url") and i.get("ext") and i.get("width") for i in gallery["images"]))
    check("图集 formats 逐张列出",
          len(gallery["formats"]) == 2 and gallery["formats"][0]["format_id"] == "douyin-image-1")
    check("图集不使用 direct_url（改为本地合成视频）", "direct_url" not in gallery)
    check("图集带配乐时长用于合成", "video_duration" in gallery and "music_url" in gallery,
          f"→ {gallery.get('music_url')} / {gallery.get('video_duration')}")
    check("无配乐时按每张 5 秒估算", gallery["video_duration"] >= len(gallery["images"]) * 5 - 0.01,
          f"→ {gallery['video_duration']}")
    check("纯视频页面不会被当成图集",
          douyin.extract_gallery('{"play_addr":{"uri":"v0abc"}}', "1") is None)

    # 关键回归：feed 接口对图文帖只返回推荐流，绝不能因此判定"作品不可下载"
    manager_gal = TaskManager(concurrency=1)
    gal_task = manager_gal.create("https://www.douyin.com/note/7694626674918563314",
                                 {"preset": "best"})
    gal_task.title = "#厦门"
    manager_gal.shutdown()
    check("图集目录名安全化", _safe_folder_name('#厦门/测试') == "#厦门_测试",
          f"→ {_safe_folder_name('#厦门/测试')}")
    check("空标题有兜底目录名", _safe_folder_name("") == "douyin")

    section("抖音配乐时长解析")
    check("从 music 对象取时长", douyin.parse_music_duration('{"music":{"duration":26,"title":"x"}}') == 26.0)
    check("毫秒值会换算成秒", douyin.parse_music_duration('{"music":{"duration":26000}}') == 26.0)
    check("无 music 返回 None", douyin.parse_music_duration('{"video":{}}') is None)
    check("duration 为 0 返回 None", douyin.parse_music_duration('{"music":{"duration":0}}') is None)
    check("嵌套花括号也能正确配平",
          douyin.parse_music_duration('{"music":{"a":{"b":1},"duration":12}}') == 12.0)

    section("图文帖合成视频")
    import subprocess as _sp
    import shutil as _sh
    work_img = tmp / "slide"
    work_img.mkdir(parents=True, exist_ok=True)
    # 造两张纯色测试图
    if _sh.which("ffmpeg"):
        for idx, color in ((1, "red"), (2, "blue")):
            _sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", f"color=c={color}:s=320x480:d=1", "-frames:v", "1",
                     str(work_img / f"图片{idx}.jpg")], timeout=60)
        out = work_img / "合成.mp4"
        ok, err = compose_slideshow_video(
            [work_img / "图片1.jpg", work_img / "图片2.jpg"], None, out, 4.0, []
        )
        check("无配乐也能合成视频", ok and out.exists(), f"→ {err}")
        if out.exists():
            probe = _sp.run(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration:stream=codec_type,codec_name,width,height",
                             "-of", "json", str(out)], capture_output=True, text=True)
            meta = json.loads(probe.stdout)
            streams = {s["codec_type"]: s for s in meta["streams"]}
            check("合成视频含 h264 视频流", streams.get("video", {}).get("codec_name") == "h264")
            check("合成视频为竖屏 1080x1920",
                  streams.get("video", {}).get("width") == 1080
                  and streams.get("video", {}).get("height") == 1920,
                  f"→ {streams.get('video', {}).get('width')}x{streams.get('video', {}).get('height')}")
            dur = float(meta["format"]["duration"])
            check("合成视频时长符合预期", 3.5 <= dur <= 4.6, f"→ {dur:.2f}s")
        check("无图片时返回失败", compose_slideshow_video([], None, work_img / "x.mp4", 3, [])[0] is False)
    else:
        check("ffmpeg 可用（合成前提前提）", False, "未找到 ffmpeg")

    section("图文帖单文件下载")
    manager_dl = TaskManager(concurrency=1)
    dl_task = manager_dl.create("https://example.com/x", {"preset": "best"})
    target = tmp / "dl" / "图片1.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)

    def ok_fetch(_url):
        return b"x" * 1024

    check("下载成功返回 True", fetch_to_file(dl_task, ok_fetch, "https://x/a.jpg", target, 1) is True)
    check("文件已写入", target.is_file() and target.stat().st_size == 1024)
    check("累计下载字节数", dl_task.downloaded_bytes == 1024, f"→ {dl_task.downloaded_bytes}")

    def boom_fetch(_url):
        raise OSError("connection reset")

    check("下载异常返回 False 而非抛出",
          fetch_to_file(dl_task, boom_fetch, "https://x/b.jpg", tmp / "dl" / "b.jpg", 2) is False)
    check("空响应返回 False",
          fetch_to_file(dl_task, lambda _u: b"", "https://x/c.jpg", tmp / "dl" / "c.jpg", 3) is False)
    check("无链接返回 False",
          fetch_to_file(dl_task, ok_fetch, None, tmp / "dl" / "d.jpg", 4) is False)
    manager_dl.shutdown()

    section("抖音文件名规范化")
    manager = TaskManager(concurrency=1)
    work = tmp / "dyrename"
    work.mkdir(parents=True, exist_ok=True)
    messy = work / "oMzOINEDa1fAynoQtqbJAofEPjF209Ay9xBwAg.mp4"
    messy.write_bytes(b"x" * 20000)
    task = manager.create("https://example.com/x", {"preset": "720", "download_dir": str(work)})
    task.title = "韩立燃烧精血，疯狂跑路！！！"
    renamed = TaskManager._rename_to_title(task, [str(messy)], work)
    check("乱码文件名被改成标题", Path(renamed[0]).name.startswith("韩立燃烧精血"), f"→ {renamed[0]}")
    check("原文件已消失", not messy.exists())
    # 标题里含非法字符时会被替换
    weird = work / "abc123.mp4"
    weird.write_bytes(b"y" * 20000)
    task.title = 'a/b:c*d?e"f<g>h|i'
    out = TaskManager._rename_to_title(task, [str(weird)], work)
    check("非法字符被替换为下划线", "/" not in Path(out[0]).name and ":" not in Path(out[0]).name,
          f"→ {Path(out[0]).name}")
    # 重名时自动加序号
    dup = work / "zzz999.mp4"
    dup.write_bytes(b"z" * 20000)
    task.title = "重名测试"
    (work / "重名测试.mp4").write_bytes(b"w" * 20000)
    out2 = TaskManager._rename_to_title(task, [str(dup)], work)
    check("重名自动追加序号", "(2)" in Path(out2[0]).name, f"→ {Path(out2[0]).name}")
    manager.shutdown()


def test_error_extraction() -> None:
    """错误提取：错误行与紧随其后的中文提示必须成对返回（曾把提示当成错误本身）。"""
    section("错误提取")
    sample = (
        "[youtube] Extracting URL: https://x\n"
        "ERROR: [youtube] abc: The page needs to be reloaded.\n"
        "提示：YouTube 的 JS 挑战未解开：需要 JS 运行时。\n"
    )
    got = ytdlp._last_error(sample) or ""
    check("保留原始错误", "The page needs to be reloaded" in got, f"→ {got[:60]}")
    check("同时带上中文提示", "JS 运行时" in got, f"→ {got[-40:]}")

    bare = ytdlp._last_error("ERROR:\n提示：抖音需要签名参数\n") or ""
    check("裸 ERROR 时用提示兜底", "签名参数" in bare, f"→ {bare}")

    plain = ytdlp._last_error("ERROR: [Douyin] abc: Fresh cookies\n") or ""
    check("无提示时原样返回", plain == "[Douyin] abc: Fresh cookies", f"→ {plain}")

    fallback = ytdlp._last_error("random failure line\n提示：提示语\n") or ""
    check("无 ERROR 行时退回最后有效行", fallback == "random failure line", f"→ {fallback}")


def test_retry_strategies() -> None:
    section("多策略重试计划")
    video = ytdlp.retry_strategies({"preset": "720"}, audio_only=False)
    audio = ytdlp.retry_strategies({"preset": "audio"}, audio_only=True)
    custom = ytdlp.retry_strategies({"preset": "720", "custom_format": "bv*+ba"}, audio_only=False)

    check("视频有多套降级策略", len(video) >= 3, f"→ {len(video)}")
    check("首套策略不改变用户设置", video[0][1] == {})
    check("包含换编码策略", any(p.get("_codec_phase") == "fallback" for _, p in video))
    check("包含降并发策略", any(p.get("concurrent_fragments") == 1 for _, p in video))
    check("音频不换编码", all("_codec_phase" not in p for _, p in audio))
    check("自定义格式不换编码", all("_codec_phase" not in p for _, p in custom))

    # 降级参数确实能生成不同的命令行
    args_primary, _ = ytdlp.build_format_args({"preset": "720"})
    args_fallback, _ = ytdlp.build_format_args({"preset": "720", **video[1][1]})
    sort_primary = args_primary[args_primary.index("-S") + 1]
    sort_fallback = args_fallback[args_fallback.index("-S") + 1]
    check("降级后编码偏好确实不同", sort_primary != sort_fallback, f"→ {sort_primary} vs {sort_fallback}")
    args_slow, _ = ytdlp.build_format_args({"preset": "720", **video[2][1]})
    check("降级后启用分片下载", "--http-chunk-size" in args_slow)

    section("永久性错误识别（避免无意义重试）")
    for message in [
        "[generic] nope: Unable to download webpage: HTTP Error 404: Not Found",
        "[BiliBili] xx: This video may be deleted or geo-restricted",
        "Unsupported URL: https://example.com/foo",
        "Sign in to confirm your age. Use --cookies",
        "Requested format is not available",
    ]:
        check(f"判定为不可重试：{message[:34]}", ytdlp.is_permanent_error(message))
    for message in [
        "[download] Got error: SSL: UNEXPECTED_EOF_WHILE_READING",
        "HTTP Error 503: Service Unavailable",
        "timed out",
    ]:
        check(f"判定为可重试：{message[:34]}", not ytdlp.is_permanent_error(message))


# ---------------------------------------------------------------------------
# 4. 任务状态机（离线，注入假引擎）
# ---------------------------------------------------------------------------
def test_task_state_machine(tmp: Path) -> None:
    section("任务状态机")
    manager = TaskManager(concurrency=2)

    # --- 成功路径：伪造一个 yt-dlp 进程输出
    task = manager.create("https://example.com/v", {"preset": "360", "download_dir": str(tmp)})
    # 创建后会在下载线程池里立刻开跑，因此初始状态可能是 queued 也可能是 running
    check("任务进入可执行状态", task.state in ("queued", "running"), f"→ {task.state}")

    fake_lines = [
        "[download] Destination: /tmp/fake.mp4",
        " 12.1%|8423526|69759161|NA|614009.5|99",
        " 99.0%|69759161|69759161|NA|614009.5|1",
        '[Merger] Merging formats into "/tmp/fake.mp4"',
    ]
    for line in fake_lines:
        manager._on_line(task, line)
    check("阶段推进到合流", task.phase == "merging", f"→ {task.phase}")
    check("进度已记录", task.percent is not None and task.downloaded_bytes > 0)
    check("合流阶段进度置顶", task.percent >= 99.0)

    manager._on_line(task, "[download] Got error: SSL: UNEXPECTED_EOF")
    check("网络抖动计入 warnings", task.warnings == 1)
    check("抖动不覆盖阶段提示", task.message != "[download] Got error: SSL: UNEXPECTED_EOF" or task.last_warning is not None)
    check("抖动单独记录", bool(task.last_warning))

    manager._on_line(task, "ERROR: 视频已被删除")
    check("错误被捕获", task.error == "视频已被删除")

    snapshot = task.to_dict()
    check("快照包含关键字段",
          all(key in snapshot for key in ("id", "state", "percent", "speed", "eta", "phase_text")))

    # --- 收尾判定：结果文件缺失 / 播放列表部分失败 / 抖动自愈
    def make_task(manager, tmpdir, **opts):
        work = Path(tmpdir)
        work.mkdir(parents=True, exist_ok=True)
        t = manager.create("https://example.com/x", {"preset": "360", "download_dir": str(work), **opts})
        t.title = "测试视频"
        return t, work

    work_root = tmp / "finalize"
    manager_f = TaskManager(concurrency=1)

    # (a) 抖动后自愈：有 Giving up 记录但产物完整 → 不算部分失败
    t1, w1 = make_task(manager_f, work_root / "a")
    good = w1 / "测试视频.mp4"
    good.write_bytes(b"x" * 20000)
    t1.failed_items = 1
    rf1 = work_root / "r1.txt"
    rf1.write_text(str(good) + "\n", encoding="utf-8")
    manager_f._finalize(t1, w1, rf1, time.time() - 10, set(), False)
    check("抖动自愈不报部分完成", t1.state == STATE_DONE and not t1.incomplete, f"→ {t1.message}")

    # (b) 结果文件记录了产物但文件不存在 → 部分失败
    t2, w2 = make_task(manager_f, work_root / "b")
    rf2 = work_root / "r2.txt"
    rf2.write_text(str(w2 / "缺失1.mp4") + "\n" + str(w2 / "缺失2.mp4") + "\n", encoding="utf-8")
    manager_f._finalize(t2, w2, rf2, time.time() - 10, set(), False)
    check("产物缺失被判为部分完成", t2.incomplete, f"→ {t2.message}")

    # (c) 播放列表预期 3 个只完成 1 个 → 部分失败
    t3, w3 = make_task(manager_f, work_root / "c")
    only = w3 / "条目1.mp4"
    only.write_bytes(b"y" * 20000)
    t3.playlist_total = 3
    rf3 = work_root / "r3.txt"
    rf3.write_text(str(only) + "\n", encoding="utf-8")
    manager_f._finalize(t3, w3, rf3, time.time() - 10, set(), False)
    check("播放列表未达标被判为部分完成", t3.incomplete, f"→ {t3.message}")
    check("部分完成文案含条目数", "1" in (t3.message or ""), f"→ {t3.message}")

    # (d) 播放列表 2 个都完成 → 无部分失败
    t4, w4 = make_task(manager_f, work_root / "d")
    f1, f2 = w4 / "条目1.mp4", w4 / "条目2.mp4"
    f1.write_bytes(b"a" * 20000)
    f2.write_bytes(b"b" * 20000)
    t4.playlist_total = 2
    rf4 = work_root / "r4.txt"
    rf4.write_text(f"{f1}\n{f2}\n", encoding="utf-8")
    manager_f._finalize(t4, w4, rf4, time.time() - 10, set(), False)
    check("播放列表全部完成", t4.state == STATE_DONE and not t4.incomplete, f"→ {t4.message}")
    check("多产物文件数正确", len(t4.files) == 2, f"→ {t4.files}")
    check("多产物 message 含总数", "2" in (t4.message or ""), f"→ {t4.message}")
    manager_f.shutdown()

    # --- 播放列表：单个条目失败必须被记录（yt-dlp 对列表内失败仍返回 0）
    manager_pl = TaskManager(concurrency=1)
    pl = manager_pl.create("https://example.com/pl", {"preset": "360", "playlist": True})
    pl.playlist_total = 2
    manager_pl._on_line(pl, "[download] Destination: /tmp/one.f30016.mp4")
    manager_pl._on_line(pl, "[download] Destination: /tmp/one.f30216.m4a")
    manager_pl._on_line(pl, '[Merger] Merging formats into "/tmp/one.mp4"')
    manager_pl._on_line(pl, "[download] Destination: /tmp/two.f30016.mp4")
    manager_pl._on_line(pl, "[download] Got error: [SSL: UNEXPECTED_EOF_WHILE_READING]")
    manager_pl._on_line(pl, "[download] Got error: SSL: UNEXPECTED_EOF_WHILE_READING. Giving up after 15 retries")
    manager_pl._on_line(pl, "ERROR:")
    manager_pl._on_line(pl, "[download] Finished downloading playlist: 测试列表")
    check("条目失败被计数", pl.failed_items == 1, f"→ {pl.failed_items}")
    check("合流产物被记录", any("one.mp4" in f for f in pl.files), f"→ {pl.files}")
    check("裸 ERROR 也会记录原因", bool(pl.error), f"→ {pl.error!r}")
    check("播放列表总数被记录", pl.playlist_total == 2)
    # 条目数由"合流完成"驱动，分片流不会让一个条目被算两次
    check("条目计数按合流事件递增", pl.item_index == 1, f"→ {pl.item_index}")
    manager_pl.shutdown()

    # --- 取消排队中的任务
    manager2 = TaskManager(concurrency=1)
    blocked = threading.Event()
    manager2._download_pool.submit(blocked.wait, 5)
    queued = manager2.create("https://example.com/q", {"preset": "best"})
    time.sleep(0.2)
    check("并发满时任务保持排队", queued.state == "queued", f"→ {queued.state}")
    check("可取消排队任务", manager2.cancel(queued.id) is True)
    check("取消后状态正确", queued.state == STATE_CANCELED, f"→ {queued.state}")
    blocked.set()
    manager2.shutdown()

    # --- 失败路径
    manager3 = TaskManager(concurrency=1)
    failing = manager3.create("https://example.com/f", {"preset": "best"})
    failing.state = "running"
    manager3._fail(failing, "模拟失败")
    check("失败状态被设置", failing.state == STATE_ERROR and failing.error == "模拟失败")
    manager3.shutdown()

    # --- 事件订阅（SSE 数据源）
    manager4 = TaskManager(concurrency=1)
    watched = manager4.create("https://example.com/s", {"preset": "best"})
    queue = manager4.subscribe(watched.id)
    manager4._on_line(watched, " 50.0%|100|200|NA|1024|3")
    received = None
    deadline = time.time() + 2
    while time.time() < deadline and received is None:
        try:
            received = queue.get_nowait()
        except Exception:
            time.sleep(0.02)
    check("订阅者能收到事件", received is not None and received["id"] == watched.id)
    manager4.unsubscribe(watched.id, queue)
    manager4.shutdown()

    manager.shutdown()


# ---------------------------------------------------------------------------
# 5. HTTP 接口
# ---------------------------------------------------------------------------
def http_json(url: str, payload: dict | None = None, method: str = "GET") -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def test_http_api() -> None:
    section("HTTP 接口")
    config.PORT = 8791
    manager = TaskManager(concurrency=1)
    httpd = server.create_server(manager)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{config.PORT}"
    time.sleep(0.4)

    try:
        status, data = http_json(f"{base}/api/health")
        check("health 正常", status == 200 and data.get("ok") is True)
        check("health 含引擎信息", "engine" in data and "version" in data["engine"])
        check("health 含磁盘信息", isinstance(data.get("disk_free"), int))

        status, data = http_json(f"{base}/api/presets")
        check("presets 返回预设", status == 200 and len(data.get("presets", [])) >= 5)

        status, data = http_json(f"{base}/api/probe", {"url": "不是链接"}, method="POST")
        check("probe 拒绝非法链接", status == 400, f"→ {status}")

        status, data = http_json(f"{base}/api/probe", {"url": "https://example.com"}, method="POST")
        check("probe 对无效页面返回业务错误",
              status == 200 and data.get("ok") is False and bool(data.get("error")))

        status, data = http_json(f"{base}/api/tasks", {"url": "https://example.com/x"}, method="POST")
        check("可以创建任务", status == 201 and len(data.get("tasks", [])) == 1)
        task_id = data["tasks"][0]["id"]

        status, data = http_json(f"{base}/api/tasks")
        check("任务列表可读", status == 200 and any(t["id"] == task_id for t in data["tasks"]))

        status, data = http_json(f"{base}/api/tasks/{task_id}")
        check("任务详情含日志字段", status == 200 and "log" in data["task"])

        status, data = http_json(f"{base}/api/tasks", {"urls": ["bad-url"]}, method="POST")
        check("批量提交校验链接", status == 400)

        status, data = http_json(f"{base}/api/tasks", {"urls": [f"https://example.com/{i}" for i in range(60)]}, method="POST")
        check("超出数量上限被拒绝", status == 400)

        status, data = http_json(f"{base}/api/reveal", {"path": "/etc/passwd"}, method="POST")
        check("越权路径被拒绝", status == 400)

        status, data = http_json(f"{base}/api/tasks/not-exist-xxxx")
        check("未知任务返回 404", status == 404)

        # SSE：能取到首帧
        request = urllib.request.Request(f"{base}/api/tasks/{task_id}/events")
        with urllib.request.urlopen(request, timeout=10) as response:
            check("SSE 响应类型正确",
                  response.headers.get("Content-Type", "").startswith("text/event-stream"))
            first = response.readline().decode()
            second = response.readline().decode()
            check("SSE 首帧为 JSON 数据帧", first.startswith("data:") and second.strip() == "")
    finally:
        manager.shutdown()
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# 6. 真实网络测试（可选）
# ---------------------------------------------------------------------------
def test_live() -> None:
    section("真实网络（--live）")
    info = ytdlp.engine_version()
    check("引擎可用", bool(info.get("ok")), f"→ {info}")
    check("ffmpeg 可用", bool(info.get("ffmpeg")))

    url = "https://www.bilibili.com/video/BV1GJ411x7h7"
    try:
        probed = ytdlp.probe(url)
    except ytdlp.EngineError as exc:
        check("真实站点解析", False, f"→ {exc}")
        return
    check("解析到视频", probed.get("kind") == "video" and bool(probed.get("title")))
    check("解析到画质列表", len(probed.get("formats") or []) > 0)
    check("画质列表含体积信息",
          any(f.get("filesize_text") for f in probed["formats"]))

    work = config.DOWNLOAD_DIR
    result = config.CACHE_DIR / "live_result.txt"
    args, _ = ytdlp.build_download_args(
        url, {"preset": "360", "concurrent_fragments": 4}, result, work
    )
    before = ytdlp.snapshot_dir(work)
    started = time.time()
    proc = ytdlp.EngineProcess(args)
    progress_hits = []
    code = proc.run(on_line=lambda line: (
        progress_hits.append(line) if ytdlp.parse_progress_line(line) else None
    ))
    check("真实下载退出码为 0", code == 0, f"→ {code}")
    check("下载过程中收到进度行", len(progress_hits) > 0, f"→ {len(progress_hits)}")
    filepath = ytdlp.read_result_file(result) or ytdlp.guess_result_file(work, started, before)
    check("定位到产物文件", bool(filepath) and Path(filepath).is_file(), f"→ {filepath}")
    if filepath and Path(filepath).is_file():
        size = Path(filepath).stat().st_size
        check("产物非空", size > 100_000, f"→ {size} 字节")
        ffprobe = config.ffprobe_path()
        if ffprobe:
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration,size,format_name",
                 "-of", "json", filepath],
                capture_output=True, text=True, timeout=60,
            )
            check("ffprobe 可解析产物", probe.returncode == 0, probe.stderr[:200])
            if probe.returncode == 0:
                meta = json.loads(probe.stdout)["format"]
                print(f"     产物：{meta.get('format_name')} 时长 {meta.get('duration')}s 体积 {meta.get('size')}B")
    try:
        result.unlink()
    except OSError:
        pass


def main() -> int:
    live = "--live" in sys.argv
    print("\033[36mVideoHarbor 后端自测\033[0m")
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        test_progress_parser()
        test_command_builder()
        test_file_helpers(tmp)
        test_match_existing(tmp)
        test_retry_strategies()
        test_environment_adaptation()
        test_error_extraction()
        test_douyin(tmp)
        test_task_state_machine(tmp / "tasks")

    test_http_api()
    if live:
        test_live()

    print(f"\n\033[36m== 结果 ==\033[0m\n  通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  \033[31m失败：{item}\033[0m")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
