#!/usr/bin/env bash
# VideoHarbor 环境准备脚本（一次执行，之后无需再跑）
#
# 为什么要自带 Python 运行时？
#   本机只有系统自带 Python 3.9.6，而 yt-dlp 需要 Python >= 3.10；
#   且 GitHub release 二进制在本网络不可达。
#   本脚本把官方 python.org 的 Python 3.12 安装包「就地解包」到 runtime/ 目录，
#   全程不需要 sudo，不污染系统，卸载 = 删掉 runtime/ 目录。
#
# 网络说明：默认使用国内镜像（npmmirror / USTC），可用环境变量覆盖。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RT="$ROOT/runtime"
# 注意区分两个版本号：
#   PYVER    完整版本，用于下载地址与缓存文件名
#   PY_SERIES 大版本，用于安装包内的目录名（Versions/3.12）
# 早期把完整版本当成目录名用，导致校验永远失败、每次都要重新下载 46MB。
PYVER="3.12.8"
PY_SERIES="${PYVER%.*}"
PY_PKG_URL="${PY_PKG_URL:-https://registry.npmmirror.com/-/binary/python/${PYVER}/python-${PYVER}-macos11.pkg}"
PY_PKG_URL_FALLBACK="https://www.python.org/ftp/python/${PYVER}/python-${PYVER}-macos11.pkg"
PIP_INDEX="${PIP_INDEX:-https://mirrors.ustc.edu.cn/pypi/simple/}"

info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*" >&2; }

mkdir -p "$RT"

# ---------------------------------------------------------------- 1. Python 运行时
# 注意：必须通过 run.sh 探活，因为 framework 里的 python3 直接执行会因
# 找不到 dyld 依赖而 Abort，从而被误判为"运行时缺失"并触发重新解包。
NEED_PY=1
if [ -x "$RT/py/bin/python3" ] || [ -x "$RT/Python.framework/Versions/$PY_SERIES/bin/python3" ]; then
  if "$ROOT/run.sh" -m pip --version >/dev/null 2>&1; then
    NEED_PY=0
  fi
fi

if [ "$NEED_PY" = "1" ]; then
  info "解包 Python $PYVER 到 runtime/（约 45MB，无需 sudo）"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT

  # 复用已下载的安装包，避免重复下载
  CACHED_PKG="${PY_PKG_CACHE:-$RT/cache/python-${PYVER}-macos11.pkg}"
  if [ -f "$CACHED_PKG" ]; then
    info "使用已缓存的安装包：$CACHED_PKG"
    cp "$CACHED_PKG" "$TMP/py.pkg"
  else
    mkdir -p "$(dirname "$CACHED_PKG")"
    if ! curl -fL --connect-timeout 15 --retry 3 --retry-all-errors --progress-bar \
         -o "$TMP/py.pkg" "$PY_PKG_URL"; then
      warn "镜像下载失败，回退 python.org 官方源"
      curl -fL --connect-timeout 15 --retry 3 --retry-all-errors --progress-bar \
        -o "$TMP/py.pkg" "$PY_PKG_URL_FALLBACK"
    fi
    cp "$TMP/py.pkg" "$CACHED_PKG"
  fi

  info "解包安装包"
  pkgutil --expand-full "$TMP/py.pkg" "$TMP/exp"

  # 先校验新解包结果，确认可用后再替换旧运行时（避免把可用运行时删坏）
  NEW_PAYLOAD="$TMP/exp/Python_Framework.pkg/Payload"
  if [ ! -x "$NEW_PAYLOAD/Versions/$PY_SERIES/bin/python3" ]; then
    # 缓存包可能损坏：删掉缓存重新下载一次
    warn "安装包解包结果异常，清除缓存后重试"
    rm -f "$CACHED_PKG" && rm -rf "$TMP/exp" "$TMP/py.pkg"
    curl -fL --connect-timeout 15 --retry 3 --retry-all-errors --progress-bar \
      -o "$TMP/py.pkg" "$PY_PKG_URL" || \
      curl -fL --connect-timeout 15 --retry 3 --retry-all-errors --progress-bar \
        -o "$TMP/py.pkg" "$PY_PKG_URL_FALLBACK"
    cp "$TMP/py.pkg" "$CACHED_PKG"
    pkgutil --expand-full "$TMP/py.pkg" "$TMP/exp"
  fi
  if [ ! -x "$NEW_PAYLOAD/Versions/$PY_SERIES/bin/python3" ]; then
    warn "无法获得可用的 Python 运行时，请检查网络后重试：$PY_PKG_URL"
    exit 1
  fi

  info "替换运行时目录"
  rm -rf "$RT/Python.framework" "$RT/py"
  mv "$NEW_PAYLOAD" "$RT/Python.framework"
  # 运行时的 libssl/libcrypto 是绝对路径引用，用稳定软链结构让其可被解析
  ln -sfn "Python.framework/Versions/$PY_SERIES" "$RT/py"

  info "引导 pip"
  if ! "$ROOT/run.sh" -m ensurepip --upgrade --default-pip >/dev/null 2>&1; then
    warn "pip 引导失败，请删除 runtime/ 后重跑 ./setup.sh"
    exit 1
  fi
else
  info "Python 运行时已存在且可用（$("$ROOT/run.sh" -c 'import sys;print(sys.version.split()[0])')），跳过解包"
fi

# ---------------------------------------------------------------- 2. 校验运行时
info "运行时自检"
"$ROOT/run.sh" - <<'PY'
import ssl, sys, urllib.request
print(f"    Python {sys.version.split()[0]}  OpenSSL {ssl.OPENSSL_VERSION.split()[1]}")
urllib.request.urlopen("https://mirrors.ustc.edu.cn", timeout=15).close()
print("    HTTPS/TLS 正常")
PY

# ---------------------------------------------------------------- 3. 安装 yt-dlp
info "安装/升级 yt-dlp（镜像：${PIP_INDEX}）"
"$ROOT/run.sh" -m pip install --disable-pip-version-check -q --upgrade \
  -i "$PIP_INDEX" --trusted-host mirrors.ustc.edu.cn "yt-dlp[default]"

info "引擎版本"
"$ROOT/run.sh" --engine-version

# ---------------------------------------------------------------- 4. ffmpeg 检查
info "检查 ffmpeg（用于高清合流 / MP3 转码 / 字幕内嵌）"
if command -v ffmpeg >/dev/null 2>&1; then
  echo "    OK: $(ffmpeg -version | head -1 | cut -d' ' -f1-3)"
else
  warn "未找到 ffmpeg：高清视频合流、MP3 转码、字幕内嵌将不可用"
  warn "安装：brew install ffmpeg"
fi

# ---------------------------------------------------------------- 5. 目录准备
mkdir -p "$ROOT/static" "$ROOT/downloads"
if [ -f "$ROOT/static/index.html" ]; then
  info "前端产物已存在"
else
  warn "前端尚未构建：请执行  cd frontend && npm install && npm run build"
fi

echo
info "准备完成！启动： ./run.sh"
info "然后浏览器打开： http://127.0.0.1:8765"
