"""环境自查工具：检查运行 VideoHarbor 所需的依赖是否齐备。

用法：
    ./run.sh --py tools/verify_env.py                    # 只做本地检查
    ./run.sh --py tools/verify_env.py --url <视频链接>    # 额外做一次真实解析
    ./run.sh --py tools/verify_env.py --url <链接> --proxy 127.0.0.1:7897 --browser chrome
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config, ytdlp  # noqa: E402

OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"
CYAN = "\033[36m"


def section(title: str) -> None:
    print(f"\n{CYAN}== {title} ==\033[0m")


def check_proxy(proxy: str) -> bool:
    """确认代理端口可连，并尝试用它访问外网。"""
    host, _, port = proxy.replace("http://", "").replace("socks5://", "").partition(":")
    if not port:
        print(f"  {BAD} 代理地址格式应为 host:port，当前为 {proxy!r}")
        return False

    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=5):
            print(f"  {OK} {host}:{port} 端口可连接")
    except OSError as exc:
        print(f"  {BAD} {host}:{port} 无法连接：{exc}")
        return False

    # SOCKS 代理不便用 urllib 直测，这里只对 http 代理做连通性验证
    if proxy.startswith("socks"):
        print(f"  {WARN} SOCKS 代理仅校验了端口，未做外网连通测试")
        return True

    full = ytdlp.sanitize_proxy(proxy) or proxy
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": full, "https": full})
        )
        start = time.time()
        with opener.open("https://www.youtube.com/", timeout=20) as response:
            print(f"  {OK} 通过代理访问 YouTube 成功（HTTP {response.status}，{time.time() - start:.1f}s）")
        return True
    except (urllib.error.URLError, OSError) as exc:
        print(f"  {WARN} 通过代理访问 YouTube 失败：{exc}")
        print("      若你只是下载国内站点，通常可以忽略；下载 YouTube 必须先修好代理。")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="VideoHarbor 环境自查")
    parser.add_argument("--url", help="可选：额外做一次真实解析来验证链路")
    parser.add_argument("--proxy", default="", help="可选：代理地址，如 127.0.0.1:7897")
    parser.add_argument("--browser", default="", help="可选：从哪个浏览器读取 cookies（chrome/edge/firefox）")
    args = parser.parse_args()

    problems = 0

    section("引擎与依赖")
    engine = ytdlp.engine_version()
    if engine.get("ok"):
        print(f"  {OK} yt-dlp {engine['version']}")
    else:
        problems += 1
        print(f"  {BAD} yt-dlp 不可用：{engine.get('error')}")
        print("      请先执行 ./setup.sh")

    if engine.get("ffmpeg"):
        print(f"  {OK} ffmpeg {engine['ffmpeg']}（高清合流 / MP3 转码 / 字幕内嵌）")
    else:
        problems += 1
        print(f"  {BAD} ffmpeg 缺失：brew install ffmpeg")

    runtime = engine.get("js_runtime")
    if runtime:
        print(f"  {OK} JS 运行时 {Path(runtime).name}（{runtime}）—— YouTube 必需")
    else:
        problems += 1
        print(f"  {BAD} 未找到 JS 运行时（node/deno/bun/qjs）")
        print("      YouTube 会报 “The page needs to be reloaded.”，请执行：brew install node")

    section("目录")
    config.ensure_dirs()
    print(f"  {OK} 下载目录 {config.DOWNLOAD_DIR}")
    print(f"  {OK} 磁盘剩余 {ytdlp._human_size(config.disk_free_bytes())}")
    if (config.STATIC_DIR / "index.html").is_file():
        print(f"  {OK} 前端产物已就绪")
    else:
        problems += 1
        print(f"  {BAD} 前端产物缺失：cd frontend && npm install && npm run build")

    if args.proxy:
        section("代理")
        check_proxy(args.proxy)

    if args.url:
        section("真实解析链路")
        opts: dict[str, object] = {}
        if args.proxy:
            opts["proxy"] = args.proxy
        if args.browser:
            opts["cookies_from_browser"] = args.browser
        try:
            info = ytdlp.probe(args.url, opts)
            print(f"  {OK} {info.get('extractor')} · {(info.get('title') or '')[:50]}")
            print(f"      时长 {info.get('duration_text')} · 可用画质 {len(info.get('formats') or [])} 个")
            if info.get("note"):
                print(f"  {WARN} {info['note']}")
            if not (info.get("formats") or []):
                problems += 1
                print(f"  {BAD} 未获取到任何画质，该站点可能暂时不可用")
        except ytdlp.EngineError as exc:
            problems += 1
            print(f"  {BAD} 解析失败：{exc}")

    print()
    if problems:
        print(f"\033[31m发现 {problems} 个问题，请按上方提示处理后重试。\033[0m")
    else:
        print("\033[32m环境检查通过，可以正常使用。\033[0m")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
