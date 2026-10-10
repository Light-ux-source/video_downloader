# 变更记录

记录每一轮迭代做了什么、为什么。项目尚未发布正式版本，按开发阶段分组。

---

## 阶段 7：抖音图文帖（图集）支持

**背景**：用户反馈"只有图文类解析不成功，其他形式都能无水印下载"，并提供了一条真实图文帖链接。

### 调研过程

先穷举免签名路径，确认**图文帖数据在哪里**：

| 尝试 | 结果 |
|---|---|
| App Feed 接口 | ❌ 对图文帖只返回推荐流，不含目标数据 |
| App detail 接口 | ❌ HTTP 200 但空响应 |
| Web detail 接口（自注册游客 ttwid） | ❌ 403 `Blocked by ArgusSecurityPlugin Uifid Not Found` |
| **SSR 分享页 `/share/video/<id>/`** | ✅ **`images` 数组内联在页面里** |

同时对比了两个开源实现：

- [YusongXiao/douyin_phaser](https://github.com/YusongXiao/douyin_phaser)：用真实 Chrome + DOM 抽取
- [belingud/douyin-downloader-skill](https://github.com/belingud/douyin-downloader-skill)：
  自实现 `a_bogus` 签名（434+120 行，Apache-2.0）调 Web detail 接口

**最终选择分享页方案**：已实测可用，且**不需要引入签名代码**、不需要浏览器自动化。

### 关键实测结论

| 项 | 结论 |
|---|---|
| 原图在哪 | `images[].url_list` 的 **`.jpeg`**：199.8KB、1000×1573、**无水印** |
| 哪个不能用 | `download_url_list` 模板名带 `-water`：129.4KB webp、**带水印**，已排除 |
| 存放方式 | `downloads/{标题}/图片1.jpeg`、`图片2.jpeg`… |

### 交付

- `core/douyin.py`：新增 `parse_images_array()`（括号配平解析内联 JSON）、
  `rank_image_urls()`（jpeg 原图优先）、`extract_gallery()`
- `core/tasks.py`：新增 `_download_douyin_gallery()` 原生逐张下载（图片不是流媒体，
  不走 yt-dlp），并复用统一的收尾校验
- `frontend/InspectPanel.vue`：新增图集展示（图片网格 + 逐张尺寸 + 「下载全部 N 张」）
- **修了一个连带 bug**：收尾时的"按标题重命名"会把图集的 `图片1.jpeg` 改成 `标题.jpeg`，
  破坏逐张编号；现在检测到文件已在以标题命名的子目录里就跳过重命名

### 实测

| 场景 | 结果 |
|---|---|
| 图集解析（用户提供的链接） | ✅ 3.6s，识别为 `kind=gallery`，1 张 1000×1573 原图 |
| 图集下载 | ✅ `downloads/#厦门/图片1.jpeg`，199.8KB，无水印 |
| 抖音视频（回归） | ✅ 仍正常，1080p |
| B 站 / YouTube（回归） | ✅ 15 / 80 个画质 |
| 自测 | ✅ 159 → **177 项**全部通过 |

---

## 阶段 6：抖音通路升级（App Feed 接口）+ 不可下载场景的精确提示

**背景**：用户反馈"视频不支持下载时解析不成功"，需要继续找方案。

### 调研

搜索社区后参考了 [media-parser](https://github.com/ucmao/media-parser) 的实现思路，
其源码注释指出一条**免 ArgusSecurityPlugin 门禁的主路径**：
移动端 `aweme/v1/feed/` 接口配 App 形态 UA。实测验证后确认可用，且比原先的
SSR 分享页方案更优。

### 升级内容

1. **改用 App Feed 接口作为主路径**
   `https://api5-normal-c-hl.amemv.com/aweme/v1/feed/?aweme_id=<id>`（App UA）
   - 免签名免 Cookie，`status_code: 0`
   - 带 `bit_rate[]`：**精确分辨率 + data_size + 直连 url_list**
   - 解析从 8.5s 降到约 2.7s
2. **精确判断"视频本身不支持下载"**：`aweme_list` 为空 → 抛 `DouyinUnavailable`，
   给出"已删除 / 私密 / 仅部分可见 / 地区限制"的可读原因。
   不再出现误导性的 `Fresh cookies are needed`，也不会冒泡成"服务内部错误"。
3. **保留 SSR 分享页方案作为兜底**，主路径失效时自动降级；
   两条都失败时会把两条路径的原因一起告诉用户。
4. **互补画质**：feed 的 bit_rate 最高通常 720p，请求 1080p 时用
   `play_addr.uri` 走播放接口补一条更高档直链（实测可拿到 1920×1080）。
5. **档位选择修正**：请求低于最低档（如 360p 而最低 576p）时取**最小档**，
   而不是退回最高档——避免"想省流量却下了最大文件"。
6. **网络健壮性**：把 `http.client.IncompleteRead` 等传输层异常收敛为
   `DouyinError`，并对 feed / 分享页 / 播放接口加**自动重试**（最多 3 次，指数退避）。
   实测曾因响应体截断导致任务失败，修复后稳定。
7. **图文/图集明确提示**：检测到 `images` 字段时提示"这是图文/图集内容，没有视频流"，
   不误报成视频解析失败。

### 实测

| 场景 | 结果 |
|---|---|
| 正常视频解析 | ✅ 约 2.7s（旧方案 8.5s） |
| 1080p 下载 | ✅ 49.29MB，1920×1080 |
| 720p 下载 | ✅ 20.42MB，1280×720 |
| 480p 下载 | ✅ 13.81MB，1024×576 |
| 不存在的视频 | ✅ 2.6s 内明确提示"不支持下载"，不再误导 |
| 回归 | ✅ B 站 15 个画质、YouTube 80 个画质正常 |

自测从 144 项增至 **159 项**。

---

## 阶段 5：抖音免 Cookie 支持（重难点攻坚）

**背景**：用户实测抖音报 `Fresh cookies are needed`，且明确要求"必须实现、且不需要用户提供 Cookie"。

### 定位：确认 yt-dlp 侧无解

穷举抖音所有接口（详见 [TROUBLESHOOTING](TROUBLESHOOTING.md#抖音已支持免-cookie-直连通道)）：

- Web/移动端 detail 接口 → **403**
- 旧版 iteminfo 接口 → `{"status_code": 11110, "status_msg": "encrypt_data_miss"}`
- 分享页无游客 Cookie → 风控壳页；页面直连 → 反爬 JS-VM
- 确认 PyPI 最新版 yt-dlp 的抖音提取器仍是未实现的 TODO

结论：抖音要求 `a_bogus`/msToken 签名，签名由页面 JS-VM 动态生成，**yt-dlp 与"升级引擎"都无解**。

### 方案：从开源社区找到突破口并落地

参考社区实践（[`douyin-nocookie-approach`](https://github.com/cat-xierluo/legal-skills/blob/main/skills/universal-media-downloader/references/douyin-nocookie-approach.md)）
给出的关键线索——**App 端旧播放直链接口不受签名墙保护**——实测验证并补齐了缺失环节：

```
① 访问首页拿游客 Cookie（ttwid/__ac_nonce，只是设备标识，非登录态）
② 带游客 Cookie 请求 SSR 分享页 → 提取 play_addr.uri 作为 video_id
③ aweme.snssdk.com/aweme/v1/play/?video_id=<id>&ratio=<档位> → 302 到 CDN 直链
④ 带 Referer 下载
```

**关键发现（社区文档未覆盖的部分）**：

1. **分享页必须带游客 Cookie**，否则只返回风控壳页——社区文档记录该页已失效（2026-08-30），
   实测发现加上游客 Cookie 后数据照常渲染，通路重新可用。
2. **必须用 `play`，不能用 `playwm`**：通过抓取同一时间点画面做像素对比确认——

   | 接口 | 分辨率 | 水印 |
   |---|---|---|
   | `play` | 1920×1080 | **无水印** |
   | `playwm` | 1280×720（固定，ratio 无效） | 左上角带抖音号与昵称水印 |

### 交付

- 新增 `core/douyin.py`（约 300 行，含详细注释说明通路原理与边界）
- 接入 `probe()`（解析结果直接给前端）与 `_resolve_douyin_direct()`（下载时换直链 + 带 Referer）
- 对 CDN 直链乱码文件名做**按标题重命名**（通用能力，不只抖音受益）
- 新增 23 项单元测试；自测从 126 项增至 **144 项**

### 实测结果

| 场景 | 结果 |
|---|---|
| 解析（你的链接） | ✅ 2.2 秒，无 Cookie，标题/video_id/档位齐全 |
| 1080p 下载 | ✅ 49.29MB，1920×1080 h264+aac，**无水印** |
| 720p 下载 | ✅ 35.42MB，1280×720，无水印，文件名 = 视频标题 |
| MP3 提取 | ✅ 3.21MB，自动嵌入封面 |

### 风险提示

该通路依赖抖音"新旧 API 并存"的窗口期（旧播放接口为兼容老客户端而未加签名）。
若抖音下掉该接口或给分享页也加签名，通路会失效——届时程序会给出明确报错，
而不是含糊的上游提示。

---

## 阶段 4：仓库化与文档沉淀

**目标**：初始化 git 仓库、统一权限、把散落的知识沉淀成文档。

### 通过「全新 clone 验证」抓出的 bug

推送前做了一次真实的全新 clone 验证（clone → setup.sh → 自测 → 启动 → 真实解析），
在第一步就发现 `setup.sh` 有 bug：

- **症状**：全新环境下 `setup.sh` 报「安装包解包结果异常」，清缓存重新下载 46MB 后
  仍然失败；而在已装好运行时的仓库里执行却"正常"（因为走了跳过分支）。
- **根因**：`PYVER="3.12.8"` 被用在**目录名**上，但 python.org 安装包内的目录是
  `Versions/3.12`（大版本号）。于是
  `[ ! -x "$NEW_PAYLOAD/Versions/3.12.8/bin/python3" ]` 永远成立 → 判定解包失败 →
  删缓存重新下载 → 再失败。这也解释了此前那次"运行时被误删"。
- **修复**：引入 `PY_SERIES="${PYVER%.*}"` 专门用于目录名与软链，
  `PYVER` 只用于下载地址与缓存文件名；并在注释里写明两者区别。
- **验证**：全新 clone 从零 `setup.sh` 成功；再执行一次正确跳过解包（不再重复下载）。

**教训**：幂等分支让 bug 在开发机上长期隐藏——只有真正从零走一遍才会暴露。
这也是本次坚持做 clone 验证的原因。

### 仓库化动作

- 初始化仓库（默认分支 `main`），配置 `.gitignore`
  （排除 `runtime/`、`downloads/`、`node_modules/`、cookies、系统文件；
  **保留** `static/` 构建产物，让 clone 后免构建即可运行）
- 统一文件权限（源码 644、脚本 755）——原先 `run.sh` 是 711、源码是 600，
  其他人 clone 后会无法读取/执行
- 文档体系化：拆分并新增
  [USAGE](USAGE.md)、[ARCHITECTURE](ARCHITECTURE.md)、[API](API.md)、
  [CONFIGURATION](CONFIGURATION.md)、[DEVELOPMENT](DEVELOPMENT.md)、
  [TROUBLESHOOTING](TROUBLESHOOTING.md)、[DECISIONS](DECISIONS.md)、CHANGELOG（本文）
- 提交前安全自查：确认仓库内无 cookies、token、大文件

---

## 阶段 3：真实环境联调（用户实测的两个报错）

**背景**：用户在高级参数配置了代理（`127.0.0.1:7897`）与 Chrome Cookies，
YouTube 报 `The page needs to be reloaded.`，抖音报 `Unsupported URL`。

### 定位过程（先说清"不是配置问题"）

| 对照实验 | 结果 | 结论 |
|---|---|---|
| 代理端口 / 连通性 | 端口开放、可访问 YouTube | 代理配置正确 |
| 仅代理（无 cookies） | ✅ 解析成功 | 代理没问题 |
| 仅 cookies（无代理） | ❌ 报同样的错 | 复现 |
| 代理 + cookies | ❌ 报同样的错 | 复现 |
| `-v` 详细日志 | `JS Challenge Providers: bun/deno/node/quickjs (unavailable)` | **根因：JS 挑战无法求解** |
| 加 `--js-runtimes node` | 0 个画质 → **19~80 个画质** | 确认根因 |

### 修复

1. **自动探测并注入 JS 运行时**（`detect_js_runtime()` / `js_runtime_args()`）
   —— 新版 yt-dlp 默认只认 `deno`，用户装的是 `node`，必须显式声明。
   现按 node → deno → bun → qjs 探测并自动注入；无运行时时自动跳过。
2. **代理地址容错**（`sanitize_proxy()`）—— `127.0.0.1:7897` 自动补 `http://`。
3. **抖音链接纠正**（`normalize_url()`）—— `/note/<id>` 自动改写为 `/video/<id>` 再尝试，
   并带上说明，避免用户看到含糊的 `Unsupported URL`。
4. **错误信息中文化**（`friendly_error()`）—— 14 类引擎报错映射为可操作中文提示，
   同时**保留原始报错**便于搜索与提 issue。
5. **重试退避**（`RETRY_BACKOFF_SECONDS`）—— 原先是零间隔连打，代理隧道瞬时抖动时
   会连着撞三次；现改为 5s/10s/15s 退避，且可被取消打断。
6. **修复错误提取 bug** —— `_last_error()` 原先只取最后一行，会把中文提示**当成错误本身**，
   导致用户看不到真正的报错。现改为"错误行 + 紧随提示"成对提取。
7. **健康检查暴露 `js_runtime`** —— 启动自检、`/api/health`、界面都能看到状态。

### 验证

- YouTube（用用户配置，不传额外参数）：解析成功 80 个画质，并**真实下载完成**
  （44.3s / 1.28MB，`ffprobe` 确认 video:h264 + audio:aac）
- 任务命令中确认出现 `--js-runtimes node:/opt/homebrew/bin/node` 与 `--proxy http://127.0.0.1:7897`
- 抖音 `/note/`：自动按 `/video/` 尝试后给出明确中文提示
- 新增工具 `tools/verify_env.py`（环境自查 + 真实解析验证）
- 自测从 103 项增至 **126 项**

### 阶段 3 顺带发现

- 用户代理出现过多次**上游隧道瞬时中断**（`curl` 也失败、客户端仍在监听），
  属网络环境波动；已用退避重试缓解，非代码问题。

---

## 阶段 2：播放列表与"部分失败"判定

**背景**：目标复核时发现「播放列表」功能只写了代码、没做端到端验证。

### 修复（3 个真 bug）

1. **播放列表部分失败被误报成功**（最严重）
   yt-dlp 对列表内某个条目失败仍返回退出码 0，导致"下 2 个、坏 1 个"被标成「已完成」。
   → 新增 `_finalize()` 收尾判定：读回结果文件**全部行**、逐行校验产物真实存在、
   对比播放列表预期条目数，不足则标 `done + incomplete`，界面显示「部分完成」。
2. **抖动自愈被误判为失败**
   日志出现 `Giving up after N retries` 但后续策略补齐了文件，仍被计为失败条目。
   → 以"实际完成条目数是否达标"为准，达标即清零失败计数。
3. **结果文件只读最后一行**
   播放列表会写入多行，只读最后一行会漏判失败条目。
   → 新增 `read_result_files()`，`read_result_file()` 保留为兼容包装。

### 同时改进

- 条目计数由"合流完成"驱动（原先会把视频流/音频流各算一次，出现 `4/2` 的错乱）
- 任务卡片新增展示：条目进度 `1/2`、产物文件数、`存在未完成条目` 徽标

### 验证

- `bilibili.com/list/1`（2 条目）完整下载成功 → `全部完成：共 2 个条目`
- 刻意制造网络失败 → 正确显示 `部分完成：成功 1 个条目，另有条目未完成`
- 混合批量（正常视频 + 必然 404 链接）→ 状态互不污染
- 自测从 96 项增至 103 项（后随阶段 3 增至 126 项）

---

## 阶段 1：开发与自测

按方案完成 MVP 与用户选定的扩展功能。

### 交付

- 后端：`app.py` + `core/{config,ytdlp,tasks,server}.py`（纯标准库）
- 前端：Vue 3 + Vite，暗色霓虹 HUD 风格（不对称三栏、命令行输入框、
  ASCII 进度条、扫描线、任务日志展开、无障碍达标）
- 环境准备：`setup.sh`（自带 Python 3.12 运行时 + yt-dlp）、`run.sh` 包装器
- 自测：`tests/run_tests.py`

### 开发中通过真实测试暴露并修复的 6 个 bug

1. **进度行解析失败** —— 与 `--print-to-file` 同时使用时，yt-dlp 会把进度模板的
   字面前缀（`DL|`）一起吃掉，行退化为 ` 12.1%|8423526|...`，且字段可能为 `NA`。
   → 解析器改为**按结构识别**并容忍 `NA`。
2. **最终产物定位不到** —— `--print` 与进度输出互斥（实测）。
   → 改用 `--print-to-file` 旁路回写。
3. **结果文件跨任务污染** —— 该选项是**追加**写入，跨尝试/跨任务会读到别的任务写的路径，
   导致误报成功。→ 每次执行前清空结果文件。
4. **音频任务误认视频产物** —— 兜底匹配只看标题前缀，把同名 `.mp4` 当成 MP3 的产物。
   → 严格限制扩展名 + 时间窗约束。
5. **失败后反复撞同一条坏流** —— 残留 `.part` / 分片导致重试继续失败。
   → 重试前清理分片（`.f<id>.` 与 `.part`）。
6. **编码偏好导致下载失败** —— 某些 CDN 拒绝 AV1 流。
   → 编码偏好改为 H.264 优先，失败时自动换编码，并新增多策略降级。

### 工程性问题（3 个）

- `setup.sh` 幂等检查直接用 framework 里的 `python3` 探活会 Abort，
  导致**误删可用运行时**；且"先删后校验"会在失败时把环境弄坏。
  → 改为通过 `run.sh` 探活 + **先校验后替换** + 缓存自愈。
- bash 中 `$VAR` 紧跟全角字符时，多字节字符会被并入变量名（`set -u` 直接报错）。
  → 全局加花括号加固。
- 线程池内未捕获异常会被静默吞掉、任务永远停在「进行中」。
  → 全链路兜底 `try/except`，堆栈写入任务日志。

### 实测结果

- B 站 212 秒 MV：360P/480P/720P/1080P 视频、MP3 音频均成功；
  `ffprobe` 校验容器/时长/编码/分辨率正确；MP3 自动嵌入封面
- SSE：实测 21 个单调递增的进度事件（含百分比、速度、ETA）
- 取消：进程组干净退出、无残留进程、`.part` 保留
- 续传：日志出现 `Resuming download at byte 866308`
- 永久性错误：快速失败并给出可读原因

---

## 阶段 0：方案与选型

- 需求确认：基于 yt-dlp 二次封装、Python 技术栈、无数据库、前端要独特
- 方案评审：用户选定「Vue 3 + Vite」「暗色霓虹 HUD 风」「MVP + 字幕内嵌」
- 环境实测：Python 3.9.6 / ffmpeg 8.1.2 / node v26 / GitHub Release 不可达 / PyPI 镜像可用
- 两处方案调整（详见 [DECISIONS](DECISIONS.md)）：
  1. 引擎改用 PyPI 包（GitHub 二进制不可达）
  2. 项目内自带 Python 3.12 运行时（系统 3.9 过旧，且要零 sudo、零污染）

---

## 尚未完成 / 已知限制

| 项 | 状态 | 说明 |
|---|---|---|
| 抖音下载 | ❌ 不可用 | yt-dlp 上游未实现 `a_bogus`/`msToken` 签名，见 [排障文档](TROUBLESHOOTING.md#抖音不可用原因) |
| 字体内嵌实测 | ⚠️ 部分验证 | 测试用 B 站视频只有"弹幕"（私有 XML，ffmpeg 无法内嵌），普通字幕未在真实站点验证成功案例 |
| 远程仓库 | 待推送 | 本地仓库已初始化并提交；是否推送到 GitHub 待确认 |
| 多用户 / 鉴权 | 未做 | 定位是单机自用，仅监听回环地址 |
| Windows / Linux 支持 | 未做 | `setup.sh` 是 macOS 专属方案 |
