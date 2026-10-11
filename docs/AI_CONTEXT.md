# AI 上下文手册（供下一个 AI 快速接手）

> 本文件是**接手本项目的第一入口**。目标：让另一个 AI 在 10 分钟内搞清楚
> 「这是什么、现在到哪一步了、哪些坑已经踩过、下一步该怎么动」。
>
> 维护约定：**每次完成实质性工作后同步更新本文件**（尤其第 3、6、7 节）。
> 最后更新：2026-10-12 · 对应提交（阶段 11 修复图文帖配乐，待提交）

---

## 1. 一分钟速览

| 项 | 值 |
|---|---|
| 项目 | **VideoHarbor** —— 本地视频下载工作台（Web GUI + 后端） |
| 路径 | `/Users/mike/ai_project/video_downloader` |
| 仓库 | https://github.com/Light-ux-source/video_downloader （Public） |
| 当前提交 | `dfb16c0`（共 13 个提交，2026-10-10 起） |
| 访问地址 | http://127.0.0.1:8765 （只监听回环，无鉴权，单机自用） |
| 架构 | 浏览器(Vue 3 SPA) → Python 标准库 HTTP 后端 → subprocess 调 yt-dlp/ffmpeg |
| 技术栈 | 后端 Python 3.12（**仅标准库**）；前端 Vue 3 + Vite；引擎 yt-dlp + ffmpeg |
| 测试 | **238 项**，`./run.sh --py tests/run_tests.py`（离线，秒级） |
| 代码量 | Python 4727 行 / 前端 3219 行 / 文档 2498 行 |
| 数据库 | **无**（任务状态在内存，产物落 `downloads/`） |

### 两条最重要的命令

```bash
./run.sh                                    # 启动服务（首次先跑 ./setup.sh）
./run.sh --py tests/run_tests.py            # 全部自测（改动后必跑）
```

### 三分钟建立上下文（按顺序读）

1. 本文件（你正在读）
2. [`README.md`](../README.md) —— 功能与快速开始
3. [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) —— 模块职责与数据流
4. [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) —— **已踩过的坑**（含抖音通路原理）
5. [`docs/DECISIONS.md`](DECISIONS.md) —— 为什么这么设计（14 项 ADR）
6. [`docs/CHANGELOG.md`](CHANGELOG.md) —— 11 个阶段各做了什么

---

## 2. 环境约束（决定了所有选型，务必先理解）

这台机器的环境很特殊，**任何方案都必须先过这三条**：

| 约束 | 实测 | 后果 |
|---|---|---|
| 系统 Python 只有 **3.9.6**，无 brew python | yt-dlp 要求 ≥3.10 | 项目**自带 Python 3.12 运行时**在 `runtime/`（setup.sh 就地解包，无需 sudo） |
| **GitHub Release 不可达**（302 后对象存储超时） | 无法用 yt-dlp 官方单文件二进制 | 改用 **PyPI 镜像**装包（USTC 镜像很快） |
| 本机到部分 CDN 连接抖动 | 常见 `SSL: UNEXPECTED_EOF`、分片重置 | 必须有多策略重试；见第 6 节 |

其它事实：
- `ffmpeg 8.1.2`（`/opt/homebrew/bin/ffmpeg`）可用 —— 合流/转码/合成都依赖它
- `node v26.8.1` 可用 —— **YouTube 必需**（新版 yt-dlp 默认只认 deno，必须显式注入 `--js-runtimes`）
- 用户网络有代理：`127.0.0.1:7897`（Verge/mihomo），**YouTube 必须走它**
- **git 全局配置有坑**：`url.https://ghproxy.com/https://github.com/.insteadof=https://github.com/`，
  该镜像已失效 → 直接用 `git push` 会失败。**推送方式见第 8 节**

---

## 3. 当前进度与状态

### 已完成（均已实测验证）

| 能力 | 状态 | 验证方式 |
|---|---|---|
| 链接解析（元信息 + 全画质列表） | ✅ | B 站 / YouTube / 抖音实测 |
| 画质选择（best/1080/720/480/360/仅音频） | ✅ | 真实下载 + ffprobe 校验 |
| 音频提取 MP3（含元数据/封面） | ✅ | ffprobe 确认 mp3 + 嵌入封面 |
| 批量投递（多行，≤50 个） | ✅ | API 实测 |
| 播放列表（整列表 / 指定条目） | ✅ | B 站合集 2 条目，含「部分完成」判定 |
| SSE 实时进度（百分比/速度/ETA/阶段） | ✅ | 实测 21 个单调递增事件 |
| 取消 / 重试 / 断点续传 | ✅ | 进程组干净退出；`Resuming download at byte …` |
| 多策略重试 + 退避 | ✅ | 4 级降级，失败可自愈 |
| 字幕内嵌 | ⚠️ **仅代码链路验证** | 测试站点只有"弹幕"（私有 XML，ffmpeg 无法内嵌）→ 已优雅降级 |
| **抖音视频**（免 Cookie，无水印 1080p） | ✅ | 真实下载，ffprobe 确认 |
| **抖音图文帖 → 合成视频**（图片轮播 + 配乐） | ✅ | **3 条真实链接**（单图×2 + 5 图×1）均 1080×1920 / h264+**aac**，音量 −12.7 / −14.3 / −14.1 dB，时长与配乐一致 |
| **图文帖配乐获取**（多源候选 + 按响应类型探测） | ✅ | 修复"只有部分链接有声"；覆盖 `.mp3` 与**无扩展名 m4a** 两种配乐 |
| 启动端口预检（友好报错） | ✅ | 单测覆盖 |
| 前端界面（暗色霓虹 HUD） | ✅ | 无头 Chrome 截图验收 |

### 未完成 / 待验证（**接手时优先看这里**）

| 事项 | 现状 | 需要的动作 |
|---|---|---|
| 抖音**多图**帖轮播时长分配 | 已用 5 图真实帖验证（按配乐时长均分） | 更多图（9 图）与"每张固定 N 秒"需求仍待用户确认 |
| 抖音**实况动图**（`动图N.mp4`）分支 | 代码已实现（按数据结构 + 参考实现） | **无真实样本跑过**，需带实况的帖子验证 |
| 字幕内嵌真实成功案例 | 只有降级路径被验证 | 找一个有正常字幕（SRT/ASS）的站点实测 |
| 抖音合集 / 用户主页 / 直播 | ❌ 不支持 | 合集与主页**可能可行**（`sec_uid` 已能拿到）；直播不建议做 |
| 多图帖配乐时长 vs 各图节奏 | 均分时长（P2 未做） | 用户可能要求固定每张 N 秒，需其拍板 |
| 图文帖**图片素材**下载健壮性 | 单链接单次（P1 未做） | `url_list` 有 3–4 条等价 CDN 链接但未轮换/补试，抖动网络下多图帖更易失败 |
| 图片来源格式 | 优先 `.jpeg` 原图 | 部分帖子可能只有 `.webp`（代码已回退处理） |

### 仓库健康度

- 阶段 11 改动**尚未提交**（工作区含 `core/douyin.py`、`core/tasks.py`、测试与文档）
- 远端最后同步提交为 `dfb16c0`（阶段 11 待推送）
- `.gitignore` 已排除 `runtime/`（243MB）、`downloads/`、`node_modules/`
- `static/` 构建产物**故意入库**（clone 后免 `npm install` 即可用）
- **推前端改动后必须 `cd frontend && npm run build` 并提交** `static/` 变化

---

## 4. 代码地图（按重要性）

| 文件 | 行数 | 职责 | 改动风险 |
|---|---|---|---|
| `core/douyin.py` | 987 | **抖音专用提取器**（视频 + 图文帖），含通路原理注释 | 高（依赖抖音接口，易变） |
| `core/tasks.py` | 1340 | 任务状态机、下载编排、图集合成、收尾判定 | 高（核心逻辑） |
| `core/ytdlp.py` | 912 | 引擎封装：命令构造、进度解析、重试策略、环境自适应 | 中 |
| `core/server.py` | 418 | HTTP 路由 / SSE / 静态资源 / 路径越权防护 | 中 |
| `app.py` | 164 | 入口：参数、端口预检、启动自检、优雅关闭 | 低 |
| `core/config.py` | 63 | 路径/端口/并发（支持 `VH_*` 环境变量） | 低 |
| `tests/run_tests.py` | 1134 | 238 项自测（28 个分组，含配乐回归 30 项） | —— |
| `tools/verify_env.py` | 142 | 环境自查（含真实解析验证） | 低 |
| `frontend/src/**` | 3219 | Vue 3 组件 + 自研 store + 设计令牌 | 中 |

### 关键函数定位（便于精准修改）

```
core/douyin.py
  is_douyin_url()          URL 识别（含 App 域名）
  resolve_numeric_id()     短链→数字 id
  extract()                ★ 总入口（主路径 feed → 兜底分享页）
  _extract_via_feed()      主路径：App Feed 免签名接口（视频走这里，带 bit_rate）
  extract_item()           ★ 分享页内联 JSON → videoInfoRes.item_list[0]（最可靠通道）
  parse_images_array()     图文帖：括号配平解析内联 images 数组
  rank_image_urls()        图文帖：jpeg 原图优先（排除带水印的 download_url_list）
  _audio_url_candidates()  ★ 配乐多源候选链（play_url → play_addr.uri → <audio> → 正则 → playwm）
  _music_duration_seconds() 配乐时长（优先取自 JSON）
  extract_gallery()        图文帖：构造 kind=gallery 结果
  parse_music_duration()   配乐时长的正则兜底
  play_url_for()           播放直链接口（play 无水印优先，playwm 兜底）

core/tasks.py
  _run_inner()             ★ 单任务执行主流程
  _resolve_douyin_direct() 抖音：换成直链 / 或准备图集素材
  _download_douyin_gallery() ★ 图文帖：下载素材 + 合成视频
  download_douyin_music()  ★ 配乐：候选逐个 range 探测 → 按 Content-Type 定扩展名 → 校验换源
  _audio_ext_from_response() 响应类型 → .mp3/.m4a（无扩展名 OSS key 的关键）
  compose_slideshow_video()  ★ ffmpeg 合成（含音轨校验，见第 7 节坑 5）
  _finalize()              ★ 收尾：定位产物 + 判定「部分完成」
  _rename_to_title()       CDN 乱码文件名 → 按标题重命名

core/ytdlp.py
  build_format_args()      把前端参数翻译成 yt-dlp 命令行
  parse_progress_line()    ★ 进度解析（结构识别，容忍 NA）
  retry_strategies()       4 级降级策略
  friendly_error()         14 类错误 → 中文可操作提示
  detect_js_runtime()      自动注入 --js-runtimes（YouTube 必需）
```

---

## 5. 运行与验证

```bash
# 启动（先确认 8765 没被占用；已被占用会给出友好提示）
./run.sh
./run.sh --port 8899 --concurrency 4      # 换端口 / 改并发

# 测试
./run.sh --py tests/run_tests.py          # 238 项离线自测（改动后必跑）
./run.sh --py tests/run_tests.py --live   # 追加真实解析 + 真实下载
./run.sh --py tools/verify_env.py --url "<链接>" --proxy 127.0.0.1:7897 --browser chrome

# 直接调引擎（最快复现问题）
./run.sh -m yt_dlp --js-runtimes node --proxy http://127.0.0.1:7897 --list-formats "<链接>"
./run.sh -c "import sys;print(sys.version)"          # 执行一段代码
./run.sh - <<'PY'                                     # 从 stdin 执行
print("hi")
PY

# 前端（改了前端必须重新构建，否则页面不变）
cd frontend && npm run dev      # 开发模式（API 代理到 8765，需另开 ./run.sh）
cd frontend && npm run build    # 产物输出到 ../static
```

**验证风格要求**：本项目所有结论都来自实测（ffprobe 校验、像素对比、接口探测），
不要凭推测下结论。改动后至少跑一次离线自测；涉及站点行为的改动要做真实下载验证。

---

## 6. 已踩过的坑（**不要重复踩**）

### 6.1 yt-dlp / 引擎层

| # | 坑 | 现象 | 解法 |
|---|---|---|---|
| 1 | `--print` 与进度输出**互斥** | 加了 `--print` 就收不到进度行 | 用 `--print-to-file` 旁路写产物路径 |
| 2 | `--print-to-file` 会**吃掉进度模板的字面前缀** | 期望 `DL\|12%...`，实得 ` 12%...` | 进度解析**按结构识别**，不按前缀；且字段可能是 `NA` |
| 3 | `--print-to-file` 是**追加写入** | 跨尝试/跨任务读到别人的路径 → 误报成功 | 每次执行前 `unlink` 结果文件 |
| 4 | 编码偏好会影响成功率 | 某些 CDN 拒绝 AV1 流 | H.264 优先，失败换编码（`STRATEGY_PATCHES`） |
| 5 | 失败后残留 `.part`/分片 | 重试继续撞同一条坏流 | 重试前 `_cleanup_fragments()` |
| 6 | 新版 yt-dlp **默认只启用 deno** | YouTube 报 `The page needs to be reloaded.` | `detect_js_runtime()` 自动注入 `--js-runtimes node` |

### 6.2 抖音层（最容易失效，改动前必读 `docs/TROUBLESHOOTING.md`）

| # | 结论 | 细节 |
|---|---|---|
| 1 | Web 业务接口**全部要签名** | `www.douyin.com/aweme/v1/web/**` → 403 或 `encrypt_data_miss`；yt-dlp 撞的就是这堵墙 |
| 2 | **App 端域名多数免签名** | `api5-normal-c-hl.amemv.com` / `aweme.snssdk.com` —— 视频走 Feed 接口，直链走 `aweme/v1/play/` |
| 3 | 分享页必须带**游客 cookie** | 否则只返回风控壳页（`_$jsvmprt`/captcha）；游客 cookie 访问首页即可白拿，**用户无感** |
| 4 | 图文帖数据在**分享页**，不在 Feed | Feed 对图文帖只回推荐流 → 必须降级到 `/share/video/<id>/` 解析 `images` |
| 5 | **必须用 `play` 不能用 `playwm`** | 像素对比实测：`play`=1080p 无水印；`playwm`=固定 720p 且带水印 |
| 6 | 原图在 `url_list` 的 `.jpeg` | 199.8KB 无水印；`download_url_list` 模板名带 `-water`，**带水印**，已排除 |
| 7 | 图文帖**没有视频流** | 帖子级 `video` 只有封面（`bit_rate` 为空）；那个 `play_addr` 其实是配乐 mp3 |
| 8 | 图文帖要**本地合成视频** | ffmpeg 图片轮播 + 配乐；时长取 `music.duration` |
| 9 | 网络抖动常见 | `http.client.IncompleteRead` 必须收敛为可重试错误（已加 3 次重试） |
| 10 | **配乐不能只靠一条正则去页面里搜** | 旧正则要求「域名含 `music` + `.mp3` 结尾」→ 只有域名恰好含 music 的帖子有声。配乐域名在 `lf26`/`sf9`/`sf11`/`sf6` 间漂移，且可能是**无扩展名 OSS key**（`audio/mp4`）。→ 改走内联 JSON + 多候选 + 按 `Content-Type` 探测（见下条） |
| 11 | 配乐**稳定来源**是 `item.video.play_addr.uri` | `music.play_url` 在实测三条帖子里**全是空**；图文帖的"播放地址"就是配乐。解析入口 `extract_item()` |
| 12 | 配乐**按响应类型定扩展名** | `audio/mpeg → .mp3`；`audio/mp4` 或无扩展名 + `video/mp4 → .m4a`。只看 URL 后缀会把无扩展名 m4a 当成非音频丢掉（这就是 `GUNTbne404o` 无声的原因） |
| 13 | 单个配乐候选可能失效 | 403 / 类型不对 / 文件损坏都要**自动换下一个候选**，全部失败才报错并让用户感知 |

### 6.3 判定正确性（曾误报成功，危害最大）

| # | 坑 | 解法 |
|---|---|---|
| 1 | 播放列表**部分失败仍返回退出码 0** | `_finalize()` 核对产物数 vs 预期条目数 → 标「部分完成」 |
| 2 | 兜底匹配会把**同名视频当成音频产物** | 严格限制扩展名 + 时间窗（`_match_existing`） |
| 3 | 抖动自愈被误判为失败 | 产物数达标即清零失败计数 |
| 4 | 合成视频**丢了配乐却报成功** | 合成后 `_probe_streams()` 校验含音轨；配乐下载走任务代理；损坏文件直接报错 |

### 6.4 工程层

| # | 坑 | 解法 |
|---|---|---|
| 1 | bash 中 `$VAR` 紧跟**全角字符**会被并入变量名 | 一律写 `${VAR}` |
| 2 | 线程池异常被静默吞掉，任务永远「进行中」 | `_run()` 全链路兜底 try/except，堆栈写任务日志 |
| 3 | `setup.sh` 曾用 `PYVER=3.12.8` 当**目录名**（实为 `3.12`） | 引入 `PY_SERIES`；且改为「先校验后替换」 |
| 4 | 幂等分支让 bug 在开发机隐藏 | 交付前必须做**全新 clone 验证** |
| 5 | 后台测试实例残留占端口 | **测试后用 `pkill -f "app.py --port <端口>"` 回收**；启动已加端口预检 |

---

## 7. 修改约定（接手时请遵守）

### 代码风格
- **后端**：Python 3.12 语法（`str | None`），注释解释**为什么**而非复述代码，
  关键约束必须写进注释（例如"`--print-to-file` 是追加写入"）
- **前端**：Vue 3 `<script setup>`，模板/逻辑/样式同文件，scoped 样式
- **错误处理**：用户可见的错误必须能落到"我该做什么"，原始报错保留便于搜索

### 测试要求
- 新增/修改行为 → **必须补断言**（现有 27 个分组，按主题归入）
- 涉及"判定正确性"的逻辑 → 必须有**反向用例**（例如：抖动自愈不能误报部分完成）
- 涉及网络的用例放 `--live` 分支，避免离线自测被网络拖垮
- 创建 `TaskManager` 后记得 `shutdown()`；`create()` 后不要断言初始状态是 `queued`

### 交付要求
- 改动后跑：`./run.sh -m compileall -q core app.py` + `./run.sh --py tests/run_tests.py`
- 改前端 → `cd frontend && npm run build` 并提交 `static/`
- 同步文档：相关 `docs/*.md` + `docs/CHANGELOG.md` + **本文件**
- 提交信息用中文，说明**背景/根因/改动/实测**（参考已有 13 个提交的写法）

### 沟通约定（用户偏好，来自历史交互）
- 用户要求"**先给方案、经确认再改代码**"——**重大改动必须先出方案并等确认**，
  不要在未确认时直接动代码（这条被明确强调过）
- 用户偏好**根因分析 + 实测证据**，不接受"可能是"的结论
- 用户会明确说"不用管某功能"，也接受"实现不了 + 原因说明"（例如抖音早期结论）
- 报告要**如实区分"已验证/未验证"**（历史上多次因此补充验证）

---

## 8. 推送与远端（有坑）

git 全局配置把 github.com 改写到已失效的 `ghproxy.com`，**直接 `git push` 会失败**。
可用方式（已多次验证）：

```bash
# 方式一：屏蔽全局配置 + 走用户代理（推荐，成功率最高）
GIT_CONFIG_GLOBAL=/dev/null git -c http.proxy=http://127.0.0.1:7897 \
  -c https.proxy=http://127.0.0.1:7897 push

# 方式二：屏蔽全局配置，直连
GIT_CONFIG_GLOBAL=/dev/null git push

# 用户永久修复（需其本人决定，不要擅自改）
git config --global --unset url.https://ghproxy.com/https://github.com/.insteadof
```

**代理会抖动**：推送可能偶发失败（`SSL_ERROR_SYSCALL`），**重试 2-4 次通常即可成功**。
推完用 `gh api repos/Light-ux-source/video_downloader/commits --jq '.[0].sha[0:7]'`
核对本地与远端是否一致。

> ⚠️ `git -c url.<base>.insteadof=` **无法**清空多值配置，必须用 `--unset` 或屏蔽全局配置。

---

## 9. 文档索引（各文档职责，避免重复）

| 文档 | 给谁看 | 内容 |
|---|---|---|
| **本文件** | **AI 接手** | 进度、坑、约定、推送方式 |
| [`README.md`](../README.md) | 用户 / 访客 | 是什么、快速开始、功能表、文档索引 |
| [`docs/USAGE.md`](USAGE.md) | 用户 | 界面说明、快捷键、参数详解、6 个场景 |
| [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) | 开发者 | 分层、模块职责、任务生命周期、扩展指引 |
| [`docs/API.md`](API.md) | 集成方 | 全部接口、30+ 字段表、SSE 协议、错误码 |
| [`docs/CONFIGURATION.md`](CONFIGURATION.md) | 运维 / 用户 | `VH_*` 环境变量、目录、代理与 Cookies |
| [`docs/DEVELOPMENT.md`](DEVELOPMENT.md) | 开发者 | 目录结构、改动指引、测试说明、调试技巧 |
| [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) | 用户 / 开发者 | 站点限制、报错对照表、FAQ、**抖音通路原理** |
| [`docs/DECISIONS.md`](DECISIONS.md) | 开发者 | 14 项 ADR（背景/决策/理由/代价）+ 未采纳方案 |
| [`docs/CHANGELOG.md`](CHANGELOG.md) | 开发者 | 11 个阶段的迭代与全部已修问题 |
| [`design-system/README.md`](../design-system/README.md) | 前端 | 真实设计令牌（MASTER.md 是自动生成快照，**不代表实现**） |

---

## 10. 下一步建议（按优先级）

1. **补真实验证**（最高优先，都是"代码已写但没真样本"）：
   - 已补：**5 图真实图文帖**（`0JWrSFgCzV0`）节奏验证通过
   - 仍缺：一条**带实况动图**的帖子、一条**有正常字幕**的视频
2. **P1 · 图文帖图片素材下载健壮性**（用户已确认后续再做）：
   目前每张图只试 `url_list[0]` 一次，其余 3–4 条等价 CDN 链接未轮换/补试；
   抖动网络下多图帖更容易出现"第 N 张失败"→ 标「部分完成」
3. **P2 · 图文帖节奏优化**（等用户拍板）：目前按配乐时长均分，可能要改成固定每张 N 秒
4. **抖音合集 / 用户主页**（可行性中等）：`sec_uid` 已能从分享页拿到，
   需要找到免签名的作品列表接口（App 端 `aweme/post/` 返回空，需继续试参数）
5. **字幕内嵌**：确认普通字幕（SRT/ASS）能否成功内嵌，补真实案例
6. **长期维护**：抖音通路依赖"新旧 API 并存"窗口期，需定期回归；
   若失效，参考 `docs/TROUBLESHOOTING.md` 的探测方法重新找通路。
   特别注意：**配乐相关字段（`play_addr.uri` 的语义、`music.play_url` 是否回归）
   是最易变的部分**，改动前先按第 6.2 节第 10–13 条做真实链接实测

> 阶段 11 的修复（配乐多源候选 + 按响应类型探测）**尚未提交/推送**，
> 接手时先 `git status` / `git log -1` 确认。
