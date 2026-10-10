#!/usr/bin/env bash
# VideoHarbor 启动器 / 运行时包装器
#
# 用法：
#   ./run.sh                  启动 Web 服务（默认 http://127.0.0.1:8765）
#   ./run.sh --engine-version 打印 yt-dlp 版本
#   ./run.sh -m yt_dlp ...    直接调用 yt-dlp（等价于命令行使用）
#   ./run.sh - <<'PY'         用自带运行时执行 Python 代码片段
#
# 本项目自带 Python 运行时（runtime/），不依赖系统 Python 版本，也无需 sudo。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RT="$ROOT/runtime"
PY="$RT/Python.framework/Versions/3.12/bin/python3"

if [ ! -x "$PY" ]; then
  # 仅用于 setup.sh 自举阶段（运行时尚未就位时）
  if [ -x "$RT/py/bin/python3" ]; then
    PY="$RT/py/bin/python3"
  else
    echo "运行时缺失，请先执行： ./setup.sh" >&2
    exit 1
  fi
fi

# dyld 需要能解析 framework 与 OpenSSL 的绝对路径引用
export DYLD_FRAMEWORK_PATH="$RT"
export DYLD_FALLBACK_LIBRARY_PATH="$RT/Python.framework/Versions/3.12/lib"
export PYTHONNOUSERSITE=1
export PYTHONPYCACHEPREFIX="$RT/pycache"
# macOS 上没有 certifi 时用系统证书；装了 certifi 则优先使用
if [ -f "$RT/Python.framework/Versions/3.12/lib/python3.12/site-packages/certifi/cacert.pem" ]; then
  export SSL_CERT_FILE="$RT/Python.framework/Versions/3.12/lib/python3.12/site-packages/certifi/cacert.pem"
else
  export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/cert.pem}"
fi
export PATH="$RT/Python.framework/Versions/3.12/bin:$PATH"

case "${1:-}" in
  --engine-version)
    exec "$PY" -m yt_dlp --version
    ;;
  -m)
    shift
    exec "$PY" -m "$@"
    ;;
  -)
    # 从标准输入执行 Python 代码： ./run.sh - <<'PY' ... PY
    exec "$PY" -
    ;;
  -c|--code)
    # 直接执行一段代码： ./run.sh -c "import sys;print(sys.version)"
    shift
    exec "$PY" -c "$1"
    ;;
  --py)
    # 用自带运行时执行任意脚本： ./run.sh --py tests/run_tests.py --live
    shift
    cd "$ROOT"
    exec "$PY" "$@"
    ;;
  *)
    cd "$ROOT"
    exec "$PY" "$ROOT/app.py" "$@"
    ;;
esac
