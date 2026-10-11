"""VideoHarbor 后端入口。

    ./run.sh               # 默认 http://127.0.0.1:8765
    ./run.sh --port 9000 --concurrency 3
"""
from __future__ import annotations

import argparse
import errno
import socket
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import config, server, ytdlp  # noqa: E402
from core.tasks import TaskManager  # noqa: E402

RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"

BANNER = r"""
 __     ___     _         _   _            _
 \ \   / (_) __| | ___  | | | | __ _ _ __| |__   ___  _ __
  \ \ / /| |/ _` |/ _ \ | |_| |/ _` | '__| '_ \ / _ \| '__|
   \ V / | | (_| |  __/ |  _  | (_| | |  | |_) | (_) | |
    \_/  |_|\__,_|\___| |_| |_|\__,_|_|  |_.__/ \___/|_|
"""


def describe_port_owner(host: str, port: int) -> str:
    """探测占用端口的服务是不是 VideoHarbor 自己，给出更准确的提示。"""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=3) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        if isinstance(data, dict) and "engine" in data:
            version = (data.get("engine") or {}).get("version") or "未知版本"
            return f"看起来是另一个 VideoHarbor 实例（yt-dlp {version}）"
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        pass
    return "被其它程序占用"


def check_port(host: str, port: int) -> str | None:
    """启动前预检端口；返回错误说明（None 表示可用）。

    放到任何初始化动作之前，好处是失败时进程干干净净，
    用户看到的是可操作的提示而不是一长串 traceback。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                owner = describe_port_owner(host, port)
                return (
                    f"端口 {port} 已被占用（{owner}）。\n"
                    f"    处理方式任选其一：\n"
                    f"      1) 换端口启动： {CYAN}./run.sh --port 8899{RESET}\n"
                    f"      2) 关掉旧实例： {CYAN}pkill -f 'app.py --port {port}'{RESET}\n"
                    f"      3) 查看占用者： {CYAN}lsof -nP -iTCP:{port} -sTCP:LISTEN{RESET}\n"
                    f"    提示：正常按 Ctrl+C 退出不会残留进程；用 Ctrl+Z 或直接关终端可能留下后台实例。"
                )
            if exc.errno in (errno.EACCES, errno.EPERM):
                return f"没有权限监听 {host}:{port}，请换用 1024 以上的端口（如 --port 8899）"
            if exc.errno == errno.EADDRNOTAVAIL:
                return f"地址 {host} 在本机不可用，请检查 --host 参数（局域网共享通常用 0.0.0.0）"
            return f"无法监听 {host}:{port}：{exc}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="VideoHarbor 视频下载神器")
    parser.add_argument("--host", default=config.HOST, help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=config.PORT, help="监听端口")
    parser.add_argument("--concurrency", type=int, default=config.CONCURRENCY, help="同时下载数")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    config.HOST = args.host
    config.PORT = args.port
    config.CONCURRENCY = max(1, args.concurrency)

    # 先做端口预检：避免初始化了一半才失败，也让报错可操作
    port_error = check_port(config.HOST, config.PORT)
    if port_error:
        print(f"{RED}✗ 启动失败：{RESET}{port_error}")
        return 1

    config.ensure_dirs()

    engine = ytdlp.engine_version()
    print(f"{CYAN}{BANNER}{RESET}")
    if engine["ok"]:
        print(f"  {GREEN}●{RESET} 下载引擎   yt-dlp {engine['version']}")
    else:
        print(f"  {RED}●{RESET} 下载引擎   不可用：{engine.get('error')}")
        print(f"    请先运行： {CYAN}./setup.sh{RESET}")
    ffmpeg = engine.get("ffmpeg")
    if ffmpeg:
        print(f"  {GREEN}●{RESET} 合流/转码  ffmpeg {ffmpeg}")
    else:
        print(f"  {YELLOW}●{RESET} 合流/转码  ffmpeg 缺失（高清合流与 MP3 转码不可用）")
    js_runtime = engine.get("js_runtime")
    if js_runtime:
        print(f"  {GREEN}●{RESET} JS 运行时  {Path(js_runtime).name}（YouTube 必需）")
    else:
        print(f"  {YELLOW}●{RESET} JS 运行时  未找到（YouTube 会报 “The page needs to be reloaded.”）")
        print(f"    安装： {CYAN}brew install node{RESET}")
    print(f"  {GREEN}●{RESET} 存储目录   {config.DOWNLOAD_DIR}")
    print(f"  {GREEN}●{RESET} 并发下载   {config.CONCURRENCY}")
    if not (config.STATIC_DIR / "index.html").is_file():
        print(f"  {YELLOW}●{RESET} 前端产物缺失，请执行： cd frontend && npm install && npm run build")

    manager = TaskManager(config.CONCURRENCY)
    try:
        httpd = server.create_server(manager)
    except OSError as exc:
        # 预检通过但真正 bind 时仍失败（例如预检后被别的进程抢占）
        manager.shutdown()
        if exc.errno == errno.EADDRINUSE:
            print(f"{RED}✗ 启动失败：{RESET}端口 {config.PORT} 在启动瞬间被占用，请重试或换端口。")
        else:
            print(f"{RED}✗ 启动失败：{RESET}无法监听 {config.HOST}:{config.PORT}：{exc}")
        return 1

    url = f"http://{config.HOST}:{config.PORT}"
    print(f"\n  {GREEN}▶{RESET} 服务已启动：{CYAN}{url}{RESET}")
    print(f"  {DIM}按 Ctrl+C 退出{RESET}\n")

    stopping = threading.Event()

    def shutdown(_signum: int | None = None, _frame: object = None) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print(f"\n{DIM}正在关闭：结束后台任务…{RESET}")
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        manager.shutdown()
        httpd.server_close()
        print(f"{DIM}已退出{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
