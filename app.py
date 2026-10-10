"""VideoHarbor 后端入口。

    ./run.sh               # 默认 http://127.0.0.1:8765
    ./run.sh --port 9000 --concurrency 3
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
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
    print(f"  {GREEN}●{RESET} 存储目录   {config.DOWNLOAD_DIR}")
    print(f"  {GREEN}●{RESET} 并发下载   {config.CONCURRENCY}")
    if not (config.STATIC_DIR / "index.html").is_file():
        print(f"  {YELLOW}●{RESET} 前端产物缺失，请执行： cd frontend && npm install && npm run build")

    manager = TaskManager(config.CONCURRENCY)
    httpd = server.create_server(manager)
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
