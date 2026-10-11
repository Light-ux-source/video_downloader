# 架构设计

> 🤖 接手项目请先读 [AI_CONTEXT.md](AI_CONTEXT.md)（进度/坑/约定）；
> 本文档解释"为什么这样分层"。

## 1. 设计目标与约束

| 目标 | 说明 |
|---|---|
| **不重复造轮子** | 站点适配全部交给 yt-dlp（覆盖 1800+ 站点），我们只做编排与交互 |
| **少改动、易升级** | 引擎以独立进程调用，升级 = 换安装包，业务代码零改动 |
| **零第三方后端依赖** | 后端只用 Python 标准库，避免环境问题；前端产物预构建入库 |
| **不依赖数据库** | 任务状态保存在内存，产物落磁盘 |
| **本机自用** | 只监听 `127.0.0.1`，无鉴权、无多租户隔离 |

**关键环境约束**（决定了整体形态）：最初的目标机器只有系统自带 Python 3.9，
而 yt-dlp 需要 ≥3.10；且 GitHub Release 在该网络不可达。
因此最终采用「项目内自带 Python 运行时 + PyPI 镜像安装 yt-dlp」的方案。
详见 [DECISIONS.md](DECISIONS.md#d1-python-运行时方案)。

## 2. 分层结构

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器（Vue 3 SPA，构建产物在 static/）                       │
│  · 解析结果展示  · 参数面板  · 任务队列  · SSE 订阅进度         │
└───────────────┬─────────────────────────────────────────────┘
                │ fetch JSON  /  EventSource(SSE)
┌───────────────▼─────────────────────────────────────────────┐
│  后端 app.py（标准库 ThreadingHTTPServer）                    │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ server.py   │  │ tasks.py     │  │ config.py          │  │
│  │ 路由/SSE/   │─▶│ 线程池/状态机 │  │ 路径/端口/并发/磁盘  │  │
│  │ 静态资源/校验│  │ 事件广播      │  │                    │  │
│  └─────────────┘  └──────┬───────┘  └────────────────────┘  │
│                          │                                   │
│                   ┌──────▼───────┐                           │
│                   │ ytdlp.py     │                           │
│                   │ 引擎封装：    │                           │
│                   │ 参数构造/进度  │                           │
│                   │ 解析/重试策略  │                           │
│                   └──────┬───────┘                           │
└──────────────────────────┼───────────────────────────────────┘
                           │ subprocess（start_new_session）
                ┌──────────▼──────────┐
                │ yt-dlp 进程          │
                │ + ffmpeg（合流/转码） │
                └─────────────────────┘
```

## 3. 模块职责

### `app.py` — 入口
- 解析命令行参数（`--host/--port/--concurrency/--no-browser`）
- 启动前自检并打印引擎、ffmpeg、JS 运行时、下载目录状态
- 注册信号处理，优雅关闭（先停 HTTP，再取消所有在跑任务）

### `core/config.py` — 配置集中处
- 路径：项目根、`static/`、`downloads/`、`runtime/cache/`
- 参数：监听地址、端口、下载并发（默认 2）、解析并发（默认 3）、任务历史上限
- 工具：`ytdlp_command()`、`ffmpeg_dir()`、`disk_free_bytes()`
- 所有项都支持环境变量覆盖（`VH_*`），便于测试与部署

### `core/ytdlp.py` — 引擎封装（本项目最核心的文件）
| 分类 | 职责 |
|---|---|
| **命令构造** | `build_download_args()` / `build_format_args()`：把前端参数翻译成 yt-dlp 命令行 |
| **画质预设** | `QUALITY_PRESETS`：best/1080/720/480/360/audio 映射到 `-f` 表达式 |
| **编码偏好** | `DEFAULT_SORT` / `FALLBACK_SORT`：H.264 优先（兼容性），失败换 AV1/VP9/HEVC |
| **进度解析** | `parse_progress_line()` / `classify_line()`：把 yt-dlp 输出变成结构化事件 |
| **信息解析** | `probe()` / `normalize_info()` / `normalize_formats()`：输出前端可用的元信息 |
| **环境自适应** | `detect_js_runtime()` / `sanitize_proxy()` / `normalize_url()` |
| **错误处理** | `friendly_error()` / `is_permanent_error()`：错误分类与中文化 |
| **重试策略** | `retry_strategies()`：4 级降级计划 |
| **进程控制** | `EngineProcess`：流式读取输出、按进程组取消、超时宽限后 SIGKILL |

### `core/tasks.py` — 任务编排
- **Task 数据类**：状态、阶段、进度、产物清单、日志、时间戳
- **TaskManager**：
  - 两个线程池：下载池（受并发限制）、解析池（独立限流）
  - 事件广播：每个任务一组订阅队列，SSE 消费
  - 状态机：`queued → running → done/error/canceled`
  - `_run_inner()`：多策略重试主循环
  - `_finalize()`：产物定位 + 部分失败判定（单独抽出以便单元测试）
  - 历史裁剪：超出上限时清理已结束任务

### `core/douyin.py` — 抖音免 Cookie 直连通道

yt-dlp 的抖音提取器撞签名墙（`encrypt_data_miss` / 403），因此单独实现一条通路：

采用**双路径**设计：

```
主路径（免签名，更稳、信息更全）：
  App Feed 接口 aweme/v1/feed/?aweme_id=<id>
  → 返回 bit_rate[]（精确分辨率 + 体积 + 直连 url_list）
  → aweme_list 为空即可判定"该视频本身不支持下载"

兜底路径（主路径失效时自动降级）：
  首页拿游客 Cookie（ttwid）→ SSR 分享页取 video_id → App 播放接口换 CDN 直链 → 带 Referer 下载
```

- 不依赖用户登录态与 Cookie，也不需要实现 `a_bogus` 签名
- 只用 `/aweme/v1/play/`（无水印原画）；`playwm` 固定 720p 且带水印，仅作最后兜底
- 档位映射：`best/1080→1080p`、`720→720p`、`480/360→540p`；低于最低档时取最小档
- 主路径的 `bit_rate` 通常最高 720p，请求更高画质时会用播放接口补一条 1080p 直链
- **图文帖（图集）**：Feed 接口不返回目标数据，改由分享页的 `images` 数组解析；
  优选 `url_list` 的 `.jpeg` 原图（实测无水印），排除带 `-water` 模板的 `download_url_list`
- **图文帖输出为视频**：这类帖子没有视频流（帖子级 video 只有封面、`bit_rate` 为空），
  但客户端播放的是「图片轮播 + 配乐」，因此由 `tasks._download_douyin_gallery()` 下载素材后，
  用 `compose_slideshow_video()` 本地合成 `{标题}.mp4`（竖屏 1080×1920，每图均分时长，
  AAC 配乐，总时长取 `music.duration`）；实况动图（`images[].video.play_addr`，本身是 mp4）
  另存为 `动图N.mp4`，全为动图时跳过合成。
  合成环节对"配乐完整性"做了硬保证：素材下载走任务配置的代理、配乐先校验可解码、
  ffmpeg 显式 `-map` 音视频流、`apad` 防截断、合成后 ffprobe 校验产物含音轨
  （缺失则换方案重试，仍失败则标记任务未完成并说明原因），避免"以为有配乐实际无声"
- 失败抛 `DouyinError`（视频本身不可下载时抛其子类 `DouyinUnavailable`），
  消息可直接展示；调用方把原因写进日志/错误里，避免被 yt-dlp 上游的模糊报错误导

详见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md#抖音已支持免-cookie-直连通道)。

### `core/server.py` — HTTP 层
- 路由表、JSON 读写、请求体大小限制
- SSE 端点（订阅 → 推送 → 终态关闭 + 心跳）
- 静态资源（带 hash 产物长缓存，`index.html` 不缓存）
- 安全校验：URL 白名单协议、下载目录路径逃逸防护、任务 ID 格式
- `--ignore-errors` 之外的所有错误都转成 JSON，避免请求打挂服务

## 4. 任务生命周期

```
POST /api/tasks
      │
      ▼
   [queued] ──线程池有空位──▶ [running]
      │                          │
      │                          ├─ 纠错链接形态（抖音 /note/ → /video/）
      │                          ├─ 播放列表：先查条目总数
      │                          ├─ 策略 1：用户原始配置
      │                          │    ↓ 失败（非永久错误）
      │                          ├─ 等待 5s → 策略 2：换编码
      │                          │    ↓ 失败
      │                          ├─ 等待 10s → 策略 3：降并发 + 分片下载
      │                          │    ↓ 失败
      │                          ├─ 等待 15s → 策略 4：单连接 + 更小分片
      │                          │
      │                          └─ _finalize()：定位产物、判定部分失败
      ▼                          ▼
  [canceled]              [done] / [error]
```

**关键机制**：

1. **进度事件节流**：进度行最多每 300ms 广播一次，避免刷屏。
2. **结果文件旁路**：用 `--print-to-file` 记录最终产物路径（`--print` 会抑制进度输出，不能混用）；
   该选项是**追加**写入，所以每次执行前必须清空。
3. **产物定位三级兜底**：结果文件 → 标题+扩展名+时间窗匹配 → 目录快照 diff。
4. **部分失败判定**：读回结果文件全部行、逐行校验存在性、对比播放列表预期条目数。
5. **抖动自愈不误报**：日志里有 `Giving up` 但产物数达标时，清空失败计数。
6. **取消**：向进程组发 SIGTERM，5 秒宽限后 SIGKILL，避免 ffmpeg 子进程残留。

## 5. 前端结构

| 文件 | 职责 |
|---|---|
| `App.vue` | 布局外壳（顶栏/遥测栏/舞台/页脚/Toast）、全局快捷键、初始化 |
| `store/index.js` | 自研轻量 store：状态、SSE 订阅管理、API 调用封装、持久化 |
| `api/client.js` | fetch 封装，统一超时与错误信息 |
| `components/CommandDeck.vue` | 命令行风格输入框、画质选择、高级参数面板 |
| `components/InspectPanel.vue` | 视频/播放列表信息展示、格式表 |
| `components/TaskQueue.vue` | 任务列表、筛选、清理 |
| `components/TaskCard.vue` | 单任务卡片：进度、遥测、操作、日志展开 |
| `components/ProgressBar.vue` | ASCII 块状进度条 |
| `components/TelemetryRail.vue` | 左侧系统状态栏 |
| `components/Icon.vue` | 统一 SVG 图标（不用 emoji） |
| `styles/tokens.css` | 设计令牌：颜色/字体/间距/动效/无障碍 |

**SSE 订阅管理**：每个非终态任务持有独立 `EventSource`；收到 `end` 事件或终态时关闭；
异常断开时最多重连 3 次（指数退避）。另有 20 秒兜底轮询，防 SSE 丢失导致状态不同步。

## 6. 扩展指引

| 想做什么 | 改哪里 |
|---|---|
| 增加画质预设 | `core/ytdlp.py` 的 `QUALITY_PRESETS` + 前端自动读取 `/api/presets` |
| 支持新站点 | 通常无需改代码（yt-dlp 负责）；若需特殊 URL 纠正，加进 `normalize_url()`；若引擎撞签名墙，参考 `core/douyin.py` 的独立通道写法 |
| 增加下载参数 | `build_format_args()` 加参数 + `core/server.py` 的 `_download_options` 白名单 + 前端表单 |
| 调整重试策略 | `core/ytdlp.py` 的 `STRATEGY_PATCHES` |
| 换成 FastAPI/Flask | 只需替换 `core/server.py`，其余模块不依赖 HTTP 框架 |
| 前端换 Vue 组件库 | `frontend/src/components/` 内替换，`store` 与 `api` 层不变 |
