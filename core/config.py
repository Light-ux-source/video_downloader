"""VideoHarbor 全局配置与路径工具。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _project_root() -> Path:
    """项目根目录（兼容直接从 app.py 启动）。"""
    return Path(os.environ.get("VH_ROOT") or Path(__file__).resolve().parent.parent)


ROOT = _project_root()
STATIC_DIR = Path(os.environ.get("VH_STATIC") or (ROOT / "static"))
DOWNLOAD_DIR = Path(os.environ.get("VH_DOWNLOAD_DIR") or (ROOT / "downloads"))
CACHE_DIR = Path(os.environ.get("VH_CACHE_DIR") or (ROOT / "runtime" / "cache"))
HOST = os.environ.get("VH_HOST", "127.0.0.1")
PORT = int(os.environ.get("VH_PORT", "8765"))
# 同时下载数（任务列表本身不限制条数，超出的排队）
CONCURRENCY = max(1, int(os.environ.get("VH_CONCURRENCY", "2")))
# 同时解析数（解析较耗网络，单独限流）
PROBE_WORKERS = max(1, int(os.environ.get("VH_PROBE_WORKERS", "3")))
TASK_HISTORY_LIMIT = int(os.environ.get("VH_TASK_HISTORY_LIMIT", "200"))
PROBE_TIMEOUT = int(os.environ.get("VH_PROBE_TIMEOUT", "90"))

ENGINE = "yt-dlp"
# 引擎最低可用版本（低于此版本时前端提示升级）
MIN_ENGINE_VERSION = "2024.01.01"


def ensure_dirs() -> None:
    for d in (STATIC_DIR, DOWNLOAD_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def python_executable() -> str:
    return sys.executable


def ytdlp_command() -> list[str]:
    """返回调用 yt-dlp 的命令前缀。"""
    return [sys.executable, "-m", "yt_dlp"]


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def disk_free_bytes(path: Path | None = None) -> int:
    usage = shutil.disk_usage(str(path or DOWNLOAD_DIR))
    return usage.free


def ffmpeg_dir() -> str | None:
    """返回 ffmpeg 所在目录，供 yt-dlp --ffmpeg-location 使用。"""
    exe = ffmpeg_path()
    return str(Path(exe).parent) if exe else None
