"""yt-dlp 引擎封装。

设计原则：把 yt-dlp 当作独立进程调用，而不是 import 成库。
好处：不受 Python 版本约束、升级引擎只需换安装包、业务代码零改动、命令行可复现排障。
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from . import config, douyin

# ---------------------------------------------------------------------------
# 进度解析
# ---------------------------------------------------------------------------

# 与 --progress-template 中的字段一一对应
PROGRESS_TEMPLATE = (
    "download:"
    "%(progress._percent_str)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s"
)

# 进度行的识别必须"按结构而非按前缀"：
# 单独使用模板时行形如 "DL| 12.6%|523264|..."
# 但与 --print-to-file 同时使用时，yt-dlp 会连模板里的字面前缀一起吃掉，
# 行退化成 " 12.6%|523264|4162618|NA|2989107.5|1"。
_DL_LINE = re.compile(r"DL\|(.+)$")
# 退化的进度行：形如 "  12.6%|8423526|69759161|NA|614009.5|99"
# 即 "百分比%|下载字节|总字节|估算字节|速度|剩余秒数"，其中缺失字段为 NA
_BARE_PROGRESS = re.compile(
    r"^\s*-?\d+(?:\.\d+)?%\|-?[\d.]+(?:\|(?:-?[\d.]+|NA)){4,5}\s*$"
)
DEST_LINE = re.compile(r"^\[download\] Destination: (.+)$")
MERGE_LINE = re.compile(r"^\[Merger\] Merging formats into \"(.+)\"$")
ALREADY_LINE = re.compile(r"^\[download\] (.+) has already been downloaded$")
ERROR_LINE = re.compile(r"^ERROR:\s*(.*)$")
# 播放列表下载结束摘要，例如 "[download] Finished downloading playlist: 标题"
PLAYLIST_DONE_LINE = re.compile(r"^\[download\] Finished downloading playlist:\s*(.*)$")
_WARN_PREFIXES = (
    "[download] Got error",
    "WARNING:",
)


def _num(raw: str) -> float | None:
    """把 yt-dlp 的 'NA' / '1234.5' 之类的文本转成数字。"""
    if not raw or raw in ("NA", "None", "none", ""):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return None if value < 0 else value


def parse_progress_line(line: str) -> dict[str, Any] | None:
    """解析一行进度输出，返回结构化进度；非进度行返回 None。

    兼容两种形态：
      DL|  12.6%|523264|4162618|NA|2989107.5|1     （独立使用模板）
        12.6%|523264|4162618|NA|2989107.5|1       （与 --print-to-file 同时使用时前缀被吃掉）
    """
    text = line.strip()
    match = _DL_LINE.search(text)
    if match:
        body = match.group(1)
    elif _BARE_PROGRESS.match(text):
        body = text
    else:
        return None

    parts = body.split("|")
    if len(parts) < 6:
        return None

    percent_raw, downloaded, total, total_est, speed, eta = parts[:6]
    downloaded_b = _num(downloaded) or 0.0
    total_b = _num(total) or _num(total_est)

    percent: float | None = None
    if total_b:
        percent = min(100.0, downloaded_b / total_b * 100.0)
    else:
        stripped = percent_raw.strip().rstrip("%")
        percent = _num(stripped)

    return {
        "percent": round(percent, 2) if percent is not None else None,
        "downloaded_bytes": int(downloaded_b),
        "total_bytes": int(total_b) if total_b else None,
        "speed": _num(speed),
        "eta": int(_num(eta) or 0) if _num(eta) is not None else None,
    }


def classify_line(line: str) -> tuple[str, Any] | None:
    """把 yt-dlp 输出行分类，用于驱动任务阶段与文件识别。"""
    stripped = line.strip()
    if not stripped:
        return None

    progress = parse_progress_line(stripped)
    if progress is not None:
        return "progress", progress

    match = DEST_LINE.match(stripped)
    if match:
        return "destination", match.group(1)

    match = MERGE_LINE.match(stripped)
    if match:
        return "merge", match.group(1)

    match = ALREADY_LINE.match(stripped)
    if match:
        return "already", match.group(1)

    match = PLAYLIST_DONE_LINE.match(stripped)
    if match:
        return "playlist_done", match.group(1)

    match = ERROR_LINE.match(stripped)
    if match:
        return "error", match.group(1)

    if stripped.startswith(_WARN_PREFIXES):
        return "warning", stripped

    if "Embedding subtitles" in stripped:
        return "embed_subs", stripped
    if "Adding thumbnail" in stripped or "Embedding thumbnail" in stripped:
        return "embed_thumb", stripped
    if stripped.startswith("[ExtractAudio]"):
        return "extract_audio", stripped
    if stripped.startswith("[Metadata]") or "Adding metadata" in stripped:
        return "metadata", stripped

    return "log", stripped


# ---------------------------------------------------------------------------
# 画质预设
# ---------------------------------------------------------------------------

# 说明：yt-dlp 的 -f 表达式；audio_only 走 -x 音频提取
QUALITY_PRESETS: list[dict[str, Any]] = [
    {"id": "best", "label": "最佳画质", "hint": "自动选择最高画质与音轨", "format": "bv*+ba/b"},
    {"id": "1080", "label": "1080P", "hint": "不超过 1920×1080", "format": "bv*[height<=1080]+ba/b[height<=1080]/b"},
    {"id": "720", "label": "720P", "hint": "不超过 1280×720", "format": "bv*[height<=720]+ba/b[height<=720]/b"},
    {"id": "480", "label": "480P", "hint": "不超过 854×480", "format": "bv*[height<=480]+ba/b[height<=480]/b"},
    {"id": "360", "label": "360P", "hint": "省流量", "format": "bv*[height<=360]+ba/b[height<=360]/b"},
    {"id": "audio", "label": "仅音频", "hint": "提取音轨（默认 MP3）", "format": None, "audio_only": True},
]

# 画质相同时的编码偏好：优先 H.264（本地播放器/剪辑软件兼容性最好），
# HEVC 放最后（部分播放器与浏览器不支持）。
DEFAULT_SORT = ["res", "codec:h264:av01:vp9:hevc", "br", "size"]
# 首选流不可用时的回退顺序（网络上某些编码流会被 CDN 拒绝）
FALLBACK_SORT = ["res", "codec:av01:vp9:h264:hevc", "br", "size"]
AUDIO_SORT = ["abr", "size"]

AUDIO_FORMATS = ["mp3", "m4a", "opus", "flac", "wav"]


def preset_by_id(preset_id: str) -> dict[str, Any]:
    for preset in QUALITY_PRESETS:
        if preset["id"] == preset_id:
            return preset
    return QUALITY_PRESETS[0]


# 多策略重试计划：第 1 项是首选方案，后续为降级方案。
# 目的：网络上常见「某个编码流被 CDN 拒绝」「多分片并发被限速断开」，
# 换编码或降并发后往往就能成功，比直接失败对用户友好得多。
STRATEGY_PATCHES: list[tuple[str, dict[str, Any]]] = [
    ("备用编码（VP9/AV1/HEVC）重试", {"_codec_phase": "fallback"}),
    ("降低并发分片重试", {"concurrent_fragments": 1, "chunk_size": "1M"}),
    ("单连接分片重试", {"_codec_phase": "fallback", "concurrent_fragments": 1, "chunk_size": "512K"}),
]


def _drop_codec(patch: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in patch.items() if k != "_codec_phase"}


# 这些错误重试多少次都没用，直接失败并把原因告诉用户，别浪费时间和流量
PERMANENT_ERROR_HINTS = (
    "unsupported url",
    "is not a valid url",
    "not found",
    "404",
    "video unavailable",
    "this video is unavailable",
    "private video",
    "members-only",
    "has been removed",
    "been deleted",
    "may be deleted",
    "geo-restricted",
    "not available in your country",
    "no video formats found",
    "sign in to confirm your age",
    "login required",
    "account credentials",
    "fresh cookies",
    "unable to extract",
    "requested format is not available",
    "is not a valid",
)


def is_permanent_error(message: str) -> bool:
    """判断错误是否属于"重试也不会成功"的类型。"""
    text = (message or "").lower()
    return any(hint in text for hint in PERMANENT_ERROR_HINTS)


def retry_strategies(opts: dict[str, Any], audio_only: bool) -> list[tuple[str, dict[str, Any]]]:
    """返回 [(说明, 参数补丁), ...]，首项为空补丁（即用户原始配置）。"""
    plans: list[tuple[str, dict[str, Any]]] = [("按原始设置执行", {})]
    # 自定义格式表达式下不擅自换编码，只做网络层面的降级
    if opts.get("custom_format"):
        plans.extend((f"{label}（保持自定义格式）", _drop_codec(patch)) for label, patch in STRATEGY_PATCHES)
    elif audio_only:
        # 音频只有单流，换编码无意义，只降并发
        plans.extend((label, _drop_codec(patch)) for label, patch in STRATEGY_PATCHES[1:])
    else:
        plans.extend(STRATEGY_PATCHES)
    return plans


def build_format_args(opts: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """根据下载选项生成 -f / -x 相关参数，并返回归一化后的选项。

    opts 支持：
      preset: best/1080/720/480/360/audio
      custom_format: 自定义 -f 表达式（优先级高于 preset）
      audio_only, audio_format, subtitles, subtitle_langs, auto_subs, embed_subs,
      playlist, playlist_items, cookies_file, proxy, limit_rate, concurrent_fragments,
      skip_existing, sponsorblock
    """
    preset = preset_by_id(str(opts.get("preset") or "best"))
    custom_format = (opts.get("custom_format") or "").strip()
    audio_only = bool(opts.get("audio_only")) or bool(preset.get("audio_only"))

    normalized: dict[str, Any] = {
        "preset": preset["id"],
        "custom_format": custom_format or None,
        "audio_only": audio_only,
        "audio_format": (opts.get("audio_format") or "mp3").lower(),
        "subtitles": bool(opts.get("subtitles")),
        "subtitle_langs": (opts.get("subtitle_langs") or "zh-Hans,zh-CN,en").strip(),
        "auto_subs": bool(opts.get("auto_subs")),
        "playlist": bool(opts.get("playlist")),
        "playlist_items": (str(opts.get("playlist_items") or "")).strip(),
        "cookies_file": (opts.get("cookies_file") or "").strip() or None,
        "cookies_from_browser": (opts.get("cookies_from_browser") or "").strip() or None,
        "proxy": (opts.get("proxy") or "").strip() or None,
        "limit_rate": (str(opts.get("limit_rate") or "")).strip() or None,
        "concurrent_fragments": max(1, int(opts.get("concurrent_fragments") or 4)),
        "chunk_size": (str(opts.get("chunk_size") or "")).strip() or None,
        "skip_existing": bool(opts.get("skip_existing")),
        "sponsorblock": bool(opts.get("sponsorblock")),
        # 内部字段：由任务层的多策略重试写入（primary / fallback）
        "codec_phase": "fallback" if opts.get("_codec_phase") == "fallback" else "primary",
    }
    if normalized["audio_format"] not in AUDIO_FORMATS:
        normalized["audio_format"] = "mp3"

    args: list[str] = []

    if audio_only:
        args += ["-x", "--audio-format", normalized["audio_format"]]
        if normalized["audio_format"] in ("mp3", "m4a", "opus"):
            args += ["--audio-quality", "0"]
        args += ["--embed-metadata"]
        if normalized["audio_format"] in ("mp3", "m4a"):
            args += ["--embed-thumbnail"]
    else:
        fmt = custom_format or preset.get("format") or "bv*+ba/b"
        sort = DEFAULT_SORT if normalized["codec_phase"] != "fallback" else FALLBACK_SORT
        args += ["-f", fmt, "-S", ",".join(sort)]

    # 字幕：仅视频容器支持内嵌
    if normalized["subtitles"] and not audio_only:
        args += ["--sub-langs", normalized["subtitle_langs"] or "all"]
        if normalized["auto_subs"]:
            args += ["--write-auto-subs"]
        if opts.get("embed_subs", True):
            args += ["--embed-subs", "--convert-subs", "srt"]

    if normalized["sponsorblock"]:
        args += ["--sponsorblock-remove", "sponsor"]

    if normalized["cookies_file"]:
        args += ["--cookies", normalized["cookies_file"]]
    elif normalized["cookies_from_browser"]:
        args += ["--cookies-from-browser", normalized["cookies_from_browser"]]

    if normalized["proxy"]:
        args += ["--proxy", sanitize_proxy(normalized["proxy"]) or normalized["proxy"]]
    # 自定义请求头（抖音 CDN 需要 Referer，否则 403）
    for key, value in (opts.get("headers") or {}).items():
        args += ["--add-header", f"{key}:{value}"]
    if normalized["limit_rate"]:
        args += ["--limit-rate", normalized["limit_rate"]]
    if normalized["concurrent_fragments"] > 1:
        args += ["--concurrent-fragments", str(normalized["concurrent_fragments"])]
    if normalized["chunk_size"]:
        # 分片下载，缓解 CDN 中途断开（配合多策略重试）
        args += ["--http-chunk-size", normalized["chunk_size"]]
    if normalized["skip_existing"]:
        args += ["--no-overwrites"]
    if not normalized["playlist"]:
        args += ["--no-playlist"]
    elif normalized["playlist_items"]:
        args += ["--playlist-items", normalized["playlist_items"]]

    return args, normalized


def output_template(directory: Path) -> str:
    """统一输出模板：标题截断 120 字节，避免文件名过长。"""
    return str(Path(directory) / "%(title).120B.%(ext)s")


# ---------------------------------------------------------------------------
# 环境自适应：JS 运行时 / 代理地址 / 链接纠正 / 错误信息
# ---------------------------------------------------------------------------

# 新版 yt-dlp 需要 JS 运行时来解 YouTube 的 n 挑战（否则报
# "The page needs to be reloaded." 或只剩残缺画质）。
# 且它默认只启用 deno，其他运行时必须显式声明，因此这里自动探测并注入。
JS_RUNTIME_CANDIDATES = ("node", "deno", "bun", "qjs")


def detect_js_runtime() -> str | None:
    """探测可用的 JS 运行时，返回可执行文件路径。"""
    import shutil

    for name in JS_RUNTIME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def js_runtime_args() -> list[str]:
    """生成 --js-runtimes 参数；没有可用运行时时返回空（并保持原有行为）。"""
    runtime = detect_js_runtime()
    if not runtime:
        return []
    return ["--js-runtimes", f"node:{runtime}" if Path(runtime).name.startswith("node") else runtime]


def sanitize_proxy(proxy: str | None) -> str | None:
    """补全代理协议头。

    用户常直接填 "127.0.0.1:7897"，yt-dlp 虽多数情况能识别，但显式补全更稳，
    也避免把 "127.0.0.1:7897" 当成 "127.0.0.1" 这种歧义。
    """
    if not proxy:
        return None
    value = proxy.strip()
    if not value:
        return None
    if "://" in value:
        return value
    return f"http://{value}"


# 抖音：yt-dlp 的提取器只认 /video/<id>，/note/<id>（图文/视频混排页）会落到通用
# 提取器并报 Unsupported URL。这里统一纠正为 /video/ 形态再尝试。
DOUYIN_NOTE_RE = re.compile(
    r"^(?P<prefix>https?://(?:www\.)?douyin\.com)/note/(?P<id>\d+)(?P<tail>.*)$",
    re.IGNORECASE,
)


def normalize_url(url: str) -> tuple[str, str | None]:
    """把已知的不受支持链接形态纠正为受支持形态。

    返回 (纠正后的 URL, 提示语或 None)。
    """
    match = DOUYIN_NOTE_RE.match(url.strip())
    if match:
        fixed = f"{match.group('prefix')}/video/{match.group('id')}{match.group('tail')}"
        return fixed, "抖音 /note/ 链接引擎不支持，已自动按 /video/ 形态重试"
    return url, None


# 把 yt-dlp 的内部报错翻译成用户能照着做的中文提示
_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    (
        "the page needs to be reloaded",
        "YouTube 的 JS 挑战未解开：需要 JS 运行时。请确认已安装 node（brew install node），"
        "然后重启服务（./run.sh）后重试。",
    ),
    (
        "js challenge providers",
        "缺少 JS 运行时，YouTube 只能拿到低画质。请安装 node 后重启服务。",
    ),
    (
        "fresh cookies",
        "抖音接口要求签名参数（a_bogus/msToken），yt-dlp 尚未实现该验证算法，"
        "即使提供了 cookies 也会失败。可尝试其它站点或等待引擎更新。",
    ),
    (
        "unsupported url",
        "该链接形态不受引擎支持。抖音请使用 /video/<id> 或分享短链；"
        "若为图文帖（/note/）则本身不含可下载视频。",
    ),
    (
        "sign in to confirm your age",
        "该视频有年龄限制，需要在“高级参数”中配置已登录的 Cookies。",
    ),
    (
        "login required",
        "该站点需要登录，请在“高级参数”中配置 Cookies。",
    ),
    (
        "account credentials",
        "该站点要求账号凭据，请配置 Cookies 或代理。",
    ),
    (
        "geo-restricted",
        "该视频有地区限制，请在“高级参数”中配置可用地区的代理。",
    ),
    (
        "not available in your country",
        "该视频有地区限制，请配置可用地区的代理。",
    ),
    (
        "members-only",
        "该视频为会员专属，需要配置有会员权限的 Cookies。",
    ),
    (
        "no video formats found",
        "未找到可用画质：可能是直播回放未开始、链接失效或需要 Cookies。",
    ),
    (
        "certificate verify failed",
        "HTTPS 证书校验失败，通常是代理中间人证书问题；可尝试更换代理或关闭证书校验。",
    ),
    (
        "unable to connect to proxy",
        "无法连接代理：请确认代理软件在运行，且地址端口填写正确。",
    ),
    (
        "connection refused",
        "连接被拒绝：请检查代理地址与端口是否正确。",
    ),
)


def friendly_error(message: str) -> str:
    """给原始错误追加一句可操作的中文提示（不改写原始信息，便于排障）。"""
    text = (message or "").strip()
    if not text:
        return "解析失败（引擎未返回详细信息，请查看任务日志）"
    lowered = text.lower()
    for needle, hint in _ERROR_HINTS:
        if needle in lowered:
            return f"{text}\n提示：{hint}"
    return text


# ---------------------------------------------------------------------------
# 子进程封装
# ---------------------------------------------------------------------------


class EngineError(RuntimeError):
    """yt-dlp 调用失败。"""


def _base_args() -> list[str]:
    return [
        *config.ytdlp_command(),
        "--ignore-config",
        "--no-color",
        "--no-warnings",
        "--newline",
        "--socket-timeout", "20",
        "--retries", "15",
        "--fragment-retries", "15",
        "--no-cache-dir",
        # 自动注入 JS 运行时（YouTube 的 n 挑战必需；无运行时时自动跳过）
        *js_runtime_args(),
    ]


def _ffmpeg_args() -> list[str]:
    location = config.ffmpeg_dir()
    return ["--ffmpeg-location", location] if location else []


class EngineProcess:
    """一次 yt-dlp 子进程调用，可实时回调输出并可被取消。"""

    def __init__(self, args: list[str]):
        self.args = args
        self.process: subprocess.Popen[str] | None = None
        self.canceled = False

    def run(self, on_line: Callable[[str], None] | None = None) -> int:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(
            self.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            errors="replace",
            env=env,
            start_new_session=True,  # 独立进程组，便于连同子进程一起结束
        )
        try:
            assert self.process.stdout is not None
            for raw in self.process.stdout:
                line = raw.rstrip("\n")
                if on_line:
                    on_line(line)
        finally:
            if self.process.stdout:
                self.process.stdout.close()
        return self.process.wait()

    def cancel(self) -> None:
        """先 SIGTERM 整个进程组，宽限期后 SIGKILL。"""
        self.canceled = True
        proc = self.process
        if not proc or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


# ---------------------------------------------------------------------------
# 解析（probe）
# ---------------------------------------------------------------------------


def _human_size(num: float | None) -> str | None:
    if not num:
        return None
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    value = float(num)
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    return f"{value:.1f}{units[idx]}" if idx else f"{int(value)}B"


def _format_label(fmt: dict[str, Any]) -> str:
    height = fmt.get("height")
    if height:
        label = f"{height}p"
        fps = fmt.get("fps")
        if fps and fps >= 50:
            label += f"{int(fps)}"
    elif fmt.get("vcodec") in (None, "none"):
        label = "音频"
    else:
        label = fmt.get("format_note") or fmt.get("format_id") or "未知"
    ext = fmt.get("ext") or ""
    return f"{label} · {ext}" if ext else label


def normalize_formats(info: dict[str, Any], limit: int = 80) -> list[dict[str, Any]]:
    """把 yt-dlp 的 formats 精简成前端可用的列表。"""
    raw = info.get("formats") or []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for fmt in raw:
        fid = str(fmt.get("format_id") or "")
        if not fid or fid in seen:
            continue
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        has_video = bool(vcodec and vcodec != "none")
        has_audio = bool(acodec and acodec != "none")
        if not has_video and not has_audio:
            continue
        seen.add(fid)
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        result.append({
            "format_id": fid,
            "label": _format_label(fmt),
            "ext": fmt.get("ext"),
            "height": fmt.get("height"),
            "width": fmt.get("width"),
            "fps": fmt.get("fps"),
            "vcodec": vcodec if has_video else None,
            "acodec": acodec if has_audio else None,
            "abr": fmt.get("abr"),
            "tbr": fmt.get("tbr"),
            "filesize": int(size) if size else None,
            "filesize_text": _human_size(size),
            "has_video": has_video,
            "has_audio": has_audio,
            "is_audio_only": has_audio and not has_video,
            "note": fmt.get("format_note"),
            "protocol": fmt.get("protocol"),
        })

    # 体积未知的排在后面，画质高的排前面
    result.sort(key=lambda f: (f["height"] or 0, f["filesize"] or 0), reverse=True)
    return result[:limit]


def normalize_info(info: dict[str, Any], url: str) -> dict[str, Any]:
    """整理视频元信息，兼容单视频与播放列表。"""
    if info.get("_type") == "playlist":
        entries = []
        for entry in (info.get("entries") or [])[:100]:
            if not entry:
                continue
            entries.append({
                "id": entry.get("id"),
                "title": entry.get("title"),
                "url": entry.get("webpage_url") or entry.get("url"),
                "duration": entry.get("duration"),
                "uploader": entry.get("uploader") or entry.get("channel"),
                "thumbnail": entry.get("thumbnail"),
            })
        return {
            "kind": "playlist",
            "url": info.get("webpage_url") or url,
            "title": info.get("title") or "播放列表",
            "uploader": info.get("uploader") or info.get("channel"),
            "entry_count": info.get("playlist_count") or len(entries),
            "entries": entries,
            "formats": [],
            "presets": QUALITY_PRESETS,
        }

    return {
        "kind": "video",
        "url": info.get("webpage_url") or url,
        "id": info.get("id"),
        "title": info.get("title") or "未命名视频",
        "uploader": info.get("uploader") or info.get("channel") or info.get("creator"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "duration": info.get("duration"),
        "duration_text": _fmt_duration(info.get("duration")),
        "thumbnail": info.get("thumbnail"),
        "description": (info.get("description") or "")[:2000],
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "upload_date": info.get("upload_date"),
        "is_live": bool(info.get("is_live")),
        "has_subtitles": bool(info.get("subtitles") or info.get("automatic_captions")),
        "subtitle_langs": sorted((info.get("subtitles") or {}).keys())[:20],
        "formats": normalize_formats(info),
        "presets": QUALITY_PRESETS,
    }


def _fmt_duration(seconds: Any) -> str | None:
    if not seconds:
        return None
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def probe(url: str, opts: dict[str, Any] | None = None, timeout: int | None = None) -> dict[str, Any]:
    """解析视频信息。返回归一化后的 dict；失败抛 EngineError。"""
    opts = opts or {}
    # 已知不受支持的链接形态先纠正（例如抖音 /note/ → /video/）
    target_url, url_note = normalize_url(url)

    # 抖音：yt-dlp 的提取器撞签名墙（encrypt_data_miss / 403），
    # 改走专用免 Cookie 直连通路（见 core/douyin.py 的说明）。
    if douyin.is_douyin_url(target_url):
        try:
            info = douyin.extract(
                target_url,
                preset=opts.get("preset"),
                proxy=sanitize_proxy(str(opts["proxy"])) if opts.get("proxy") else None,
            )
        except douyin.DouyinError as exc:
            # 抖音的失败原因（含"该视频本身不支持下载"）直接作为业务错误返回，
            # 不要让它冒泡成"服务内部错误"，否则用户会以为是程序坏了。
            raise EngineError(str(exc)) from exc
        if url_note:
            info["note"] = f"{url_note}；{info.get('note', '')}".strip("；")
        return info
    args = [
        *_base_args(),
        "--dump-single-json",
        "--skip-download",
        "--no-progress",
    ]
    if not opts.get("playlist"):
        args.append("--no-playlist")
    else:
        args.append("--flat-playlist")
    if opts.get("cookies_file"):
        args += ["--cookies", str(opts["cookies_file"])]
    elif opts.get("cookies_from_browser"):
        args += ["--cookies-from-browser", str(opts["cookies_from_browser"])]
    if opts.get("proxy"):
        args += ["--proxy", sanitize_proxy(str(opts["proxy"])) or str(opts["proxy"])]
    args.append(target_url)

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout or config.PROBE_TIMEOUT,
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineError(f"解析超时（>{timeout or config.PROBE_TIMEOUT}s），请检查网络或稍后重试") from exc

    stdout = completed.stdout or ""
    if completed.returncode != 0 or not stdout.strip():
        message = _last_error(completed.stderr or stdout) or "解析失败"
        raise EngineError(friendly_error(message))

    try:
        info = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise EngineError("解析结果无法识别，可能该链接不是受支持的视频页面") from exc

    result = normalize_info(info, target_url)
    if url_note:
        result["note"] = url_note
        result["original_url"] = url
    return result


def _last_error(text: str) -> str | None:
    """提取最后一条错误，并把紧随其后的中文提示一起带上。

    yt-dlp 报错占一行、我们的提示占下一行，若只取最后一行会把提示误当成错误本身。
    """
    lines = text.splitlines()
    # 从后往前找最后一条 ERROR:，并收集其后紧跟的"提示："行
    for idx in range(len(lines) - 1, -1, -1):
        match = ERROR_LINE.match(lines[idx].strip())
        if not match:
            continue
        message = match.group(1).strip()
        hints = [
            line.strip()
            for line in lines[idx + 1: idx + 3]
            if line.strip().startswith("提示：")
        ]
        # 裸 "ERROR:" 情况下，提示本身就是最有用的信息
        if not message and hints:
            return hints[0][:800]
        combined = "\n".join([message, *hints]).strip()
        return combined[:800] if combined else None

    # 没有标准 ERROR 行时，退回到最后一行非空、且不是提示行的内容
    for line in reversed(lines):
        candidate = line.strip()
        if candidate and not candidate.startswith("提示："):
            return candidate[:800]
    return None


def engine_version() -> dict[str, Any]:
    """引擎与依赖环境自检。"""
    info: dict[str, Any] = {
        "engine": config.ENGINE,
        "version": None,
        "ffmpeg": None,
        "python": None,
        "js_runtime": detect_js_runtime(),
        "ok": False,
        "error": None,
    }
    try:
        completed = subprocess.run(
            [*config.ytdlp_command(), "--version"],
            capture_output=True, text=True, timeout=30, errors="replace",
        )
        if completed.returncode == 0:
            info["version"] = (completed.stdout or "").strip().splitlines()[-1]
            info["ok"] = True
        else:
            info["error"] = _last_error(completed.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        info["error"] = str(exc)

    ffmpeg = config.ffmpeg_path()
    if ffmpeg:
        try:
            completed = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=15)
            first = (completed.stdout or "").splitlines()[0]
            info["ffmpeg"] = first.replace("ffmpeg version ", "").split(" Copyright")[0]
        except (OSError, subprocess.TimeoutExpired):
            info["ffmpeg"] = "available"
    return info


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------

RESULT_TEMPLATE = "after_move:%(filepath)s"


def build_download_args(
    url: str,
    opts: dict[str, Any],
    result_file: Path,
    download_dir: Path,
) -> tuple[list[str], dict[str, Any]]:
    """构造完整下载命令。"""
    fmt_args, normalized = build_format_args(opts)
    args = [
        *_base_args(),
        *_ffmpeg_args(),
        "--progress-template", PROGRESS_TEMPLATE,
        "--print-to-file", RESULT_TEMPLATE, str(result_file),
        "-o", output_template(download_dir),
        "--paths", f"temp:{download_dir / '.part'}",
        "--no-mtime",  # 避免修改时间被源站时间覆盖，便于按时间排序
        *fmt_args,
        url,
    ]
    return args, normalized


def read_result_files(path: Path) -> list[str]:
    """读取 --print-to-file 写出的全部最终文件路径。

    播放列表/多P 下载会写入多行，必须全部取回，否则无法判断是否有条目失败。
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in content.splitlines() if line.strip()]


def read_result_file(path: Path) -> str | None:
    """读取最后一次写入的最终文件路径（兼容单文件场景）。"""
    files = read_result_files(path)
    return files[-1] if files else None


def guess_result_file(download_dir: Path, since: float, before_files: set[str]) -> str | None:
    """兜底：用下载前后目录快照差集推断产物（兼容分P、播放列表）。"""
    candidates: list[tuple[float, str]] = []
    try:
        for entry in download_dir.iterdir():
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if entry.name in before_files:
                continue
            if entry.suffix in (".part", ".ytdl", ".temp"):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime >= since - 5:
                candidates.append((mtime, str(entry)))
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def snapshot_dir(directory: Path) -> set[str]:
    try:
        return {e.name for e in directory.iterdir() if e.is_file()}
    except OSError:
        return set()


def iter_lines(text: str) -> Iterator[str]:
    for line in text.splitlines():
        yield line


def tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text


def now() -> float:
    return time.time()
