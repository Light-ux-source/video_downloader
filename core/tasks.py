"""任务队列：线程池调度、进度聚合、事件广播（供 SSE 使用）。"""
from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, ytdlp

# 任务状态机
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_CANCELED = "canceled"
TERMINAL_STATES = {STATE_DONE, STATE_ERROR, STATE_CANCELED}

# 阶段（前端用于展示"正在做什么"）
PHASE_QUEUED = "queued"
PHASE_PREPARING = "preparing"
PHASE_DOWNLOADING = "downloading"
PHASE_MERGING = "merging"
PHASE_EMBEDDING = "embedding"
PHASE_CONVERTING = "converting"
PHASE_FINALIZING = "finalizing"
PHASE_DONE = "done"
PHASE_FAILED = "failed"

PHASE_TEXT = {
    PHASE_QUEUED: "排队中",
    PHASE_PREPARING: "解析链接",
    PHASE_DOWNLOADING: "下载中",
    PHASE_MERGING: "合流音视频",
    PHASE_EMBEDDING: "内嵌字幕/封面",
    PHASE_CONVERTING: "转码中",
    PHASE_FINALIZING: "收尾",
    PHASE_DONE: "已完成",
    PHASE_FAILED: "失败",
}

# 多策略重试之间的退避基数（秒）：第 N 次重试前等待 N*基数
RETRY_BACKOFF_SECONDS = 5.0

def fetch_to_file(task: "Task", fetch, url, target: Path, index: int) -> bool:
    """下载单个文件，逐条链接容错；成功返回 True。"""
    if not url:
        return False
    try:
        data = fetch(url)
    except Exception as exc:  # noqa: BLE001
        task.log.append(f"# 第 {index} 个文件下载失败：{exc}")
        return False
    if not data:
        return False
    try:
        target.write_bytes(data)
    except OSError as exc:
        task.log.append(f"# 第 {index} 个文件写入失败：{exc}")
        return False
    task.downloaded_bytes += len(data)
    return True


def _probe_streams(path: Path) -> list[str]:
    """返回媒体文件里的流类型列表（如 ['video', 'audio']）；失败返回空列表。"""
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.exists():
        return []
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def compose_slideshow_video(
    images: list[Path],
    music: Path | None,
    output: Path,
    duration: float,
    log: list[str],
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> tuple[bool, str]:
    """用 ffmpeg 把「图片轮播 + 配乐」合成为一个 mp4。

    竖屏 1080x1920（图片等比缩放后居中留黑边），H.264 + AAC。

    设计要点（都是为了"绝不静默丢配乐"）：
      * 先校验配乐文件确实是可解码音频，坏文件立即报错而不是合出无声视频
      * 显式 `-map` 指定视频与音频流，避免默认流选择把配乐丢掉
      * 合完再 ffprobe 校验产物**确实含音轨**；没有就换方案重试
      * 仍然没有音轨则返回失败（调用方会告知用户），而不是当作成功
    """
    import shutil
    import subprocess
    import tempfile

    if not images:
        return False, "没有可用图片"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False, "未找到 ffmpeg（合成功能需要它：brew install ffmpeg）"

    want_audio = bool(music and Path(music).exists())
    if want_audio and "audio" not in _probe_streams(Path(music)):
        # 配乐文件损坏/不是音频：明确指出，避免合出无声视频还报成功
        return False, "配乐文件无法解码（下载可能不完整）"
    if not want_audio:
        log.append("# 未获取到配乐，将生成无声视频")

    per_image = max(duration / len(images), 0.5)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},format=yuv420p"
    )
    seg_dir = Path(tempfile.mkdtemp(prefix="vh_slide_"))
    try:
        # ---- 1) 每张图编码成固定时长的无声片段 ----
        segments: list[Path] = []
        for idx, image in enumerate(images, 1):
            segment = seg_dir / f"seg{idx}.mp4"
            result = subprocess.run(
                [ffmpeg, "-y", "-v", "error",
                 "-loop", "1", "-framerate", str(fps), "-t", f"{per_image:.3f}", "-i", str(image),
                 "-vf", vf, "-an",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                 str(segment)],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0 or not segment.exists():
                return False, f"第 {idx} 张图编码失败：{(result.stderr or '')[:160]}"
            segments.append(segment)

        list_file = seg_dir / "list.txt"
        list_file.write_text(
            "".join(f"file '{seg.as_posix()}'\n" for seg in segments), encoding="utf-8"
        )

        # ---- 2) 拼接 + 混入配乐（显式 map，两种封装依次尝试）----
        attempts: list[tuple[str, list[str]]] = []
        if want_audio:
            attempts.append(("copy", [
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-af", "apad",           # 配乐短于视频时补静音，而不是截断
                "-shortest",
            ]))
            attempts.append(("reencode", [
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-af", "apad",
                "-shortest",
            ]))
        else:
            attempts.append(("silent", ["-map", "0:v:0", "-c:v", "copy", "-an"]))

        last_error = ""
        for label, extra in attempts:
            cmd = [ffmpeg, "-y", "-v", "error",
                   "-f", "concat", "-safe", "0", "-i", str(list_file)]
            if want_audio:
                cmd += ["-i", str(music)]
            cmd += extra + ["-movflags", "+faststart", "-t", f"{duration:.3f}", str(output)]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            except subprocess.TimeoutExpired:
                last_error = "ffmpeg 执行超时"
                continue
            if result.returncode != 0 or not output.exists():
                last_error = (result.stderr or "ffmpeg 合并失败")[:200]
                continue
            # 校验产物：要求音频真的在里面
            streams = _probe_streams(output)
            if "video" not in streams:
                last_error = "产物缺少视频流"
                continue
            if want_audio and "audio" not in streams:
                last_error = f"{label} 方案合成后仍丢失音轨"
                log.append(f"# 音轨校验失败（{label}），换方案重试：{last_error}")
                try:
                    output.unlink()
                except OSError:
                    pass
                continue
            log.append(
                f"# 已合成视频：{output.name}（{len(images)} 张图 · {duration:.1f}s"
                + ("· 含配乐" if want_audio else "· 无声") + f"· {label}）"
            )
            return True, ""
        return False, last_error or "合成失败"
    finally:
        shutil.rmtree(seg_dir, ignore_errors=True)


def _safe_folder_name(raw: str) -> str:
    """把标题变成安全的目录名（去掉路径分隔符与非法字符）。"""
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", str(raw or "")).strip(" .")
    return (name[:80] or "douyin")


MAX_LOG_LINES = 400

# 判断失败是否来自字幕后处理（这类失败不该让视频本体一起失败）
_SUBTITLE_FAILURE_HINTS = (
    "subtitle",
    "subtitlesconvertor",
    "embedsubtitle",
    "error opening input files",
    "invalid data found when processing input",
)


def _is_subtitle_failure(message: str) -> bool:
    text = (message or "").lower()
    return any(hint in text for hint in _SUBTITLE_FAILURE_HINTS)


@dataclass
class Task:
    id: str
    url: str
    options: dict[str, Any]
    state: str = STATE_QUEUED
    phase: str = PHASE_QUEUED
    title: str | None = None
    thumbnail: str | None = None
    uploader: str | None = None
    duration: int | None = None
    quality_label: str | None = None

    percent: float | None = None
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None

    message: str | None = None
    error: str | None = None
    filepath: str | None = None
    filesize: int | None = None
    warnings: int = 0
    last_warning: str | None = None
    # 播放列表/多P 进度与产物清单
    playlist_total: int | None = None
    item_index: int = 0
    files: list[str] = field(default_factory=list)
    incomplete: bool = False
    failed_items: int = 0

    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    work_dir: str | None = None
    log: list[str] = field(default_factory=list)

    def to_dict(self, include_log: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "url": self.url,
            "state": self.state,
            "phase": self.phase,
            "phase_text": PHASE_TEXT.get(self.phase, self.phase),
            "title": self.title,
            "thumbnail": self.thumbnail,
            "uploader": self.uploader,
            "duration": self.duration,
            "quality_label": self.quality_label,
            "percent": self.percent,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed": self.speed,
            "eta": self.eta,
            "message": self.message,
            "error": self.error,
            "filepath": self.filepath,
            "filesize": self.filesize,
            "warnings": self.warnings,
            "last_warning": self.last_warning,
            "playlist_total": self.playlist_total,
            "item_index": self.item_index,
            "file_count": len(self.files),
            "files": [Path(f).name for f in self.files],
            "incomplete": self.incomplete,
            "failed_items": self.failed_items,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
            "elapsed": (self.finished_at or time.time()) - (self.started_at or self.created_at),
        }
        if include_log:
            data["log"] = self.log[-MAX_LOG_LINES:]
        return data


class TaskManager:
    """进程内任务管理器（不依赖数据库）。

    - 下载线程池：限制同时下载数
    - 解析线程池：单独限流，保证"边下边解析"不互相饿死
    - 事件订阅：每个任务一份订阅者队列，SSE 消费
    """

    def __init__(self, concurrency: int | None = None) -> None:
        self.concurrency = concurrency or config.CONCURRENCY
        self.tasks: dict[str, Task] = {}
        self.order: list[str] = []
        self._lock = threading.RLock()
        self._download_pool = ThreadPoolExecutor(
            max_workers=self.concurrency, thread_name_prefix="vh-dl"
        )
        self._probe_pool = ThreadPoolExecutor(
            max_workers=config.PROBE_WORKERS, thread_name_prefix="vh-probe"
        )
        self._procs: dict[str, ytdlp.EngineProcess] = {}
        self._subscribers: dict[str, list[Any]] = {}

    # ------------------------------------------------------------------ 创建
    def create(self, url: str, options: dict[str, Any], meta: dict[str, Any] | None = None) -> Task:
        meta = meta or {}
        task = Task(id=uuid.uuid4().hex[:12], url=url, options=options)
        task.title = meta.get("title")
        task.thumbnail = meta.get("thumbnail")
        task.uploader = meta.get("uploader")
        task.duration = meta.get("duration")

        preset = ytdlp.preset_by_id(str(options.get("preset") or "best"))
        if options.get("custom_format"):
            task.quality_label = f"自定义 {options['custom_format']}"
        elif preset.get("audio_only"):
            task.quality_label = f"仅音频 · {(options.get('audio_format') or 'mp3').upper()}"
        else:
            task.quality_label = preset["label"]
        if options.get("playlist"):
            task.quality_label = f"{task.quality_label} · 播放列表"
        if options.get("subtitles") and not preset.get("audio_only"):
            task.quality_label = f"{task.quality_label} · 内嵌字幕"

        with self._lock:
            self.tasks[task.id] = task
            self.order.append(task.id)
            self._trim_history()
        self._emit(task)
        self._download_pool.submit(self._run, task.id)
        return task

    def _trim_history(self) -> None:
        limit = config.TASK_HISTORY_LIMIT
        if len(self.order) <= limit:
            return
        extra = len(self.order) - limit
        for task_id in list(self.order):
            if extra <= 0:
                break
            task = self.tasks.get(task_id)
            if task and task.state in TERMINAL_STATES:
                self.order.remove(task_id)
                self.tasks.pop(task_id, None)
                self._subscribers.pop(task_id, None)
                extra -= 1

    # ------------------------------------------------------------------ 查询
    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self.tasks.get(task_id)

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(reversed(self.order))[:limit]
            return [self.tasks[i].to_dict() for i in ids if i in self.tasks]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            tasks = list(self.tasks.values())
        return {
            "total": len(tasks),
            "running": sum(1 for t in tasks if t.state == STATE_RUNNING),
            "queued": sum(1 for t in tasks if t.state == STATE_QUEUED),
            "done": sum(1 for t in tasks if t.state == STATE_DONE),
            "error": sum(1 for t in tasks if t.state == STATE_ERROR),
            "canceled": sum(1 for t in tasks if t.state == STATE_CANCELED),
            "concurrency": self.concurrency,
        }

    # ------------------------------------------------------------------ 控制
    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task or task.state in TERMINAL_STATES:
                return False
            proc = self._procs.get(task_id)
            if task.state == STATE_QUEUED and proc is None:
                # 尚未开始：直接标记取消
                task.state = STATE_CANCELED
                task.phase = PHASE_FAILED
                task.message = "已取消（排队中）"
                task.finished_at = time.time()
                task.updated_at = task.finished_at
        if proc is not None:
            task.message = "正在取消…"
            self._emit(task)
            proc.cancel()
        self._emit(task)
        return True

    def retry(self, task_id: str) -> Task | None:
        with self._lock:
            old = self.tasks.get(task_id)
            if not old:
                return None
            meta = {
                "title": old.title,
                "thumbnail": old.thumbnail,
                "uploader": old.uploader,
                "duration": old.duration,
            }
            options = dict(old.options)
            url = old.url
        return self.create(url, options, meta)

    def remove(self, task_id: str) -> bool:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task or task.state not in TERMINAL_STATES:
                return False
            self.tasks.pop(task_id, None)
            if task_id in self.order:
                self.order.remove(task_id)
            self._subscribers.pop(task_id, None)
        return True

    def shutdown(self) -> None:
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            proc.cancel()
        self._download_pool.shutdown(wait=False, cancel_futures=True)
        self._probe_pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ 解析
    def probe_async(self, url: str, options: dict[str, Any]) -> Any:
        return self._probe_pool.submit(ytdlp.probe, url, options)

    # ------------------------------------------------------------------ 订阅
    def subscribe(self, task_id: str) -> Any:
        import queue

        q: Any = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: Any) -> None:
        with self._lock:
            subs = self._subscribers.get(task_id)
            if subs and q in subs:
                subs.remove(q)

    def _emit(self, task: Task) -> None:
        payload = task.to_dict()
        with self._lock:
            subs = list(self._subscribers.get(task.id, []))
        for q in subs:
            try:
                q.put_nowait(payload)
            except Exception:  # 队列满：丢弃最旧的，保证最新状态能送达
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass

    # ------------------------------------------------------------------ 执行
    def _run(self, task_id: str) -> None:
        """线程池入口：任何未预期异常都必须落到任务状态上，绝不能静默吞掉。"""
        with self._lock:
            task = self.tasks.get(task_id)
        if not task:
            return
        try:
            self._run_inner(task, task_id)
        except Exception as exc:  # noqa: BLE001 - 兜底
            import traceback

            task.log.append(f"# 内部异常：{exc}")
            task.log.extend(traceback.format_exc().splitlines()[-8:])
            if task.state not in TERMINAL_STATES:
                self._fail(task, f"内部错误：{exc}")

    def _run_inner(self, task: Task, task_id: str) -> None:
        if task.state == STATE_CANCELED:
            return

        task.state = STATE_RUNNING
        task.phase = PHASE_PREPARING
        task.started_at = time.time()
        task.updated_at = task.started_at
        task.message = "准备下载"
        self._emit(task)

        download_dir = Path(task.options.get("download_dir") or config.DOWNLOAD_DIR)
        try:
            download_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._fail(task, f"无法创建下载目录：{exc}")
            return
        task.work_dir = str(download_dir)

        # 已知不受支持的链接形态先纠正（例如抖音 /note/ → /video/）
        normalized_url, url_note = ytdlp.normalize_url(task.url)
        if url_note:
            task.log.append(f"# {url_note}")
            task.url = normalized_url

        # 播放列表/多P：先查一次条目总数，用于展示进度与判定"部分完成"
        if task.options.get("playlist"):
            try:
                info = ytdlp.probe(
                    task.url,
                    {"playlist": True, "cookies_file": task.options.get("cookies_file"),
                     "cookies_from_browser": task.options.get("cookies_from_browser"),
                     "proxy": task.options.get("proxy")},
                    timeout=45,
                )
                if info.get("kind") == "playlist":
                    task.playlist_total = info.get("entry_count") or len(info.get("entries") or [])
                    if not task.title:
                        task.title = info.get("title")
            except Exception as exc:  # 解析失败不影响下载本身
                task.log.append(f"# 未能获取播放列表条目数：{exc}")

        result_file = config.CACHE_DIR / f"result_{task.id}.txt"
        try:
            result_file.unlink()
        except OSError:
            pass

        before_files = ytdlp.snapshot_dir(download_dir)
        started = time.time()

        # 抖音：yt-dlp 撞签名墙，先换成免 Cookie 直链（见 core/douyin.py）
        self._resolve_douyin_direct(task)

        # 抖音图文帖：直接抓图（图片不是流媒体，无需 yt-dlp 参与）
        if task.options.get("_douyin_images"):
            self._download_douyin_gallery(task, download_dir)
            return

        # 多策略执行：网络抖动/编码流被拒时，逐级降级重试，显著提高成功率
        audio_only = bool(
            ytdlp.preset_by_id(str(task.options.get("preset") or "best")).get("audio_only")
        )
        strategies = ytdlp.retry_strategies(task.options, audio_only)
        code = 0
        attempts = 0
        for index, (label, patch) in enumerate(strategies):
            attempts = index + 1
            if index:
                task.options = {**task.options, **patch}
                task.warnings += 1
                task.message = f"{label}（第 {index + 1} 次尝试）"
                task.error = None
                task.percent = None
                task.downloaded_bytes = 0
                task.updated_at = time.time()
                task.log.append(f"# 重试策略 {index + 1}/{len(strategies)}：{label}")
                # 清掉上一次失败留下的分片，避免继续撞同一条坏流
                removed = self._cleanup_fragments(download_dir, task.title or "")
                if removed:
                    task.log.append(f"# 已清理 {removed} 个残留分片文件")
                self._emit(task)
                # 退避等待：网络/代理抖动往往是瞬时的，立刻重试只会撞在同一时刻。
                # 最后一次失败不需要再等。
                if code != 0 and index < len(strategies) - 1:
                    wait = RETRY_BACKOFF_SECONDS * index
                    if wait > 0:
                        task.log.append(f"# 等待 {wait}s 后重试（避开瞬时网络抖动）")
                        self._emit(task)
                        # 分片睡眠，便于取消时能及时响应
                        for _ in range(int(wait * 2)):
                            if task.state == STATE_CANCELED:
                                return
                            time.sleep(0.5)

            code = self._spawn_engine(task, task_id, download_dir, result_file)
            if code is None:  # 参数错误 / 执行异常，已置为失败
                return
            if task.state == STATE_CANCELED:
                return
            if code == 0:
                break

            # 非 0 退出码：先确认是否其实已经拿到了最终产物
            existing = ytdlp.read_result_file(result_file)
            if not existing:
                existing = self._match_existing(
                    download_dir, task.title or "", started, audio_only, before_files
                )
            if existing and Path(existing).is_file():
                task.filepath = existing
                code = 0
                break
            if index == len(strategies) - 1:
                break
            task.log.append(f"# 第 {index + 1} 次尝试失败：{(task.error or f'退出码 {code}')[:160]}")

            # 永久性错误（链接失效 / 地区限制 / 需要登录等）重试无意义，直接结束
            if ytdlp.is_permanent_error(task.error or ""):
                task.log.append("# 判定为重试也无法解决的错误，停止后续策略")
                break

            # 字幕后处理失败（例如 B 站弹幕 XML 无法被 ffmpeg 内嵌）时，
            # 自动改成「不内嵌字幕」继续重试：保住视频本体，并明确告知用户，
            # 而不是让整个任务失败，也不是静默忽略。
            if task.options.get("subtitles") and _is_subtitle_failure(task.error or ""):
                task.options = {**task.options, "subtitles": False}
                task.log.append("# 字幕无法内嵌（源字幕格式不被 ffmpeg 支持），已改为不内嵌字幕继续下载")
                task.message = "字幕无法内嵌，改为仅下载视频"
                self._emit(task)

        if code != 0:
            self._fail(
                task,
                (task.error or f"yt-dlp 退出码 {code}")
                + (f"（已自动尝试 {attempts} 种策略）" if attempts > 1 else ""),
            )
            return

        # 成功：结果文件可能有多行（播放列表/多P），交给统一的收尾逻辑判定
        try:
            self._finalize(task, download_dir, result_file, started, before_files, audio_only)
        finally:
            try:
                result_file.unlink()
            except OSError:
                pass

    def _finalize(
        self,
        task: Task,
        download_dir: Path,
        result_file: Path,
        started: float,
        before_files: set[str],
        audio_only: bool,
    ) -> None:
        """收尾：定位产物、判定是否"部分完成"、落终态。

        单独成方法是为了可被单元测试直接驱动（并发/播放列表/抖动自愈等场景）。
        """
        recorded = ytdlp.read_result_files(result_file)
        produced = [p for p in recorded if Path(p).is_file()]
        if not produced:
            single = self._match_existing(
                download_dir, task.title or "", started, audio_only, before_files
            ) or ytdlp.guess_result_file(download_dir, started, before_files)
            produced = [single] if single else []

        for item in produced:
            if item not in task.files:
                task.files.append(item)
        task.item_index = len(task.files)

        missing = [p for p in recorded if not Path(p).is_file()]
        expected = int(task.playlist_total or 0)
        complete_count = len([p for p in task.files if Path(p).is_file()])

        # "Giving up" 计数只是线索：有些流在放弃后由后续策略/重试补齐了，
        # 因此先看"实际完成条目数是否达到预期"，达到就认为没有真实失败。
        if expected and complete_count >= expected:
            task.failed_items = 0
        elif complete_count and not missing and not expected and task.failed_items:
            # 单视频场景：产物已拿到，日志里的放弃记录属抖动后自愈
            task.failed_items = 0

        task.incomplete = bool(task.failed_items) or bool(missing) or (
            bool(expected) and complete_count < expected
        )

        # CDN 直链（抖音等）下载下来是乱码文件名，按任务标题重命名
        renamed = self._rename_to_title(task, produced, download_dir)
        if renamed:
            produced = renamed

        if produced:
            path = Path(produced[0])
            task.filepath = str(path)
            try:
                task.filesize = path.stat().st_size
            except OSError:
                task.filesize = None
            if not task.title:
                task.title = path.stem
            if task.incomplete:
                task.message = (
                    f"部分完成：成功 {complete_count} 个条目，另有条目未完成（可查看日志后重试）"
                )
            elif complete_count > 1:
                task.message = f"全部完成：共 {complete_count} 个条目"
            elif not task.message:
                task.message = "下载完成"
        else:
            task.message = "下载结束，但未定位到产物文件"
            task.incomplete = True

        task.state = STATE_DONE
        task.phase = PHASE_DONE
        task.percent = 100.0
        task.finished_at = time.time()
        task.updated_at = task.finished_at
        self._emit(task)

    def _resolve_douyin_direct(self, task: Task) -> None:
        """把抖音链接换成免 Cookie 的 CDN 直链。

        yt-dlp 的抖音提取器会撞签名墙（encrypt_data_miss / 403），
        因此这里改用直连通路；失败时保留原链接，让 yt-dlp 按常规流程报错。
        """
        from . import douyin

        original = task.options.get("_source_url") or task.url
        if not douyin.is_douyin_url(original):
            return
        try:
            info = douyin.extract(
                original,
                preset=str(task.options.get("preset") or "best"),
                proxy=ytdlp.sanitize_proxy(task.options.get("proxy")) if task.options.get("proxy") else None,
            )
        except douyin.DouyinError as exc:
            # 保留原链接继续走 yt-dlp（它大概率会报 needs cookies），
            # 但把真实原因写进日志，避免用户被上游的模糊报错误导。
            task.log.append(f"# 抖音直连通路失败：{exc}")
            task.last_warning = f"抖音直连通路失败：{exc}"
            return

        if not task.title:
            task.title = info.get("title")
        if not task.thumbnail:
            task.thumbnail = info.get("thumbnail")

        # 图文帖：本身没有视频流，但客户端播放的是「图片轮播 + 配乐」，
        # 因此这里把素材交给原生逻辑本地合成视频（不走 yt-dlp）。
        if info.get("kind") == "gallery" and info.get("images"):
            animated = int(info.get("animated_count") or 0)
            task.options = {
                **task.options,
                "_source_url": original,
                "_douyin_images": info["images"],
                "_douyin_headers": info.get("direct_headers") or {},
                "_douyin_music": info.get("music_url"),
                "_douyin_duration": info.get("video_duration"),
            }
            task.playlist_total = len(info["images"]) + (0 if animated == len(info["images"]) else 1)
            task.log.append(
                f"# 抖音图文帖：共 {len(info['images'])} 张原图"
                + (f"（含 {animated} 张实况动图）" if animated else "")
                + f"，配乐时长 {info.get('music_duration') or '未知'}s，将本地合成为视频"
            )
            self._emit(task)
            return

        task.options = {
            **task.options,
            "_source_url": original,
            "headers": info.get("direct_headers") or {},
        }
        task.url = info["direct_url"]
        task.playlist_total = None
        task.log.append(
            f"# 抖音直连通路成功：video_id={info.get('video_id')} "
            f"档位={info.get('ratio')}（yt-dlp 提取器不可用，已改用播放直链接口）"
        )
        self._emit(task)

    @staticmethod
    def _rename_to_title(task: Task, produced: list[str], download_dir: Path) -> list[str]:
        """把乱码文件名（CDN 直链的无意义 basename）改成任务标题。

        仅在标题已知、且当前文件名确实"不像标题"时执行；
        重名时自动追加序号，避免覆盖已有文件。
        """
        title = (task.title or "").strip()
        if not title or not produced:
            return produced

        safe = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", title).strip(" .")[:120]
        if not safe:
            return produced

        result: list[str] = []
        for raw in produced:
            path = Path(raw)
            if not path.is_file():
                result.append(raw)
                continue
            # 已经放在以标题命名的子目录里（如抖音图文帖的 图片N.jpeg）
            # → 不要改名，否则会破坏逐张编号
            if path.parent.name == safe:
                result.append(raw)
                continue
            # 文件名已经以标题开头 → 无需改名
            if path.stem.startswith(safe[:40]):
                result.append(raw)
                continue
            candidate = download_dir / f"{safe}{path.suffix}"
            index = 2
            while candidate.exists() and candidate != path:
                candidate = download_dir / f"{safe} ({index}){path.suffix}"
                index += 1
            try:
                path.rename(candidate)
                task.log.append(f"# 已按标题重命名：{path.name[:40]} → {candidate.name[:40]}")
                result.append(str(candidate))
            except OSError as exc:
                task.log.append(f"# 重命名失败（保留原文件名）：{exc}")
                result.append(raw)
        return result

    def _download_douyin_gallery(self, task: Task, download_dir: Path) -> None:
        """把抖音图文帖保存为视频。

        图文帖本身没有视频流（帖子级 video 只有封面，bit_rate 为空），
        但客户端播放的是一个"图片轮播 + 配乐"的视频。因此这里：

          1. 下载全部无水印原图（实测 `url_list` 的 .jpeg）
          2. 若某张图带动图（实况），下载它对应的 mp4（`images[].video.play_addr`）
          3. 生成一个幻灯片视频：图片 + 配乐，时长取抖音给的播放时长
             —— 全为动图时跳过合成，直接保存动画视频

        产物统一放在 `downloads/{标题}/` 下，与其他任务的收尾校验共用同一套逻辑。
        """
        import http.cookiejar
        import urllib.request

        from . import douyin

        images: list[dict[str, Any]] = list(task.options.get("_douyin_images") or [])
        headers = dict(task.options.get("_douyin_headers") or {})
        music_url = task.options.get("_douyin_music")
        duration = float(task.options.get("_douyin_duration") or 0) or max(len(images), 1) * 5.0

        folder = download_dir / _safe_folder_name(task.title or task.url)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._fail(task, f"无法创建目录：{exc}")
            return

        result_file = config.CACHE_DIR / f"result_{task.id}.txt"
        produced: list[str] = []
        failures: list[str] = []

        # 走任务里配置的代理与游客 cookie：
        # 抖音 CDN 在部分网络下必须经代理才通，否则素材（尤其配乐）会下载失败。
        proxy = ytdlp.sanitize_proxy(task.options.get("proxy")) if task.options.get("proxy") else None
        opener = douyin._build_opener(proxy, http.cookiejar.CookieJar())

        def fetch(url: str, timeout: int = 90) -> bytes:
            request = urllib.request.Request(url, headers=headers)
            with opener.open(request, timeout=timeout) as response:
                return response.read()

        task.phase = PHASE_DOWNLOADING
        all_animated = bool(images) and all(i.get("animated_url") for i in images)
        task.playlist_total = len(images) if all_animated else len(images) + 1
        self._emit(task)

        # ---- 1) 下载原图 / 动图视频 ----
        for index, image in enumerate(images, 1):
            if task.state == STATE_CANCELED:
                return
            animated = image.get("animated_url")
            task.message = f"下载第 {index}/{len(images)} 张" + ("（实况动图）" if animated else "原图")
            task.percent = round((index - 1) / max(task.playlist_total, 1) * 100, 2)
            self._emit(task)

            if animated:
                target = folder / f"动图{index}.mp4"
                written = fetch_to_file(task, fetch, animated, target, index)
                if written:
                    produced.append(str(target))
                else:
                    failures.append(f"第 {index} 张动图")
                continue

            target = folder / f"图片{index}.{image.get('ext') or 'jpg'}"
            written = fetch_to_file(task, fetch, image.get("url"), target, index)
            if written:
                produced.append(str(target))
            else:
                failures.append(f"第 {index} 张")

            task.item_index = index
            task.updated_at = time.time()
            self._emit(task)

        # ---- 2) 合成视频（图片 + 配乐） ----
        if not all_animated and produced:
            task.message = "正在合成视频（图片 + 配乐）"
            task.phase = PHASE_CONVERTING
            self._emit(task)

            music_path = None
            music_failed_reason = ""
            if music_url:
                music_path = folder / ".music.mp3"
                try:
                    data = fetch(music_url, timeout=120)
                    if not data:
                        raise ValueError("响应为空")
                    music_path.write_bytes(data)
                    task.log.append(f"# 配乐已下载：{len(data) / 1024:.1f}KB")
                except Exception as exc:  # noqa: BLE001
                    # 配乐拿不到会导致视频无声——必须让用户知道，不能静默降级
                    music_failed_reason = str(exc)[:160]
                    task.log.append(f"# ⚠ 配乐下载失败，视频将没有声音：{music_failed_reason}")
                    task.warnings += 1
                    music_path = None
            else:
                music_failed_reason = "该帖子未提供配乐"
                task.log.append("# ⚠ 未获取到配乐直链，视频将没有声音")

            video_path = folder / f"{_safe_folder_name(task.title or task.url)}.mp4"
            ok, err = compose_slideshow_video(
                [Path(p) for p in produced if p.endswith((".jpg", ".jpeg", ".webp", ".png"))],
                music_path,
                video_path,
                duration,
                task.log,
            )
            if ok:
                produced.insert(0, str(video_path))
                has_audio = "audio" in _probe_streams(video_path)
                if music_path and has_audio:
                    try:
                        music_path.unlink()   # 只有在音轨确实混入后才清理配乐
                    except OSError:
                        pass
                if not has_audio:
                    # 合成成功但没有声音：明确告知并计入未完成，避免"以为有配乐"
                    reason = music_failed_reason or "合成产物不含音轨"
                    task.log.append(f"# ⚠ 视频已生成但没有声音：{reason}")
                    task.warnings += 1
                    failures.append("配乐")
                    task.last_warning = f"视频无声：{reason}"
                elif music_failed_reason:
                    task.last_warning = f"配乐异常但已合成：{music_failed_reason}"
            else:
                failures.append("视频合成")
                task.log.append(f"# 视频合成失败：{err}")

            task.item_index = len(produced)
            task.updated_at = time.time()
            self._emit(task)

        # ---- 3) 收尾（复用统一校验） ----
        try:
            result_file.write_text("\n".join(produced) + "\n", encoding="utf-8")
        except OSError:
            pass
        if failures:
            task.log.append(f"# 失败项：{', '.join(failures[:6])}")
            task.failed_items = len(failures)

        self._finalize(task, folder, result_file, time.time() - 1, set(), audio_only=False)
        if not task.incomplete and produced:
            if all_animated:
                task.message = f"全部完成：共 {len(produced)} 个实况动图视频"
            else:
                has_video = any(p.endswith(".mp4") for p in produced)
                silent = has_video and "audio" not in _probe_streams(
                    Path(next(p for p in produced if p.endswith(".mp4")))
                )
                task.message = (
                    f"全部完成：共 {len(produced)} 个文件"
                    + ("（合成视频无声，见日志）" if silent else "（含合成视频与配乐）")
                )
            self._emit(task)

    def _spawn_engine(
        self, task: Task, task_id: str, download_dir: Path, result_file: Path
    ) -> int | None:
        """启动一次 yt-dlp 执行；返回退出码，参数/执行异常时返回 None。"""
        try:
            args, _normalized = ytdlp.build_download_args(
                task.url, task.options, result_file, download_dir
            )
        except Exception as exc:  # 参数构造失败（如自定义表达式非法）
            self._fail(task, f"参数错误：{exc}")
            return None

        # --print-to-file 是追加写入：每次执行前必须清空，否则会读到旧路径
        try:
            result_file.unlink()
        except OSError:
            pass

        task.log.append(f"$ {' '.join(args[1:])}")
        proc = ytdlp.EngineProcess(args)
        with self._lock:
            self._procs[task_id] = proc
        try:
            proc.run(on_line=lambda line: self._on_line(task, line))
        except Exception as exc:
            self._fail(task, f"执行异常：{exc}")
            return None
        finally:
            with self._lock:
                self._procs.pop(task_id, None)

        if proc.canceled:
            task.state = STATE_CANCELED
            task.phase = PHASE_FAILED
            task.message = "已取消"
            task.finished_at = time.time()
            task.updated_at = task.finished_at
            self._emit(task)
            return -1
        return proc.process.returncode if proc.process else -1

    @staticmethod
    def _match_existing(
        download_dir: Path,
        title: str,
        since: float,
        audio_only: bool = False,
        before_files: set[str] | None = None,
    ) -> str | None:
        """按标题 + 期望扩展名找"本次下载确实涉及"的产物。

        仅用于"文件已存在，跳过下载"场景：此时 yt-dlp 既不写结果文件也不更新 mtime。
        两道约束缺一不可，否则会把别的任务 / 别的画质的同名文件误判成本次产物：
          1. 扩展名必须符合本次任务类型（音频 vs 视频）
          2. 文件必须是本次新建的，或 mtime 落在本次任务时间窗内
        """
        if not title:
            return None
        allowed = (
            (".mp3", ".m4a", ".opus", ".flac", ".wav", ".aac", ".ogg")
            if audio_only
            else (".mp4", ".mkv", ".webm", ".mov", ".flv")
        )
        before = before_files or set()
        tolerance = 2.0
        candidates: list[tuple[float, str]] = []
        try:
            for entry in download_dir.iterdir():
                if not entry.is_file():
                    continue
                if not entry.name.startswith(title[:40]):
                    continue
                if entry.suffix.lower() not in allowed:
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                if stat.st_size < 4096:
                    continue
                if stat.st_mtime >= since - tolerance or entry.name not in before:
                    candidates.append((stat.st_mtime, str(entry)))
        except OSError:
            return None
        if not candidates:
            return None
        candidates.sort()
        return candidates[-1][1]

    @staticmethod
    def _cleanup_fragments(download_dir: Path, title: str) -> int:
        """清掉失败留下的分片/临时文件，避免下次重试继续撞同一条坏流。"""
        removed = 0
        try:
            for entry in list(download_dir.iterdir()):
                if not entry.is_file():
                    continue
                name = entry.name
                if title and not name.startswith(title[:40]):
                    continue
                if re.search(r"\.f\d+\.", name) or name.endswith((".part", ".ytdl", ".temp")):
                    try:
                        entry.unlink()
                        removed += 1
                    except OSError:
                        pass
        except OSError:
            return removed
        try:
            temp = download_dir / ".part"
            if temp.is_dir():
                for entry in list(temp.iterdir()):
                    if entry.is_file():
                        try:
                            entry.unlink()
                            removed += 1
                        except OSError:
                            pass
        except OSError:
            pass
        return removed

    def _on_line(self, task: Task, line: str) -> None:
        """处理一行 yt-dlp 输出：更新阶段、进度、日志、错误。"""
        kind, payload = ytdlp.classify_line(line) or ("log", line)
        now = time.time()
        changed = False

        if kind == "progress":
            task.percent = payload.get("percent")
            task.downloaded_bytes = payload.get("downloaded_bytes") or 0
            task.total_bytes = payload.get("total_bytes") or task.total_bytes
            task.speed = payload.get("speed")
            task.eta = payload.get("eta")
            task.updated_at = now
            # 进度事件按 300ms 节流即可，避免刷屏
            if now - getattr(task, "_last_emit", 0) >= 0.3:
                task._last_emit = now  # type: ignore[attr-defined]
                self._emit(task)
            return

        if kind == "destination":
            if task.phase in (PHASE_QUEUED, PHASE_PREPARING):
                task.phase = PHASE_DOWNLOADING
                task.message = "开始传输数据"
            # 视频条目的计数交给"合流完成"事件，这里不重复计数
            changed = True
        elif kind == "playlist_done":
            task.message = "播放列表已处理完毕"
            changed = True
        elif kind == "merge":
            if payload and payload not in task.files:
                task.files.append(str(payload))
                task.item_index = len(task.files)
            task.phase = PHASE_MERGING
            task.message = "音视频合流"
            task.percent = max(task.percent or 0.0, 99.0)
            changed = True
        elif kind == "embed_subs":
            task.phase = PHASE_EMBEDDING
            task.message = "内嵌字幕"
            changed = True
        elif kind == "embed_thumb":
            task.phase = PHASE_EMBEDDING
            task.message = "内嵌封面"
            changed = True
        elif kind == "extract_audio":
            task.phase = PHASE_CONVERTING
            task.message = "提取/转码音频"
            changed = True
        elif kind == "metadata":
            task.phase = PHASE_FINALIZING
            task.message = "写入元数据"
            changed = True
        elif kind == "log":
            match = re.search(r"Downloading (\d+) format\(s\)", line)
            if match and task.playlist_total is None and "playlist" in (task.options or {}):
                pass  # 条目总数由 playlist_count 提供，这里不覆盖
        elif kind == "already":
            task.message = "文件已存在，跳过下载"
            task.filepath = task.filepath or payload
            changed = True
        elif kind == "error":
            if str(payload).strip():
                task.error = str(payload)[:500]
                task.message = str(payload)[:160]
            else:
                task.error = task.error or "yt-dlp 报告了一个错误（详情见日志）"
            task.warnings += 1
            changed = True
        elif kind == "warning":
            if "Got error" in line:
                # 传输抖动会被 yt-dlp 自动重试：只累计次数并单独展示，不覆盖正常阶段提示
                task.warnings += 1
                task.last_warning = line.strip()[:200]  # type: ignore[attr-defined]
                changed = True
            if "Giving up after" in line:
                # 重试耗尽：说明确实有一个条目/流下载失败
                task.failed_items += 1
                changed = True

        if line.strip():
            task.log.append(line.strip()[:400])
            if len(task.log) > MAX_LOG_LINES * 2:
                del task.log[:MAX_LOG_LINES]

        task.updated_at = now
        if changed:
            self._emit(task)

    def _fail(self, task: Task, message: str) -> None:
        task.state = STATE_ERROR
        task.phase = PHASE_FAILED
        task.error = message
        task.message = message
        task.finished_at = time.time()
        task.updated_at = task.finished_at
        self._emit(task)
