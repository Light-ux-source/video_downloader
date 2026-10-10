"""HTTP 服务：标准库实现，零第三方依赖。

路由：
  GET  /                          → 前端 SPA
  GET  /api/health                → 引擎自检、并发、磁盘
  GET  /api/presets               → 画质预设与音频格式
  POST /api/probe                 → 解析视频/播放列表
  GET  /api/tasks                 → 任务列表
  POST /api/tasks                 → 批量创建下载任务
  GET  /api/tasks/{id}            → 单个任务详情（含日志）
  GET  /api/tasks/{id}/events     → SSE 实时进度流
  POST /api/tasks/{id}/cancel     → 取消任务
  POST /api/tasks/{id}/retry      → 重试任务
  DELETE /api/tasks/{id}          → 删除记录
  POST /api/reveal                → 在访达中显示
  POST /api/reveal-dir            → 打开下载目录
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import config, ytdlp
from .tasks import (
    STATE_CANCELED,
    STATE_DONE,
    STATE_ERROR,
    TERMINAL_STATES,
    TaskManager,
)

MAX_BODY = 2 * 1024 * 1024
MAX_URLS = 50
TASK_ID_RE = re.compile(r"^/api/tasks/([A-Za-z0-9_-]{1,64})(/events|/cancel|/retry)?$")
URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


class BadRequest(ValueError):
    pass


def _json_default(value: Any) -> Any:
    return str(value)


class Handler(BaseHTTPRequestHandler):
    server_version = "VideoHarbor/1.0"
    protocol_version = "HTTP/1.1"  # 支持 keep-alive 与流式响应
    manager: TaskManager
    started_at: float = time.time()

    # ------------------------------------------------------------------ 工具
    def log_message(self, fmt: str, *args: Any) -> None:  # 精简访问日志
        if os.environ.get("VH_VERBOSE"):
            super().log_message(fmt, *args)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status=status)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise BadRequest("请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequest("请求体不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise BadRequest("请求体必须是 JSON 对象")
        return data

    @staticmethod
    def _safe_path(raw: str) -> Path | None:
        """仅允许操作下载目录内的文件。"""
        if not raw:
            return None
        path = Path(raw).expanduser()
        try:
            resolved = path.resolve()
            root = config.DOWNLOAD_DIR.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        return resolved

    # ------------------------------------------------------------------ 路由
    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/health":
                return self._api_health()
            if path == "/api/presets":
                return self._api_presets()
            if path == "/api/tasks":
                return self._api_task_list()
            match = TASK_ID_RE.match(path)
            if match:
                task_id, suffix = match.group(1), match.group(2)
                if suffix == "/events":
                    return self._api_task_events(task_id)
                return self._api_task_detail(task_id)
            return self._serve_static(path)
        except BadRequest as exc:
            self._send_error_json(400, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:  # 兜底，避免单个请求打挂服务
            self._send_error_json(500, f"服务内部错误：{exc}")

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/probe":
                return self._api_probe()
            if path == "/api/tasks":
                return self._api_task_create()
            if path == "/api/reveal":
                return self._api_reveal()
            if path == "/api/reveal-dir":
                return self._api_reveal_dir()
            match = TASK_ID_RE.match(path)
            if match and match.group(2) in ("/cancel", "/retry"):
                return self._api_task_action(match.group(1), match.group(2))
            self._send_error_json(404, "接口不存在")
        except BadRequest as exc:
            self._send_error_json(400, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_error_json(500, f"服务内部错误：{exc}")

    def do_DELETE(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        match = TASK_ID_RE.match(path)
        if match and not match.group(2):
            removed = self.manager.remove(match.group(1))
            return self._send_json({"ok": removed})
        self._send_error_json(404, "接口不存在")

    # ------------------------------------------------------------------ 接口实现
    def _api_health(self) -> None:
        engine = ytdlp.engine_version()
        disk = config.disk_free_bytes()
        self._send_json({
            "ok": True,
            "engine": engine,
            "ffmpeg_ok": bool(engine.get("ffmpeg")),
            "js_runtime": engine.get("js_runtime"),
            "concurrency": self.manager.concurrency,
            "tasks": self.manager.stats(),
            "disk_free": disk,
            "disk_free_text": ytdlp._human_size(disk),
            "download_dir": str(config.DOWNLOAD_DIR),
            "uptime": time.time() - self.started_at,
        })

    def _api_presets(self) -> None:
        self._send_json({
            "ok": True,
            "presets": ytdlp.QUALITY_PRESETS,
            "audio_formats": ytdlp.AUDIO_FORMATS,
        })

    def _api_probe(self) -> None:
        data = self._read_json()
        url = str(data.get("url") or "").strip()
        if not URL_RE.match(url):
            raise BadRequest("请输入以 http:// 或 https:// 开头的视频链接")
        options = self._probe_options(data)
        try:
            info = ytdlp.probe(url, options)
        except ytdlp.EngineError as exc:
            return self._send_json({"ok": False, "error": str(exc)}, status=200)
        self._send_json({"ok": True, "info": info})

    @staticmethod
    def _probe_options(data: dict[str, Any]) -> dict[str, Any]:
        options: dict[str, Any] = {"playlist": bool(data.get("playlist"))}
        for key in ("cookies_file", "cookies_from_browser", "proxy"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                options[key] = value.strip()
        return options

    def _api_task_list(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        limit = min(500, max(1, int((query.get("limit") or ["200"])[0] or 200)))
        self._send_json({
            "ok": True,
            "tasks": self.manager.list(limit=limit),
            "stats": self.manager.stats(),
        })

    def _api_task_detail(self, task_id: str) -> None:
        task = self.manager.get(task_id)
        if not task:
            return self._send_error_json(404, "任务不存在")
        self._send_json({"ok": True, "task": task.to_dict(include_log=True)})

    def _api_task_create(self) -> None:
        data = self._read_json()
        raw_urls = data.get("urls")
        if isinstance(raw_urls, str):
            raw_urls = re.split(r"[\s,]+", raw_urls)
        if not raw_urls:
            single = str(data.get("url") or "").strip()
            raw_urls = [single] if single else []
        if not isinstance(raw_urls, list):
            raise BadRequest("urls 必须是数组或字符串")

        urls: list[str] = []
        for item in raw_urls:
            candidate = str(item).strip()
            if not candidate:
                continue
            if not URL_RE.match(candidate):
                raise BadRequest(f"链接格式不正确：{candidate[:60]}")
            if candidate not in urls:
                urls.append(candidate)
        if not urls:
            raise BadRequest("请至少提供一个视频链接")
        if len(urls) > MAX_URLS:
            raise BadRequest(f"一次最多提交 {MAX_URLS} 个链接")

        options = self._download_options(data)
        created = []
        for url in urls:
            task = self.manager.create(url, dict(options))
            created.append(task.to_dict())
        self._send_json({"ok": True, "tasks": created}, status=201)

    @staticmethod
    def _download_options(data: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "preset", "custom_format", "audio_only", "audio_format", "subtitles",
            "subtitle_langs", "auto_subs", "embed_subs", "playlist", "playlist_items",
            "cookies_file", "cookies_from_browser", "proxy", "limit_rate",
            "concurrent_fragments", "chunk_size", "skip_existing", "sponsorblock",
        }
        options = {k: v for k, v in data.items() if k in allowed}
        # 允许传自定义下载目录，但必须落在下载根目录内
        if data.get("download_dir"):
            safe = Handler._safe_path(str(data["download_dir"]))
            if safe:
                options["download_dir"] = str(safe)
        return options

    def _api_task_action(self, task_id: str, action: str) -> None:
        task = self.manager.get(task_id)
        if not task:
            return self._send_error_json(404, "任务不存在")
        if action == "/cancel":
            ok = self.manager.cancel(task_id)
            return self._send_json({"ok": ok, "task": task.to_dict()})
        if action == "/retry":
            if task.state not in TERMINAL_STATES:
                raise BadRequest("任务仍在进行中，无法重试")
            new_task = self.manager.retry(task_id)
            return self._send_json({"ok": bool(new_task), "task": new_task.to_dict() if new_task else None})
        self._send_error_json(404, "接口不存在")

    def _api_task_events(self, task_id: str) -> None:
        task = self.manager.get(task_id)
        if not task:
            return self._send_error_json(404, "任务不存在")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        sub = self.manager.subscribe(task_id)
        try:
            self._sse_write({"type": "state", "task": task.to_dict()})
            last = time.time()
            while True:
                current = self.manager.get(task_id)
                if current is None:
                    self._sse_write({"type": "removed"})
                    break
                try:
                    payload = sub.get(timeout=5)
                    self._sse_write({"type": "update", "task": payload})
                    last = time.time()
                except queue.Empty:
                    if time.time() - last >= 10:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last = time.time()
                if current.state in TERMINAL_STATES:
                    # 终态：确保前端收到最后一次快照后关闭
                    self._sse_write({"type": "update", "task": current.to_dict()})
                    self._sse_write({"type": "end"})
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.manager.unsubscribe(task_id, sub)

    def _sse_write(self, payload: dict[str, Any]) -> None:
        body = f"data: {json.dumps(payload, ensure_ascii=False, default=_json_default)}\n\n".encode("utf-8")
        self.wfile.write(body)
        self.wfile.flush()

    def _api_reveal(self) -> None:
        data = self._read_json()
        target = self._safe_path(str(data.get("path") or ""))
        if not target or not target.exists():
            raise BadRequest("文件不存在或不在下载目录内")
        self._reveal(target, select=True)
        self._send_json({"ok": True})

    def _api_reveal_dir(self) -> None:
        data = self._read_json()
        raw = str(data.get("path") or "").strip()
        target = self._safe_path(raw) if raw else config.DOWNLOAD_DIR
        if not target:
            target = config.DOWNLOAD_DIR
        if target.is_file():
            target = target.parent
        target.mkdir(parents=True, exist_ok=True)
        self._reveal(target, select=False)
        self._send_json({"ok": True, "path": str(target)})

    @staticmethod
    def _reveal(path: Path, select: bool) -> None:
        """macOS 用访达打开；其他平台尽力而为。"""
        try:
            if os.uname().sysname == "Darwin":
                args = ["open", "-R", str(path)] if select else ["open", str(path)]
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif os.name == "nt":
                subprocess.Popen(["explorer", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent if select else path)])
        except OSError:
            pass

    # ------------------------------------------------------------------ 静态资源
    def _serve_static(self, path: str) -> None:
        static_dir = config.STATIC_DIR
        if path in ("/", ""):
            path = "/index.html"
        relative = urllib.parse.unquote(path).lstrip("/")
        target = (static_dir / relative).resolve()
        try:
            target.relative_to(static_dir.resolve())
        except ValueError:
            return self._send_error_json(403, "非法路径")

        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # SPA 兜底
            index = static_dir / "index.html"
            if not index.is_file():
                return self._send_not_built()
            target = index

        content_type, _ = mimetypes.guess_type(str(target))
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type or 'application/octet-stream'}")
        self.send_header("Content-Length", str(len(body)))
        # 带 hash 的产物长缓存，index.html 不缓存
        if target.name == "index.html":
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(body)

    def _send_not_built(self) -> None:
        html = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>VideoHarbor · 前端未构建</title>
<style>body{background:#08090D;color:#E6EDF3;font-family:ui-monospace,Menlo,monospace;
display:grid;place-items:center;height:100vh;margin:0}code{color:#22D3EE}</style>
<div><h1>前端产物缺失</h1>
<p>请在项目目录执行：</p>
<p><code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p>然后刷新本页。</p></div></html>"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(manager: TaskManager) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"manager": manager})
    httpd = ThreadingHTTPServer((config.HOST, config.PORT), handler)
    httpd.daemon_threads = True
    return httpd
