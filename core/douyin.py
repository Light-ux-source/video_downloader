"""抖音（Douyin）免 Cookie 提取器。

背景
----
yt-dlp 的抖音提取器走的是 `www.douyin.com/aweme/v1/web/aweme/detail/` 这类 Web 业务接口，
这些接口现在要求 `a_bogus` / msToken 请求签名（实测返回 `status_code: 11110` /
`encrypt_data_miss`，或直接 403）。yt-dlp 源码里此处仍是未实现的 TODO：

    # TODO: Run verification challenge code to generate signature cookies

因此直接交给 yt-dlp 必然报 `Fresh cookies (not necessarily logged in) are needed`。

本项目采用的通路（**无需用户登录、无需用户提供 Cookie、无需签名算法**）
------------------------------------------------------------------
1. 访问 douyin.com / iesdouyin.com 首页，拿到**游客 Cookie**（`ttwid`、`__ac_nonce`）。
   这只是设备标识，不代表任何账号登录态。
2. 带上游客 Cookie 请求 SSR 分享页 `www.iesdouyin.com/share/video/<数字id>/`，
   页面会内联渲染出视频数据，其中 `play_addr.uri` 就是 `video_id`（形如 `v0d00fg10000...`）。
   —— 不带游客 Cookie 时该页只返回风控壳页（captcha/slardar），这是最容易踩的坑。
3. 用 `video_id` 调 **App 端旧播放直链接口**
   `https://aweme.snssdk.com/aweme/v1/play/?video_id=<id>&ratio=<档位>`，
   它只校验 video_id 合法性与时间戳，**不需要任何签名**，返回 302 指向 CDN 直链。
4. CDN（`*.douyinvod.com`）只校验 Referer，带上 `https://www.douyin.com/` 即可下载。

实测要点
-------
- 必须用 `/aweme/v1/play/`，**不要用** `/aweme/v1/playwm/`：
  后者固定返回 720p 且带水印；前者才真正响应 ratio（540p/720p/1080p）。
- CDN 直链有效期约 1–2 小时，因此"解析"与"下载"必须在同一流程内完成，
  不能把链接存起来过很久再用。

边界
----
- 仅支持公开视频；私密/仅好友可见拿不到 video_id。
- 图文笔记（非视频）拿到的不是视频流。
- 直播与直播回放不适用（走 live 接口）。
- 抖音若下掉旧播放接口或给分享页也加签名，此通路会失效——届时会明确报错。
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# 游客 Cookie 的获取入口（不登录，仅拿设备标识）
GUEST_COOKIE_URLS = (
    "https://www.douyin.com/",
    "https://www.iesdouyin.com/",
)

SHARE_PAGE_TEMPLATE = "https://www.iesdouyin.com/share/video/{video_id}/"
PLAY_API_TEMPLATE = (
    "https://aweme.snssdk.com/aweme/v1/play/?video_id={video_id}&ratio={ratio}&line=0"
)
# playwm 会带水印且固定 720p，仅作为 play 不可用时的最后兜底
PLAYWM_API_TEMPLATE = (
    "https://aweme.snssdk.com/aweme/v1/playwm/?video_id={video_id}&ratio={ratio}&line=0"
)
CDN_REFERER = "https://www.douyin.com/"

UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 画质档位 → 分享/播放接口的 ratio 参数
RATIO_BY_PRESET: dict[str, str] = {
    "best": "1080p",
    "1080": "1080p",
    "720": "720p",
    "480": "540p",
    "360": "540p",
}

DOUYIN_HOST_RE = re.compile(
    r"^https?://(?:"
    r"v\.douyin\.com|"
    r"www\.douyin\.com|douyin\.com|"
    r"www\.iesdouyin\.com|iesdouyin\.com"
    r")/",
    re.IGNORECASE,
)
# 数字作品 id：出现在 /video/<id>、/note/<id>、/share/video/<id>
NUMERIC_ID_RE = re.compile(r"/(?:video|note|share/video|share/note)/(\d{6,})")
# SSR 页面里的 video_id（play_addr.uri）
VIDEO_URI_RE = re.compile(r'"play_addr":\{"uri":"(v0[0-9a-zA-Z]+)"')
# 分享页里的标题
DESC_RE = re.compile(r'"desc":"(.*?)","create_time"')


class DouyinError(RuntimeError):
    """抖音提取失败（消息面向用户，可直接展示）。"""


def is_douyin_url(url: str) -> bool:
    return bool(DOUYIN_HOST_RE.match((url or "").strip()))


def ratio_for_preset(preset: str | None) -> str:
    return RATIO_BY_PRESET.get(str(preset or "best"), "1080p")


def _build_opener(proxy: str | None, jar: http.cookiejar.CookieJar) -> urllib.request.OpenerDirector:
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
    follow: bool = True,
    referer: str | None = None,
    timeout: int = 25,
) -> tuple[int, bytes, dict[str, str]]:
    if not follow:
        opener = urllib.request.build_opener(
            *[h for h in opener.handlers if not isinstance(h, urllib.request.HTTPRedirectHandler)],
            _NoRedirect(),
        )
    headers = {"User-Agent": UA_IPHONE, "Accept-Language": "zh-CN,zh;q=0.9"}
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
    except OSError as exc:
        raise DouyinError(f"网络请求失败：{exc}") from exc


def warm_guest_cookies(opener: urllib.request.OpenerDirector) -> None:
    """访问首页获取游客 Cookie（失败不致命，分享页可能仍可渲染）。"""
    for url in GUEST_COOKIE_URLS:
        try:
            _request(opener, url, timeout=15)
        except DouyinError:
            continue


def resolve_numeric_id(
    opener: urllib.request.OpenerDirector, url: str
) -> str:
    """从抖音链接中取出数字作品 id（短链需要跟随 302）。"""
    match = NUMERIC_ID_RE.search(url)
    if match:
        return match.group(1)

    if "v.douyin.com" in url:
        status, _, headers = _request(opener, url, follow=False, timeout=20)
        location = headers.get("Location") or ""
        if not location:
            # 有的实现不给 Location 而是直接 200 渲染，试着从正文里找
            status, body, _ = _request(opener, url, timeout=25)
            found = NUMERIC_ID_RE.search(body.decode("utf-8", "replace"))
            if found:
                return found.group(1)
            raise DouyinError("短链未能跳转到视频页，请改用完整链接（含 /video/<id>）")
        match = NUMERIC_ID_RE.search(location)
        if match:
            return match.group(1)
        raise DouyinError(f"短链跳转结果无法识别：{location[:120]}")

    raise DouyinError("未能从链接中识别抖音作品 id，请使用 /video/<id> 形式的链接")


def fetch_share_page(opener: urllib.request.OpenerDirector, numeric_id: str) -> str:
    """请求 SSR 分享页；必须已带游客 Cookie，否则只会拿到风控壳页。"""
    url = SHARE_PAGE_TEMPLATE.format(video_id=numeric_id)
    status, body, _ = _request(opener, url, timeout=25)
    html = body.decode("utf-8", "replace")
    if status != 200:
        raise DouyinError(f"分享页返回 HTTP {status}")
    if "play_addr" not in html:
        # 区分"视频不存在"与"被风控"
        if "_$jsvmprt" in html or "captcha" in html:
            raise DouyinError(
                "抖音分享页触发了风控（未渲染视频数据）。"
                "请稍后重试；若持续失败说明该通路可能已被封堵。"
            )
        raise DouyinError("分享页未包含视频数据（可能是图文笔记、私密视频或已被删除）")
    return html


def extract_video_id(html: str) -> str:
    match = VIDEO_URI_RE.search(html)
    if not match:
        raise DouyinError("未能从分享页提取 video_id（抖音可能已调整页面结构）")
    return match.group(1)


def play_url_for(
    opener: urllib.request.OpenerDirector, video_id: str, ratio: str
) -> str:
    """用 video_id 换 CDN 直链（302 的 Location）。"""
    errors: list[str] = []
    for template, label in ((PLAY_API_TEMPLATE, "play"), (PLAYWM_API_TEMPLATE, "playwm")):
        url = template.format(video_id=video_id, ratio=ratio)
        try:
            status, _, headers = _request(opener, url, follow=False, referer=CDN_REFERER)
        except DouyinError as exc:
            errors.append(f"{label}: {exc}")
            continue
        location = headers.get("Location") or ""
        if status in (301, 302, 303, 307) and location:
            return location
        errors.append(f"{label}: HTTP {status}")
    raise DouyinError("播放接口未返回直链（" + "；".join(errors[:2]) + "）")


def extract(
    url: str,
    *,
    preset: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    """解析抖音视频，返回与 yt-dlp 归一化结果同构的 dict。

    失败抛 DouyinError（消息可直接展示给用户）。
    """
    if not is_douyin_url(url):
        raise DouyinError("不是抖音链接")

    jar = http.cookiejar.CookieJar()
    opener = _build_opener(proxy, jar)
    warm_guest_cookies(opener)

    numeric_id = resolve_numeric_id(opener, url)
    html = fetch_share_page(opener, numeric_id)
    video_id = extract_video_id(html)

    desc = DESC_RE.search(html)
    title = desc.group(1).strip() if desc else f"douyin_{numeric_id}"

    # 分享页里内联的元信息（拿不到就用默认值）
    duration: float | None = None
    cover: str | None = None
    match = re.search(r'"video":\{"play_addr".*?"duration":(\d+)', html)
    if match:
        duration = round(int(match.group(1)) / 1000, 3) if int(match.group(1)) > 1000 else int(match.group(1))
    match = re.search(r'"cover":\{"uri":"[^"]*","url_list":\["(https:[^"]+)"', html)
    if match:
        cover = match.group(1).encode().decode("unicode_escape").replace("\\u002F", "/")

    ratio = ratio_for_preset(preset)
    direct_url = play_url_for(opener, video_id, ratio)

    return {
        "kind": "video",
        "url": url,
        "id": numeric_id,
        "title": title,
        "uploader": None,
        "extractor": "Douyin (VideoHarbor 直连)",
        "duration": duration,
        "duration_text": None,
        "thumbnail": cover,
        "description": "",
        "view_count": None,
        "like_count": None,
        "upload_date": None,
        "is_live": False,
        "has_subtitles": False,
        "subtitle_langs": [],
        # 抖音直链是单一 mp4（音视频已混流），因此只给一个"格式"
        "formats": [
            {
                "format_id": f"douyin-{ratio}",
                "label": f"{ratio} · mp4（抖音直链）",
                "ext": "mp4",
                "height": None,
                "width": None,
                "fps": None,
                "vcodec": "h264",
                "acodec": "aac",
                "abr": None,
                "tbr": None,
                "filesize": None,
                "filesize_text": None,
                "has_video": True,
                "has_audio": True,
                "is_audio_only": False,
                "note": "音视频已混流，无需合流",
                "protocol": "https",
            }
        ],
        "presets": [],
        # 供下载流程使用
        "direct_url": direct_url,
        # 注意：CDN 直链的 basename 是无意义串（形如 okf9nyEj..mp4），
        # 而 yt-dlp 不会从 HTTP 头取文件名，因此文件名在下载完成后由
        # tasks._rename_to_title() 按标题重命名。
        "direct_headers": {"Referer": CDN_REFERER, "User-Agent": UA_IPHONE},
        "ratio": ratio,
        "video_id": video_id,
        "note": "抖音：已通过免 Cookie 直连通路解析（yt-dlp 的抖音提取器不可用）",
    }


def probe_summary(info: dict[str, Any]) -> str:
    """给日志用的一行摘要。"""
    return json.dumps(
        {"video_id": info.get("video_id"), "ratio": info.get("ratio"), "title": info.get("title")},
        ensure_ascii=False,
    )
