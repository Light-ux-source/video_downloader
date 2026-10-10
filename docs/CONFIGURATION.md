# 配置说明

配置集中在 `core/config.py`，全部支持**环境变量覆盖**（前缀 `VH_`），
因此无需改代码即可调整端口、目录、并发等。

---

## 1. 环境变量一览

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VH_HOST` | `127.0.0.1` | 监听地址。改为 `0.0.0.0` 可局域网访问（自行承担风险） |
| `VH_PORT` | `8765` | 监听端口 |
| `VH_CONCURRENCY` | `2` | 同时下载数（超出的任务排队） |
| `VH_PROBE_WORKERS` | `3` | 同时解析数（解析较耗网络，单独限流） |
| `VH_TASK_HISTORY_LIMIT` | `200` | 内存中保留的任务记录上限（超出后清理已结束的） |
| `VH_PROBE_TIMEOUT` | `90` | 单次解析超时（秒） |
| `VH_ROOT` | 项目根目录 | 项目根路径覆盖 |
| `VH_STATIC` | `<root>/static` | 前端产物目录 |
| `VH_DOWNLOAD_DIR` | `<root>/downloads` | 下载输出目录 |
| `VH_CACHE_DIR` | `<root>/runtime/cache` | 引擎结果文件与缓存 |
| `VH_VERBOSE` | 未设置 | 设置后打印 HTTP 访问日志 |

`setup.sh` 专用：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PIP_INDEX` | `https://mirrors.ustc.edu.cn/pypi/simple/` | pip 镜像源 |
| `PY_PKG_URL` | npmmirror 的 python.org 镜像 | Python 安装包下载地址 |
| `PY_PKG_CACHE` | `runtime/cache/python-3.12.8-macos11.pkg` | 安装包缓存路径 |

### 示例

```bash
# 换端口 + 提高并发 + 下载到外置盘
VH_PORT=9000 VH_CONCURRENCY=4 VH_DOWNLOAD_DIR=/Volumes/Ext/videos ./run.sh

# 等价写法（命令行参数优先于环境变量）
./run.sh --port 9000 --concurrency 4
```

### 命令行参数

| 参数 | 说明 |
|---|---|
| `--host` | 监听地址 |
| `--port` | 监听端口 |
| `--concurrency` | 同时下载数 |
| `--no-browser` | 启动后不自动打开浏览器 |

---

## 2. 目录约定

| 目录 | 内容 | 是否入库 |
|---|---|---|
| `runtime/` | 自带 Python 3.12 + yt-dlp + 缓存（约 243MB） | ❌ 由 `setup.sh` 生成 |
| `downloads/` | 下载产物、`.part/` 临时分片 | ❌ 用户数据 |
| `static/` | 前端构建产物 | ✅ 入库（clone 后免构建） |
| `frontend/node_modules/` | 前端开发依赖 | ❌ |
| `*.pyc` / `__pycache__/` | Python 缓存 | ❌ |

完整忽略规则见 [`.gitignore`](../.gitignore)。

---

## 3. 代理配置

### 填写方式

「高级参数 → 代理」支持两种写法，程序会自动补全协议头：

```
127.0.0.1:7897          →  http://127.0.0.1:7897
http://127.0.0.1:7897   →  原样使用
socks5://127.0.0.1:1080 →  原样使用
```

### 建议

- **YouTube 必须走代理**（本机直连不通）
- **B 站建议直连**（走代理反而更慢，且可能触发风控）
- 代理需要能访问目标站点；若代理只对部分域名生效，注意分流规则
- 代理不稳定时下载会中断，程序会自动降级重试；也可手动降并发分片 + 限速

### 排障

```bash
# 代理端口是否可连
nc -z -v 127.0.0.1 7897

# 通过代理访问目标站点
curl -o /dev/null -w "%{http_code}\n" -x http://127.0.0.1:7897 https://www.youtube.com/
```

若上面两条正常但下载仍失败，多半是代理的**上游节点**在抖动（客户端在监听，
但隧道建立后立刻断开）——这是网络问题，不是程序问题。

---

## 4. Cookies 配置

### 方式一：从浏览器读取（推荐）

「高级参数 → 从浏览器读取 Cookies」选择已登录的浏览器：

| 浏览器 | 说明 |
|---|---|
| chrome | macOS 上需要钥匙串授权（首次可能弹窗） |
| edge / firefox / brave | 一般可直接读取 |
| safari | macOS 系统限制，**可能失败**，建议改用 Chrome 或 cookies 文件 |

### 方式二：cookies.txt 文件

用浏览器扩展（如 "Get cookies.txt LOCALLY"）导出为目标站点的 Netscape 格式文件，
然后在「Cookies 文件」里填**绝对路径**：

```
/Users/you/cookies/youtube.txt
```

### 注意事项

- 后端**不会复制或上传**你的 cookies，只在调用 yt-dlp 时把路径传给它
- 带登录态访问 YouTube 时，可能拿到**更少**的画质（播放器策略不同）。
  如果画质异常少，清空 Cookies 再试
- Cookies 有时效，过期后重新导出即可

---

## 5. 画质与格式

### 预设映射（`core/ytdlp.py` 的 `QUALITY_PRESETS`）

| 预设 | `-f` 表达式 |
|---|---|
| 最佳画质 | `bv*+ba/b` |
| 1080P | `bv*[height<=1080]+ba/b[height<=1080]/b` |
| 720P | `bv*[height<=720]+ba/b[height<=720]/b` |
| 480P | `bv*[height<=480]+ba/b[height<=480]/b` |
| 360P | `bv*[height<=360]+ba/b[height<=360]/b` |
| 仅音频 | `-x --audio-format mp3 --audio-quality 0` |

### 编码偏好（`-S` 排序）

```
首选：res,codec:h264:av01:vp9:hevc,br,size
回退：res,codec:av01:vp9:h264:hevc,br,size
```

优先分辨率，同分辨率下优先 H.264（本地播放器/剪辑软件兼容性最好），
HEVC 放最后（部分播放器不支持）。某些 CDN 会拒绝特定编码流，
因此失败时会自动换一套偏好重试。

### 自定义格式表达式

勾选「覆盖画质，使用自定义 yt-dlp 格式表达式」后可填任意 `-f` 表达式：

```
bv*[height<=1080][vcodec^=avc1]+ba/b       # 只要 H.264 的 1080p
ba[abr>128]/ba                              # 只要码率大于 128k 的音频
bv*[fps>30]+ba/b                            # 高帧率优先
```

表达式语法见 [yt-dlp 官方文档](https://github.com/yt-dlp/yt-dlp#format-selection)。

---

## 6. 重试与容错参数

| 位置 | 参数 | 值 |
|---|---|---|
| `core/ytdlp.py` | `--retries` / `--fragment-retries` | 15 / 15 |
| `core/ytdlp.py` | `--socket-timeout` | 20 秒 |
| `core/tasks.py` | `RETRY_BACKOFF_SECONDS` | 5.0（第 N 次重试等待 N×5 秒） |
| `core/ytdlp.py` | `STRATEGY_PATCHES` | 4 级降级策略定义 |
| `core/ytdlp.py` | `PERMANENT_ERROR_HINTS` | 不重试的错误关键字 |

调整示例——把退避缩短（网络好时可加快失败反馈）：

```python
# core/tasks.py
RETRY_BACKOFF_SECONDS = 2.0
```

---

## 7. 前端构建配置

`frontend/vite.config.js`：

| 项 | 值 | 说明 |
|---|---|---|
| `build.outDir` | `../static` | 产物直接输出给后端托管 |
| `build.emptyOutDir` | `true` | 构建前清空 |
| `build.target` | `es2020` | 兼容性目标 |
| `server.proxy['/api']` | `http://127.0.0.1:8765` | 开发模式 API 转发 |

镜像源在 `frontend/.npmrc`（默认 npmmirror）。

---

## 8. 端口占用排查

```bash
# 谁占用了 8765
lsof -nP -iTCP:8765 -sTCP:LISTEN

# 换端口启动
./run.sh --port 8899
```
