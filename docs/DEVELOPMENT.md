# 开发指南

## 1. 快速开始

```bash
git clone <仓库地址> video_downloader && cd video_downloader
./setup.sh                 # 准备运行时 + yt-dlp
./run.sh                   # 启动（后端 + 已构建的前端产物）
```

只需要改前端时：

```bash
cd frontend && npm install && npm run dev    # 热更新，API 代理到 8765
# 注意：另开一个终端跑 ./run.sh，dev server 只服务页面
```

改完前端**必须重新构建**，否则后端托管的仍是旧产物：

```bash
cd frontend && npm run build                 # 输出到 ../static
```

---

## 2. 目录结构

```
video_downloader/
├─ app.py                      # 后端入口：参数解析、启动自检、优雅关闭
├─ core/
│  ├─ config.py                # 路径/端口/并发/磁盘（支持 VH_* 环境变量覆盖）
│  ├─ ytdlp.py                 # 引擎封装（最核心，892 行）
│  ├─ tasks.py                 # 任务队列与状态机（766 行）
│  └─ server.py                # HTTP 路由 / SSE / 静态资源 / 安全校验
├─ frontend/
│  ├─ src/
│  │  ├─ main.js               # 挂载
│  │  ├─ App.vue               # 布局外壳
│  │  ├─ store/index.js        # 自研轻量 store + SSE 订阅管理
│  │  ├─ api/client.js         # fetch 封装
│  │  ├─ components/           # 6 个业务组件 + Icon + ProgressBar
│  │  ├─ styles/tokens.css     # 设计令牌
│  │  └─ utils/format.js       # 格式化工具（含 ASCII 进度条）
│  ├─ vite.config.js           # 产物输出到 ../static
│  └─ .npmrc                   # 镜像源
├─ tools/verify_env.py         # 环境自查工具
├─ tests/run_tests.py          # 后端自测（126 项）
├─ static/                     # 前端构建产物（入库，clone 后免构建）
├─ docs/                       # 文档
├─ run.sh                      # 启动器 / 运行时包装器
└─ setup.sh                    # 一次性环境准备
```

---

## 3. `run.sh` 的几种用法

```bash
./run.sh                                  # 启动 Web 服务
./run.sh --port 9000 --concurrency 4      # 带参数
./run.sh --engine-version                 # 打印 yt-dlp 版本
./run.sh -m yt_dlp --list-formats <url>   # 直接调用 yt-dlp
./run.sh -c "import sys;print(sys.version)"  # 执行一段代码
./run.sh - <<'PY'                         # 从 stdin 执行代码
...
PY
./run.sh --py tests/run_tests.py          # 用自带运行时跑脚本
```

`run.sh` 会自动设置 `DYLD_FRAMEWORK_PATH`、`DYLD_FALLBACK_LIBRARY_PATH`、
`SSL_CERT_FILE`、`PYTHONNOUSERSITE`，所以自带运行时可以放在任意路径。

---

## 4. 测试

```bash
./run.sh --py tests/run_tests.py          # 离线：126 项，秒级
./run.sh --py tests/run_tests.py --live   # 额外真实解析 + 真实下载（依赖外网）
```

### 测试覆盖

| 分组 | 覆盖内容 |
|---|---|
| 进度行解析 | 独立模板 / 带 `download:` 前缀 / 前缀被吃掉的退化形态 / `NA` 字段 |
| 输出行分类 | Destination、Merger、ERROR（含裸 `ERROR:`）、warning、ExtractAudio、播放列表结束 |
| 下载命令构造 | 画质预设、音频提取、字幕、自定义格式、Cookies、代理、播放列表、非法值回退 |
| 产物定位 | `read_result_files` 多行、`read_result_file` 兼容、快照兜底 |
| 已有产物匹配 | 音频/视频扩展名隔离、时间窗约束（防跨任务误认） |
| 分片清理 | 清理 `.f<id>.` 与 `.part`，不误删完整文件 |
| 多策略重试 | 策略数量、首项不改配置、换编码、降并发、音频/自定义格式不换编码 |
| 永久性错误 | 404/不支持/需登录/年龄限制 → 不重试；SSL/503 → 可重试 |
| 环境自适应 | JS 运行时探测与参数注入、代理协议补全、抖音链接纠正 |
| 错误提取与中文化 | 错误+提示成对返回、裸 ERROR 兜底、14 类错误映射 |
| 任务状态机 | 进度/阶段推进、warnings 不覆盖提示、错误捕获、取消排队任务、失败路径、事件订阅 |
| 收尾判定 | 抖动自愈不误报、产物缺失判部分完成、播放列表未达标、多产物计数 |
| HTTP 接口 | health、presets、probe 校验、任务增删查、越权路径、超量、404、SSE 首帧 |

### 写测试的注意事项

- 测试里创建 `TaskManager` 后记得 `shutdown()`，否则线程池不让进程退出
- 涉及 `create()` 的断言不要假定初始状态是 `queued`（线程池可能已开跑）
- 需要"时间窗"语义的测试要自己造文件和 `before_files` 集合（见
  `test_match_existing`）
- 真实网络相关断言放在 `--live` 分支，避免离线环境阻塞

---

## 5. 常见改动指引

### 新增画质预设

```python
# core/ytdlp.py
QUALITY_PRESETS = [
    ...
    {"id": "2160", "label": "4K", "hint": "不超过 3840×2160",
     "format": "bv*[height<=2160]+ba/b[height<=2160]/b"},
]
```

前端会自动从 `/api/presets` 读取并渲染按钮，无需改前端。

### 新增下载参数

三步都要改：

1. `core/ytdlp.py` 的 `build_format_args()`：加参数到 `normalized` 与 `args`
2. `core/server.py` 的 `_download_options()`：把键名加进 `allowed` 白名单
3. `frontend/src/components/CommandDeck.vue`：加表单控件；
   并在 `frontend/src/store/index.js` 的 `defaultSettings` 与
   `collectDownloadOptions()` 里补上

### 支持新的 URL 纠正规则

```python
# core/ytdlp.py
def normalize_url(url: str) -> tuple[str, str | None]:
    # 已有：抖音 /note/ → /video/
    # 新规则照此追加，返回 (纠正后URL, 给用户看的提示或 None)
```

### 调整重试策略

```python
# core/ytdlp.py
STRATEGY_PATCHES = [
    ("备用编码（VP9/AV1/HEVC）重试", {"_codec_phase": "fallback"}),
    ...
]
# core/tasks.py
RETRY_BACKOFF_SECONDS = 5.0   # 第 N 次重试等待 N×基数
```

### 新增 API 接口

1. `core/server.py` 的 `do_GET` / `do_POST` 里加路由分支
2. 实现 `_api_xxx()` 方法；错误用 `BadRequest` 抛（会自动转 400）
3. 在 `tests/run_tests.py::test_http_api` 里补断言
4. 更新 `docs/API.md`

### 修改界面

| 位置 | 影响 |
|---|---|
| `styles/tokens.css` | 全局配色/字体/间距/动效（改一处全站生效） |
| `components/CommandDeck.vue` | 输入区、画质、高级参数 |
| `components/InspectPanel.vue` | 解析结果展示 |
| `components/TaskCard.vue` | 任务卡片（进度、遥测、操作、日志） |
| `components/TelemetryRail.vue` | 左侧系统遥测 |
| `App.vue` | 布局外壳、顶栏、页脚、Toast |

改完执行 `cd frontend && npm run build`。

---

## 6. 调试技巧

### 后端

```bash
VH_VERBOSE=1 ./run.sh            # 打印访问日志
curl -s localhost:8765/api/health | python3 -m json.tool
```

后台任务里出现未捕获异常时，`_run()` 会兜底把堆栈写进任务日志
（任务卡片「查看日志」可见），不会静默丢失。

### 引擎调用

最快的排障方式是真的跑一遍命令：

```bash
# 打印应用实际会用的完整命令（看任务日志第一行）
curl -s localhost:8765/api/tasks/<id> | python3 -c "
import sys,json; t=json.load(sys.stdin)['task']; print(t['log'][0])"
```

拿到命令后手动执行，加 `-v` 看细节。

### 前端

- 浏览器控制台看 SSE 帧：Network → `events` 请求
- `EventSource` 断开后最多重连 3 次，另有 20 秒兜底轮询保持同步
- 界面顶栏「重连进度」按钮可手动重新订阅所有活动任务

---

## 7. 代码风格约定

- **后端**：Python 3.12 语法（`str | None`），中文注释解释"为什么"而非"是什么"，
  关键函数带 docstring。类型标注尽量完整。
- **前端**：Vue 3 `<script setup>`，组件内聚（模板/逻辑/样式同文件），
  scoped 样式。
- **注释原则**：只写非显而易见的约束与踩过的坑（例如"`--print-to-file` 是追加写入，
  每次执行前必须清空"），不写复述代码的废话。
- **错误处理**：用户可见的错误都要能落到"我该做什么"上。

---

## 8. 提交前检查清单

```bash
./run.sh -m compileall -q core app.py tools tests   # 语法
./run.sh --py tests/run_tests.py                    # 离线自测
./run.sh --py tools/verify_env.py                   # 环境自查
cd frontend && npm run build                        # 前端产物（改了前端才需要）
```

- [ ] 新增/修改的行为有对应测试断言
- [ ] 用户可见的错误有中文提示
- [ ] 相关文档（README / docs/*.md）已更新
- [ ] 没有把 `runtime/`、`downloads/`、`node_modules/`、cookies 文件提交进仓库
