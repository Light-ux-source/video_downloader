"""抖音（Douyin）免 Cookie 提取器。

背景
----
yt-dlp 的抖音提取器走 `www.douyin.com/aweme/v1/web/aweme/detail/` 这类 Web 业务接口，
这些接口现在要求 `a_bogus` / msToken 请求签名（实测返回 `status_code: 11110` /
`encrypt_data_miss`，或直接 403）。yt-dlp 源码中此处仍是未实现的 TODO：

    # TODO: Run verification challenge code to generate signature cookies

因此直接交给 yt-dlp 必然报 `Fresh cookies (not necessarily logged in) are needed`。

本项目采用的通路（**无需用户登录、无需用户提供 Cookie、无需实现签名算法**）
------------------------------------------------------------------
主路径：**移动端 App Feed 接口**

    https://api5-normal-c-hl.amemv.com/aweme/v1/feed/?aweme_id=<数字id>

- 使用 App 形态的 UA（`com.ss.android.ugc.aweme/...`），普通浏览器 UA 会被拒
- 实测**免签名、免 Cookie**，`status_code: 0`，直接返回该视频完整详情
- 返回体里的 `video.bit_rate[]` 给出每个档位的**精确分辨率与 data_size**，
  并带可直接下载的 `url_list`（比播放接口更直接，且能挑档位）
- 关键判据：**视频不可用时 `aweme_list` 为空**——据此可精确区分
  "视频本身不支持下载"与"接口失效"，避免给用户含糊报错

兜底路径：SSR 分享页 + App 播放直链

    ① 访问 douyin.com / iesdouyin.com 首页拿游客 Cookie（ttwid）
       —— 只是设备标识，不是登录态
    ② 带游客 Cookie 请求 /share/video/<数字id>/ → 页面内联渲染 play_addr.uri
       （不带游客 Cookie 只会拿到风控壳页，这是最容易踩的坑）
    ③ aweme.snssdk.com/aweme/v1/play/?video_id=<uri>&ratio=<档位> → 302 到 CDN 直链

实测要点
-------
- **必须用 `play` 接口，不要用 `playwm`**：抓同一时间点画面做像素对比确认
  `play` = 1920x1080 无水印；`playwm` = 固定 1280x720 且左上角带抖音号水印。
- CDN 直链有效期约 1–2 小时，因此解析与下载必须在同一流程内完成。
- 域名职责：`aweme.snssdk.com` / `api5-normal-c-hl.amemv.com` 是 App 端域名（多数免签名）；
  `www.douyin.com/aweme/v1/web/**` 是 Web 端接口（需要签名，不要用）。

边界
----
- 仅支持公开视频；私密 / 仅好友可见 / 已删除 / 地区限制会返回空列表，程序会明确告知。
- 图文笔记（图集）不是视频流，会明确提示不支持。
- 直播与直播回放走 live 接口，不适用。
- 若抖音给 App 端接口也加签名校验，主路径失效时会自动降级到兜底路径；
  两条都失败才报错。
"""
from __future__ import annotations

import http.client
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------- 常量

# 游客 Cookie 获取入口（不登录，仅拿设备标识）
GUEST_COOKIE_URLS = (
    "https://www.douyin.com/",
    "https://www.iesdouyin.com/",
)

# 主路径：App 端 Feed 接口（免签名）
MOBILE_FEED_ENDPOINTS = (
    "https://api5-normal-c-hl.amemv.com/aweme/v1/feed/",
    "https://aweme.snssdk.com/aweme/v1/feed/",
)
UA_APP = (
    "com.ss.android.ugc.aweme/290101 (Linux; U; Android 10; zh_CN; Pixel 4; "
    "Build/QQ3A.200805.001; Cronet/TTNetVersion:5f9037be 2023-01-13 "
    "QuicVersion:4668bb42 2022-11-21)"
)

# 兜底路径
SHARE_PAGE_TEMPLATE = "https://www.iesdouyin.com/share/video/{video_id}/"
PLAY_API_TEMPLATE = (
    "https://aweme.snssdk.com/aweme/v1/play/?video_id={video_id}&ratio={ratio}&line=0"
)
# playwm 带水印且固定 720p，仅作最后兜底
PLAYWM_API_TEMPLATE = (
    "https://aweme.snssdk.com/aweme/v1/playwm/?video_id={video_id}&ratio={ratio}&line=0"
)
CDN_REFERER = "https://www.douyin.com/"

UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 画质预设 → 播放接口 ratio（兜底路径用）
RATIO_BY_PRESET: dict[str, str] = {
    "best": "1080p",
    "1080": "1080p",
    "720": "720p",
    "480": "540p",
    "360": "540p",
}
# 画质预设 → 期望高度（主路径从 bit_rate 里挑档位；None 表示最高）
HEIGHT_BY_PRESET: dict[str, int | None] = {
    "best": None,
    "1080": 1080,
    "720": 720,
    "480": 576,
    "360": 576,
}

DOUYIN_HOST_RE = re.compile(
    r"^https?://(?:"
    r"v\.douyin\.com|"
    r"www\.douyin\.com|douyin\.com|"
    r"www\.iesdouyin\.com|iesdouyin\.com|"
    r"api\.douyin\.com|"
    r"www\.amemv\.com"
    r")/",
    re.IGNORECASE,
)
NUMERIC_ID_RE = re.compile(
    r"/(?:video|note|slides|share/video|share/note|share/slides)/(\d{6,})"
)
VIDEO_URI_RE = re.compile(r'"play_addr":\{"uri":"(v0[0-9a-zA-Z]+)"')
DESC_RE = re.compile(r'"desc":"(.*?)","create_time"')
AUTHOR_RE = re.compile(r'"nickname":"(.*?)"')
LIKES_RE = re.compile(r'"digg_count":(\d+)')
# 配乐直链（实测形如 https://lf26-music-east.douyinstatic.com/obj/ies-music-hj/<id>.mp3）
MUSIC_URL_RE = re.compile(r'https://[a-z0-9-]*music[a-z0-9-]*\.douyinstatic\.com/[^"\s\\]+\.mp3')


class DouyinError(RuntimeError):
    """抖音提取失败（消息面向用户，可直接展示）。"""


class DouyinUnavailable(DouyinError):
    """视频本身不可下载（已删除 / 地区限制 / 私密 / 仅部分可见）。"""


# ---------------------------------------------------------------- URL 判定


def is_douyin_url(url: str) -> bool:
    return bool(DOUYIN_HOST_RE.match((url or "").strip()))


def ratio_for_preset(preset: str | None) -> str:
    return RATIO_BY_PRESET.get(str(preset or "best"), "1080p")


def height_for_preset(preset: str | None) -> int | None:
    return HEIGHT_BY_PRESET.get(str(preset or "best"))


# ---------------------------------------------------------------- HTTP 基础


def _build_opener(
    proxy: str | None, jar: http.cookiejar.CookieJar
) -> urllib.request.OpenerDirector:
    handlers: list[Any] = [urllib.request.HTTPCookieProcessor(jar)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随跳转，便于读取 302 的 Location。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    ua: str = UA_IPHONE,
    follow: bool = True,
    referer: str | None = None,
    timeout: int = 25,
) -> tuple[int, bytes, dict[str, str]]:
    if not follow:
        opener = urllib.request.build_opener(
            *[h for h in opener.handlers if not isinstance(h, urllib.request.HTTPRedirectHandler)],
            _NoRedirect(),
        )
    headers = {"User-Agent": ua, "Accept-Language": "zh-CN,zh;q=0.9"}
    if "aweme" in url or "amemv" in url:
        headers["Accept"] = "application/json"
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return exc.code, body, dict(exc.headers or {})
    except urllib.error.URLError as exc:
        raise DouyinError(f"网络请求失败：{exc.reason}") from exc
    except http.client.HTTPException as exc:
        # 例如 IncompleteRead：响应体被中途截断（代理/网络抖动）
        raise DouyinError(f"网络传输中断：{exc}") from exc
    except OSError as exc:
        raise DouyinError(f"网络请求失败：{exc}") from exc


def _request_with_retry(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    attempts: int = 3,
    **kwargs: Any,
) -> tuple[int, bytes, dict[str, str]]:
    """带重试的请求：网络抖动（截断/超时）在真实环境里很常见。"""
    last: DouyinError | None = None
    for attempt in range(attempts):
        try:
            return _request(opener, url, **kwargs)
        except DouyinError as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(0.5 * (attempt + 1))
    raise last or DouyinError("网络请求失败")


def warm_guest_cookies(opener: urllib.request.OpenerDirector) -> None:
    """访问首页获取游客 Cookie（失败不致命）。"""
    for url in GUEST_COOKIE_URLS:
        try:
            _request(opener, url, timeout=15)
        except DouyinError:
            continue


# ---------------------------------------------------------------- 数字 id


def resolve_numeric_id(opener: urllib.request.OpenerDirector, url: str) -> str:
    """从抖音链接中取出数字作品 id（短链需要跟随 302）。"""
    match = NUMERIC_ID_RE.search(url)
    if match:
        return match.group(1)

    if "v.douyin.com" in url:
        _, _, headers = _request(opener, url, follow=False, timeout=20)
        location = headers.get("Location") or ""
        if location:
            match = NUMERIC_ID_RE.search(location)
            if match:
                return match.group(1)
            raise DouyinError(f"短链跳转结果无法识别：{location[:120]}")
        _, body, _ = _request(opener, url, timeout=25)
        match = NUMERIC_ID_RE.search(body.decode("utf-8", "replace"))
        if match:
            return match.group(1)
        raise DouyinError("短链未能跳转到视频页；请改用完整链接（含 /video/<id>）")

    if "/collection/" in url or "/mix/" in url:
        raise DouyinError("这是合集链接。请打开合集里的单个视频，用那个视频链接下载")
    raise DouyinError("未能从链接中识别作品 id；请使用 /video/<id> 或 /note/<id> 形式的链接")


# ---------------------------------------------------------------- 主路径：Feed


def _feed_items(
    opener: urllib.request.OpenerDirector, numeric_id: str
) -> list[dict[str, Any]]:
    """调 App 端 Feed 接口，返回 aweme_list（接口不可用则抛 DouyinError）。"""
    errors: list[str] = []
    for endpoint in MOBILE_FEED_ENDPOINTS:
        url = f"{endpoint}?aweme_id={numeric_id}&count=10"
        status, body = 0, b""
        # 网络抖动（截断/超时）很常见，同一接口最多重试 3 次再换域名
        for attempt in range(3):
            try:
                status, body, _ = _request(
                    opener, url, ua=UA_APP, referer=CDN_REFERER, timeout=25
                )
                break
            except DouyinError as exc:
                if attempt == 2:
                    errors.append(str(exc))
                else:
                    time.sleep(0.5 * (attempt + 1))
        if status != 200:
            if status:
                errors.append(f"HTTP {status}")
            continue
        text = body.decode("utf-8", "replace")
        if not text.strip():
            errors.append("空响应")
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            errors.append("响应非 JSON")
            continue
        items = data.get("aweme_list") or []
        if items:
            return items
        # 接口通但列表为空 → 交给上层判定"视频不可用"
        return []
    raise DouyinError("App 端 Feed 接口不可用（" + "；".join(errors[:2]) + "）")


def _pick_bitrate(video: dict[str, Any], want_height: int | None) -> dict[str, Any] | None:
    """从 bit_rate 数组里挑最合适的档位（带精确分辨率与直连 url_list）。"""
    rates = [
        r for r in (video.get("bit_rate") or [])
        if (r.get("play_addr") or {}).get("url_list")
    ]
    if not rates:
        return None

    def height_of(rate: dict[str, Any]) -> int:
        return int((rate.get("play_addr") or {}).get("height") or 0)

    if want_height:
        fits = [r for r in rates if 0 < height_of(r) <= want_height]
        if fits:
            return max(fits, key=height_of)
        # 全部档位都高于目标（例如只想要 360p 但最低就是 576p）→ 取最小档，
        # 而不是退回最高档，避免"选省流量却下了最大文件"。
        return min(rates, key=height_of)
    return max(rates, key=height_of)


def _classify_unavailable(filter_list: Any, numeric_id: str) -> str:
    """把"列表为空"翻译成用户能理解的原因。"""
    text = json.dumps(filter_list or [], ensure_ascii=False).lower()
    if "删除" in text or "delete" in text or "not exist" in text:
        return "该视频已被删除或不存在"
    if "地区" in text or "region" in text or "country" in text:
        return "该视频在当前网络所在地区不可播放（地区限制）"
    if "私密" in text or "private" in text or "friend" in text:
        return "该视频为私密内容或仅好友可见，无法下载"
    return (
        "抖音未返回该视频数据：可能已被删除、设为私密、仅部分可见或存在地区限制。"
        "这类视频本身不支持下载。"
    )


def _first_url(container: Any) -> str | None:
    if not isinstance(container, dict):
        return None
    urls = container.get("url_list") or []
    return urls[0] if urls else None


def _human(num: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(num)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    return f"{value:.1f}{units[idx]}" if idx else f"{int(value)}B"


def _extract_via_feed(
    opener: urllib.request.OpenerDirector, numeric_id: str, preset: str | None
) -> dict[str, Any] | None:
    """主路径。返回 None 表示应降级到兜底路径。"""
    items = _feed_items(opener, numeric_id)

    target = None
    for item in items:
        if str(item.get("aweme_id")) == str(numeric_id):
            target = item
            break
    if target is None:
        if not items:
            raise DouyinUnavailable(_classify_unavailable(None, numeric_id))
        # 接口正常返回了推荐数据，但其中没有目标 id：
        # 说明该作品不可访问（已删除 / 私密 / 仅部分可见 / 地区限制）。
        # 此时不应再降级到分享页（那里只会撞风控，给出误导性报错）。
        raise DouyinUnavailable(
            "抖音未返回该作品的数据：它可能已被删除、设为私密、仅部分可见或存在地区限制，"
            "这类视频本身不支持下载。"
        )

    # 图集（图文帖）：Feed 接口只回推荐流，这里返回 None 交给分享页路径处理
    if target.get("images"):
        return None

    video = target.get("video") or {}
    if not video:
        raise DouyinUnavailable("该内容没有视频流（可能已被删除或设为不可播放）")

    picked = _pick_bitrate(video, height_for_preset(preset))
    play_addr = (picked or {}).get("play_addr") or (video.get("play_addr") or {})
    url_list = [u for u in (play_addr.get("url_list") or []) if u]
    if not url_list:
        return None  # 没有直连地址 → 降级

    height = play_addr.get("height")
    width = play_addr.get("width")
    data_size = play_addr.get("data_size")
    direct_url = url_list[0]

    formats: list[dict[str, Any]] = []
    for rate in video.get("bit_rate") or []:
        addr = rate.get("play_addr") or {}
        if not addr.get("url_list"):
            continue
        h, w, size = addr.get("height"), addr.get("width"), addr.get("data_size")
        formats.append({
            "format_id": f"douyin-{h}p" if h else str(rate.get("gear_name") or "douyin"),
            "label": f"{h}p · mp4（抖音直链）" if h else "mp4（抖音直链）",
            "ext": "mp4",
            "height": h,
            "width": w,
            "fps": None,
            "vcodec": "h264",
            "acodec": "aac",
            "abr": None,
            "tbr": None,
            "filesize": size,
            "filesize_text": _human(int(size)) if size else None,
            "has_video": True,
            "has_audio": True,
            "is_audio_only": False,
            "note": str(rate.get("gear_name") or ""),
            "protocol": "https",
        })
    formats.sort(key=lambda f: f.get("height") or 0, reverse=True)

    # 互补：feed 的 bit_rate 通常只到 720p，而播放直链接口能给到更高档。
    # 用户要更高画质时，用 play_addr.uri 换一条更高分辨率的直链。
    video_uri = (video.get("play_addr") or {}).get("uri")
    want_height = height_for_preset(preset)
    max_feed_height = max((f.get("height") or 0) for f in formats) if formats else 0
    chosen_ratio = f"{height}p" if height else ratio_for_preset(preset)
    if video_uri and (want_height is None or want_height > max_feed_height):
        wanted_ratio = ratio_for_preset(preset)
        try:
            better = play_url_for(opener, video_uri, wanted_ratio)
            if better:
                direct_url = better
                chosen_ratio = wanted_ratio  # 记录实际选中的档位
        except DouyinError:
            pass  # 拿不到更高档就用 feed 的直链，不影响可用性

    if not formats:
        formats = [{
            "format_id": "douyin", "label": "mp4（抖音直链）", "ext": "mp4",
            "height": height, "width": width, "fps": None, "vcodec": "h264", "acodec": "aac",
            "abr": None, "tbr": None, "filesize": data_size,
            "filesize_text": _human(int(data_size)) if data_size else None,
            "has_video": True, "has_audio": True, "is_audio_only": False,
            "note": "", "protocol": "https",
        }]

    return {
        "kind": "video",
        "url": f"https://www.douyin.com/video/{numeric_id}",
        "id": numeric_id,
        "title": (target.get("desc") or "").strip() or f"douyin_{numeric_id}",
        "uploader": (target.get("author") or {}).get("nickname"),
        "extractor": "Douyin (VideoHarbor 直连)",
        "duration": (video.get("duration") or 0) // 1000 or None,
        "duration_text": None,
        "thumbnail": _first_url(video.get("cover")) or _first_url(target.get("cover")),
        "description": "",
        "view_count": (target.get("statistics") or {}).get("play_count"),
        "like_count": (target.get("statistics") or {}).get("digg_count"),
        "upload_date": None,
        "is_live": False,
        "has_subtitles": False,
        "subtitle_langs": [],
        "formats": formats,
        "presets": [],
        "direct_url": direct_url,
        "direct_headers": {"Referer": CDN_REFERER, "User-Agent": UA_APP},
        "ratio": chosen_ratio,
        "video_id": (video.get("play_addr") or {}).get("uri"),
        "note": "抖音：已通过 App 端免 Cookie 接口解析（无水印原画，yt-dlp 的抖音提取器不可用）",
    }


# ---------------------------------------------------------------- 兜底路径


def fetch_share_page(opener: urllib.request.OpenerDirector, numeric_id: str) -> str:
    """请求 SSR 分享页；必须已带游客 Cookie，否则只会拿到风控壳页。

    分享页既承载视频帖（内联 `play_addr`），也承载图文帖（内联 `images`），
    因此有任一数据即视为成功，具体类型由调用方判断。
    """
    url = SHARE_PAGE_TEMPLATE.format(video_id=numeric_id)
    status, body, _ = _request_with_retry(opener, url, timeout=25)
    html = body.decode("utf-8", "replace")
    if status != 200:
        raise DouyinError(f"分享页返回 HTTP {status}")
    if "play_addr" not in html and '"images":[' not in html:
        if "_$jsvmprt" in html or "captcha" in html:
            raise DouyinError("抖音分享页触发了风控（未渲染内容数据），请稍后重试")
        raise DouyinError("分享页未包含内容数据（可能是私密内容或已被删除）")
    return html


def extract_video_id(html: str) -> str:
    match = VIDEO_URI_RE.search(html)
    if not match:
        raise DouyinError("未能从分享页提取 video_id（抖音可能已调整页面结构）")
    return match.group(1)


def play_url_for(opener: urllib.request.OpenerDirector, video_id: str, ratio: str) -> str:
    """用 video_id 换 CDN 直链（302 的 Location）。play 无水印优先，playwm 兜底。"""
    errors: list[str] = []
    for template, label in ((PLAY_API_TEMPLATE, "play"), (PLAYWM_API_TEMPLATE, "playwm")):
        url = template.format(video_id=video_id, ratio=ratio)
        try:
            status, _, headers = _request_with_retry(
                opener, url, follow=False, referer=CDN_REFERER
            )
        except DouyinError as exc:
            errors.append(f"{label}: {exc}")
            continue
        location = headers.get("Location") or ""
        if status in (301, 302, 303, 307) and location:
            return location
        errors.append(f"{label}: HTTP {status}")
    raise DouyinError("播放接口未返回直链（" + "；".join(errors[:2]) + "）")


# ---------------------------------------------------------------- 图文（图集）


def parse_images_array(html: str) -> list[dict[str, Any]] | None:
    """从分享页内联数据里提取 `images` 数组。

    页面里是内联 JSON，路径中的 `/` 被转义为 `\\u002F`，需还原后再解析。
    用括号配平而非正则，避免图片多时匹配被截断。
    """
    start = html.find('"images":[')
    if start < 0:
        return None
    begin = html.index("[", start)
    depth = 0
    end = None
    for idx in range(begin, len(html)):
        char = html[idx]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end is None:
        return None
    try:
        parsed = json.loads(html[begin:end].replace("\\u002F", "/"))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def rank_image_urls(url_list: list[str] | None) -> list[str]:
    """给图片候选链接排序。

    实测对比同一张图的三种链接：
      - `url_list` 的 `.jpeg`：199.8KB **无水印原图** ← 首选
      - `url_list` 的 `.webp`：127.1KB，同源但体积更小
      - `download_url_list`：模板名带 `-water`，**带水印**，不使用

    因此只接受 `url_list`，并把 jpeg 排在 webp 之前。
    """
    urls = [u for u in (url_list or []) if u]
    jpegs = [u for u in urls if ".jpeg?" in u or ".jpg?" in u]
    others = [u for u in urls if u not in jpegs]
    return jpegs + others


def image_extension(url: str) -> str:
    for ext in (".jpeg", ".jpg", ".webp", ".png", ".heic"):
        if ext in url:
            return ext.lstrip(".")
    return "jpg"


def parse_music_duration(html: str) -> float | None:
    """从分享页的 music 对象里取配乐时长（秒）。

    图文帖的 `music.duration` 就是抖音客户端播放该帖的时长，
    用它作为合成视频的总时长最贴近官方效果。
    """
    pos = html.find('"music":{')
    if pos < 0:
        return None
    begin = html.index("{", pos)
    depth = 0
    end = None
    for idx in range(begin, len(html)):
        if html[idx] == "{":
            depth += 1
        elif html[idx] == "}":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end is None:
        return None
    segment = html[begin:end].replace("\\u002F", "/")
    match = re.search(r'"duration":(\d+)', segment)
    if not match:
        return None
    value = int(match.group(1))
    if value <= 0:
        return None
    # 抖音给的通常是秒；极小概率给毫秒
    return value / 1000 if value > 10000 else float(value)


def extract_gallery(html: str, numeric_id: str) -> dict[str, Any] | None:
    """把分享页里的 images 数组转成归一化的图集结果。

    返回 None 表示这不是图文帖（应交给视频路径处理）。
    """
    images = parse_images_array(html)
    if not images:
        return None

    desc = DESC_RE.search(html)
    title = desc.group(1).strip() if desc else f"douyin_{numeric_id}"
    author = AUTHOR_RE.search(html)
    likes = LIKES_RE.search(html)

    # 配乐：图文帖本身没有视频流，但带背景音乐，用于本地合成视频
    music_match = MUSIC_URL_RE.search(html)
    music_url = music_match.group(0).replace("\\u002F", "/") if music_match else None
    music_duration = parse_music_duration(html)

    items: list[dict[str, Any]] = []
    formats: list[dict[str, Any]] = []
    for index, image in enumerate(images, 1):
        ranked = rank_image_urls(image.get("url_list"))
        if not ranked:
            continue
        url = ranked[0]
        width, height = image.get("width"), image.get("height")
        # 动图（实况照片）：同一张图附带一小段视频
        animated = (image.get("video") or {}).get("play_addr", {}).get("url_list") or []
        ext = image_extension(url)
        items.append({
            "index": index,
            "url": url,
            "urls": ranked,
            "ext": ext,
            "width": width,
            "height": height,
            "animated_url": animated[0] if animated else None,
        })
        size_text = f" {width}×{height}" if width and height else ""
        formats.append({
            "format_id": f"douyin-image-{index}",
            "label": f"图 {index}{size_text}" + ("（实况）" if animated else ""),
            "ext": ext,
            "height": height,
            "width": width,
            "fps": None,
            "vcodec": None,
            "acodec": None,
            "abr": None,
            "tbr": None,
            "filesize": None,
            "filesize_text": None,
            "has_video": False,
            "has_audio": False,
            "is_audio_only": False,
            "note": "原图（无水印）",
            "protocol": "https",
        })

    if not items:
        return None

    return {
        "kind": "gallery",
        "url": f"https://www.douyin.com/note/{numeric_id}",
        "id": numeric_id,
        "title": title,
        "uploader": author.group(1).strip() if author else None,
        "extractor": "Douyin (VideoHarbor 直连)",
        "duration": None,
        "duration_text": None,
        "thumbnail": items[0]["url"],
        "description": "",
        "view_count": None,
        "like_count": int(likes.group(1)) if likes else None,
        "upload_date": None,
        "is_live": False,
        "has_subtitles": False,
        "subtitle_langs": [],
        "formats": formats,
        "presets": [],
        "image_count": len(items),
        "animated_count": sum(1 for i in items if i.get("animated_url")),
        # 下载所需的全部信息（tasks 会据此本地合成视频）
        "images": items,
        "music_url": music_url,
        "music_duration": music_duration,
        # 幻灯片视频总时长：优先用配乐时长（与抖音客户端播放效果一致），
        # 没有配乐时按每张图 5 秒估算
        "video_duration": music_duration or max(len(items), 1) * 5.0,
        "direct_headers": {"Referer": CDN_REFERER, "User-Agent": UA_IPHONE},
        "note": (
            f"抖音图文帖：共 {len(items)} 张原图（无水印）"
            + ("，含实况动图" if any(i.get("animated_url") for i in items) else "")
            + "，将本地合成为视频"
        ),
    }


def _extract_via_share(
    opener: urllib.request.OpenerDirector, numeric_id: str, preset: str | None
) -> dict[str, Any]:
    """分享页路径：图文帖返回图集，视频帖取 video_id 后换直链。"""
    html = fetch_share_page(opener, numeric_id)

    gallery = extract_gallery(html, numeric_id)
    if gallery is not None:
        return gallery

    video_id = extract_video_id(html)

    desc = DESC_RE.search(html)
    title = desc.group(1).strip() if desc else f"douyin_{numeric_id}"

    ratio = ratio_for_preset(preset)
    direct_url = play_url_for(opener, video_id, ratio)

    return {
        "kind": "video",
        "url": f"https://www.douyin.com/video/{numeric_id}",
        "id": numeric_id,
        "title": title,
        "uploader": None,
        "extractor": "Douyin (VideoHarbor 直连)",
        "duration": None,
        "duration_text": None,
        "thumbnail": None,
        "description": "",
        "view_count": None,
        "like_count": None,
        "upload_date": None,
        "is_live": False,
        "has_subtitles": False,
        "subtitle_langs": [],
        "formats": [{
            "format_id": f"douyin-{ratio}", "label": f"{ratio} · mp4（抖音直链）", "ext": "mp4",
            "height": None, "width": None, "fps": None, "vcodec": "h264", "acodec": "aac",
            "abr": None, "tbr": None, "filesize": None, "filesize_text": None,
            "has_video": True, "has_audio": True, "is_audio_only": False,
            "note": "音视频已混流，无需合流", "protocol": "https",
        }],
        "presets": [],
        "direct_url": direct_url,
        # CDN 直链 basename 是无意义串（形如 okf9nyEj..mp4），而 yt-dlp 不会从
        # HTTP 头取文件名，因此文件名在下载完成后由 tasks._rename_to_title() 重命名。
        "direct_headers": {"Referer": CDN_REFERER, "User-Agent": UA_IPHONE},
        "ratio": ratio,
        "video_id": video_id,
        "note": "抖音：已通过免 Cookie 直连通路解析（yt-dlp 的抖音提取器不可用）",
    }


# ---------------------------------------------------------------- 对外入口


def extract(
    url: str,
    *,
    preset: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    """解析抖音视频，返回与 yt-dlp 归一化结果同构的 dict。

    失败抛 DouyinError（消息可直接展示）；视频本身不可下载抛 DouyinUnavailable。
    """
    if not is_douyin_url(url):
        raise DouyinError("不是抖音链接")

    jar = http.cookiejar.CookieJar()
    opener = _build_opener(proxy, jar)
    warm_guest_cookies(opener)

    numeric_id = resolve_numeric_id(opener, url)

    # 主路径：App 端 Feed 接口（免签名、更稳，且带精确档位信息）。
    # 注意：Feed 接口对"图文帖"不返回目标数据（只回推荐流），因此这种情况
    # 必须降级到分享页路径，而不能据此判定"视频不可用"。
    feed_error: DouyinError | None = None
    feed_unavailable: DouyinUnavailable | None = None
    try:
        info = _extract_via_feed(opener, numeric_id, preset)
        if info is not None:
            return info
    except DouyinUnavailable as exc:
        feed_unavailable = exc
    except DouyinError as exc:
        feed_error = exc

    # 分享页路径：图文帖在这里解析成图集；视频帖继续取直链
    try:
        return _extract_via_share(opener, numeric_id, preset)
    except DouyinError as share_error:
        # 分享页也失败：若主路径已明确判定"作品不可下载"，用它更准确的原因
        if feed_unavailable is not None:
            raise feed_unavailable from share_error
        if feed_error:
            raise DouyinError(f"{share_error}（另一条通路：{feed_error}）") from share_error
        raise


def probe_summary(info: dict[str, Any]) -> str:
    """给日志用的一行摘要。"""
    return json.dumps(
        {"video_id": info.get("video_id"), "ratio": info.get("ratio"), "title": info.get("title")},
        ensure_ascii=False,
    )
