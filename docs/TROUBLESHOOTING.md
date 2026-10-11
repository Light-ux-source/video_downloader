# 排障指南

> 🤖 开发者/接手者：项目级坑清单与修改约定在 [AI_CONTEXT.md](AI_CONTEXT.md#6-已踩过的坑不要重复踩)，
> 本文档面向"使用中遇到问题"的排查。
>
> 遇到问题先跑自查工具，多数情况它能直接指出原因：
> ```bash
> ./run.sh --py tools/verify_env.py
> ./run.sh --py tools/verify_env.py --url "<出问题的链接>" --proxy 127.0.0.1:7897 --browser chrome
> ```

---

## 已知站点限制

| 站点 | 状态 | 说明 |
|---|---|---|
| YouTube | ✅ 可用 | **需要 node**（JS 挑战）+ 代理；部分视频需要 Cookies |
| Bilibili | ✅ 可用 | 1080P 高码率需会员 Cookies；弹幕是私有 XML 格式，无法内嵌 |
| 微博 / 小红书 / 部分国内站 | ⚠️ 视情况 | 通常需要 Cookies |
| 抖音 | ✅ 可用（免 Cookie） | 内置专用通道，无水印原画；见下节 |

### 抖音：已支持（免 Cookie 直连通道）

**状态**：✅ 可用。yt-dlp 自身仍然不支持抖音（撞签名墙），本项目内置了专用通道绕开它。

#### 为什么 yt-dlp 不行

yt-dlp 的抖音提取器走 `www.douyin.com/aweme/v1/web/aweme/detail/` 这类 Web 业务接口，
而这些接口现在要求 `a_bogus` / `msToken` 请求签名。实测结果：

| 尝试 | 结果 |
|---|---|
| Web detail 接口（带完整参数 + 代理 + cookies） | HTTP 403 |
| 移动端 detail 接口 | HTTP 403 |
| 旧版 `iteminfo` 接口 | 200 但返回 `{"status_code": 11110, "status_msg": "encrypt_data_miss"}`（缺加密参数） |
| 分享页（无游客 Cookie） | 风控壳页（captcha/slardar），无数据 |
| 页面直连 | 返回反爬 JS-VM（`_$jsvmprt`） |

`encrypt_data_miss` 是决定性证据：请求必须携带签名，而签名由页面 JS-VM 动态生成。
yt-dlp 源码里那行 `# TODO: Run verification challenge code to generate signature cookies`
至今仍在，因此升级引擎也不会好。

#### 本项目采用的通路（无需登录、无需用户 Cookie）

**主路径：移动端 App Feed 接口**（实测免签名、免 Cookie）

```
GET https://api5-normal-c-hl.amemv.com/aweme/v1/feed/?aweme_id=<数字id>
    UA: com.ss.android.ugc.aweme/290101 (...Android...)
```

这条接口的优势（实测）：

| 特性 | 说明 |
|---|---|
| 免签名免 Cookie | `status_code: 0`，直接返回该视频完整详情 |
| 信息更全 | 带 `bit_rate[]`，每个档位都有**精确分辨率与 data_size** |
| 可直接下载 | 档位里的 `url_list` 就是 CDN 直链，省掉一次跳转 |
| **能精确判断"不可下载"** | 视频被删/私密/地区限制时 **`aweme_list` 为空**，据此可给出明确原因 |

**兜底路径：SSR 分享页 + App 播放直链**

```
① 访问 douyin.com / iesdouyin.com 首页拿游客 Cookie（ttwid）
   —— 只是设备标识，不是登录态
② 带游客 Cookie 请求 /share/video/<数字id>/ → 页面内联渲染 play_addr.uri
   （不带游客 Cookie 只会拿到风控壳页，这是最容易踩的坑）
③ aweme.snssdk.com/aweme/v1/play/?video_id=<uri>&ratio=<档位> → 302 到 CDN 直链
```

两条路径互补：feed 的 `bit_rate` 通常最高到 720p，而播放接口能拿到 1080p，
因此请求高画质时会用 `play_addr.uri` 再换一条更高档的直链。

**关键点**：

- **必须带游客 Cookie 请求分享页**（兜底路径）：不带只会拿到风控壳页。
  游客 Cookie 访问首页就能白拿，**用户完全无感，不需要提供任何 Cookie**。
- **必须用 `/aweme/v1/play/`，不要用 `/aweme/v1/playwm/`**：实测像素对比确认——

  | 接口 | 分辨率 | 水印 |
  |---|---|---|
  | `play` | **1920×1080** | **无水印** |
  | `playwm` | 1280×720（固定，ratio 参数无效） | 左上角带「抖音 + 抖音号 + 昵称」水印 |

- 档位映射：`best/1080 → 1080p`、`720 → 720p`、`480/360 → 540p`（接口最低档）。
  请求低于最低档时取最小档，而不是退回最高档。
- CDN 直链有效期约 1–2 小时，解析与下载在同一流程内完成。

#### 图文帖（图集）支持

**状态**：✅ 已支持，逐张下载**无水印原图**。

实现要点（实测得出）：

| 项 | 结论 |
|---|---|
| 数据来源 | SSR 分享页 `/share/video/<id>/` —— **注意仍用 video 路由**，图文帖也走这个路由 |
| 是否需要签名 | ❌ 不需要。`a_bogus` / `Uifid` 那套只用于 Web detail 接口，图文帖数据直接内联在分享页里 |
| 原图怎么选 | `images[].url_list` 里的 **`.jpeg`** —— 实测 199.8KB、1000×1573、**无水印** |
| 不要用哪个 | `download_url_list` 的模板名带 `-water`，实测**带水印**（129.4KB webp），已明确排除 |
| **输出什么** | **视频**：图文帖本身没有视频流，但客户端播放的是「图片轮播 + 配乐」，因此本地用 ffmpeg 合成 `{标题}.mp4` |
| 配乐从哪来 | 分享页 `music` 对象里的 `.mp3` 直链；`music.duration` 就是客户端播放时长，用作合成视频总时长 |
| 存放方式 | `downloads/{标题}/{标题}.mp4` + 保留原图 `图片N.jpeg` |
| 实况照片 | `images[].video.play_addr.url_list` 存在即为动图（抖音的动图就是 mp4），另存为 `动图N.mp4`；若全部是动图则跳过合成 |

> 注意：App Feed 接口对图文帖**不返回目标数据**（只回推荐流），因此不能因为
> "Feed 里没命中"就判定作品不可用——必须降级到分享页路径再判断。
> 这个坑已写进代码注释与单元测试。

#### 图文帖合成视频没有声音？

**已修复（本版本）**。历史上可能无声的三条路径已全部堵死：

| 场景 | 旧行为 | 现行为 |
|---|---|---|
| 素材下载没走代理（配乐 CDN 在部分网络需代理） | 静默降级为无声视频 | 素材下载统一走任务配置的代理与游客 cookie |
| 配乐文件下载不完整 / 不是音频 | ffmpeg 丢弃音轨，仍报成功 | 合成前校验配乐可解码，坏文件直接报错 |
| 合成命令未显式指定音频流 | 默认流选择可能丢音轨 | `-map 0:v:0 -map 1:a:0` 显式映射 |
| 配乐短于视频 | `-shortest` 可能截断 | `-af apad` 补静音，不截断 |
| 合成后仍无音轨 | 当作成功 | ffprobe **校验产物含音轨**，失败则换方案重试（copy → 重编码）；仍失败则任务标记未完成并给出原因 |

**遇到无声视频时怎么判断**：看任务卡片「查看日志」，会有明确标注，例如

```
# 配乐已下载：416.0KB
# 已合成视频：#厦门.mp4（1 张图 · 26.0s· 含配乐· copy）
```

若配乐环节有问题，会看到 `# ⚠ 配乐下载失败，视频将没有声音：...`，
且任务状态会标为「部分完成」，不会再出现"以为有配乐实际没有"的情况。

自行验证产物是否真的有声音：

```bash
ffprobe -v error -show_entries stream=codec_type,codec_name -of csv=p=0 "downloads/<标题>/<标题>.mp4"
# 期望看到：video,h264 与 audio,aac 两行

# 确认不是静音（mean_volume 不为 -inf）
ffmpeg -hide_banner -i "downloads/<标题>/<标题>.mp4" -af volumedetect -f null /dev/null 2>&1 | grep mean_volume
```

#### 视频"不支持下载"时会发生什么

这类视频**不是程序 bug，也不是配置问题**，抖音本身就不提供可下载的视频流：

| 情况 | 程序行为 |
|---|---|
| 已删除 / 不存在 | 明确提示"未返回该作品的数据……这类视频本身不支持下载" |
| 私密 / 仅好友可见 | 同上，并优先给出对应原因 |
| 地区限制（如海外版权限制） | 同上 |
| 图文 / 图集笔记 | ✅ 已支持：本地合成为视频（图片轮播 + 配乐），见上一节 |
| 直播 / 回放 | 不适用（走 live 接口） |

实测：不存在的 id 会在约 2.6 秒内返回上述明确提示，**不会**再出现
"Fresh cookies are needed"这种误导性报错，也不会冒泡成"服务内部错误"。

#### 实现位置与自测

- 代码：`core/douyin.py`（主路径 + 兜底路径，含原理与边界注释）
- 接入点：`core/ytdlp.py::probe()`（解析）、`core/tasks.py::_resolve_douyin_direct()`（下载）
- 自测：`tests/run_tests.py::test_douyin`（URL 识别、档位选择、不可用原因分类、
  网络异常收敛与重试、文件名规范化）

---

## 报错对照表

程序会把引擎内部报错匹配成可操作的中文提示（保留原始报错便于排障）。

| 引擎原始报错 | 含义 | 处理 |
|---|---|---|
| `The page needs to be reloaded.` | YouTube JS 挑战未解开 | 缺 JS 运行时 → `brew install node` 后重启 |
| `No supported JavaScript runtime could be found` | 同上（警告） | 同上；此时画质会残缺 |
| `Fresh cookies … are needed` | 站点要求登录/签名 | 配 Cookies；若是抖音则是引擎限制 |
| `Unsupported URL` | 链接形态不支持 | 抖音用 `/video/<id>`；图文帖本身无视频 |
| `Sign in to confirm your age` | 年龄限制 | 配置已登录 Cookies |
| `Login required` / `account credentials` | 需要登录 | 配置 Cookies |
| `geo-restricted` / `not available in your country` | 地区限制 | 配置对应地区代理 |
| `members-only` | 会员专属 | 配置有会员权限的 Cookies |
| `No video formats found` | 无可用画质 | 直播未开始 / 链接失效 / 需 Cookies |
| `Unable to download … SSL: UNEXPECTED_EOF_WHILE_READING` | 连接被中断 | 网络/代理抖动；程序会自动重试，也可降并发+限速 |
| `Unable to connect to proxy` / `Connection refused` | 代理不可用 | 检查代理软件与地址端口 |
| `certificate verify failed` | 证书校验失败 | 代理中间人证书问题；更换代理 |
| `HTTP Error 403` | 被拒绝 | 通常需要 Cookies 或换代理节点 |
| `HTTP Error 404` / `Not Found` | 链接失效 | 检查链接 |

---

## 常见问题（FAQ）

### Q1：`./run.sh` 报"运行时缺失"

```bash
./setup.sh        # 重新准备运行时（幂等，已就绪时会跳过下载）
```

### Q2：解析成功了，但下载总是失败

按顺序排查：

1. **代理/网络**：`curl -o /dev/null -w "%{http_code}\n" -x http://127.0.0.1:7897 <目标站点>`
2. **降并发**：高级参数把「并发分片」设为 `1`
3. **限速**：填 `1M`，避免触发 CDN 限流
4. **换编码**：程序会自动做；也可自定义格式表达式指定 `vcodec^=avc1`
5. **看日志**：任务卡片「查看日志」，搜索 `ERROR` 与 `Got error`

### Q3：视频下载后没有声音 / 只有一条流

需要 ffmpeg 合流（高清内容音视频是分离的）：

```bash
ffmpeg -version || brew install ffmpeg
```

### Q4：YouTube 画质数量很少

两种可能：

- **没装 node**：见 Q5
- **带了登录 Cookies**：登录态走的播放器策略可能返回更少格式。
  试试清空「从浏览器读取 Cookies」后再解析

### Q5：YouTube 报 `The page needs to be reloaded.`

```bash
brew install node          # 安装 JS 运行时
./run.sh --py tools/verify_env.py   # 确认 "JS 运行时 node ... —— YouTube 必需"
```

程序会自动探测 node 并注入 `--js-runtimes`，**不需要手动加参数**。
注意：新版 yt-dlp 默认只启用 deno，若机器上只有 node 而程序没注入，就会报这个错
（本项目已修复）。

### Q6：字幕勾了但没内嵌

- B 站"弹幕"是私有 XML，ffmpeg 无法内嵌 → 程序会自动降级为「只下视频」并告知
- 普通字幕（SRT/ASS/VTT）正常可内嵌
- 想让字幕只下载不内嵌：接口传 `embed_subs: false`（界面暂未暴露此开关）

### Q7：任务显示「部分完成」

播放列表/多P 场景下有条目失败。`done` + `incomplete: true`。
处理：查看日志找出失败条目 → 点「重试」（会按相同参数重新排队）。
若只是网络抖动，重试通常能补齐；已下载的条目会因「文件已存在」而被跳过。

### Q8：取消了任务，还能续传吗

能。`.part` 文件会保留，重新下载同一视频时 yt-dlp 会从断点继续
（日志出现 `Resuming download at byte …`）。

### Q9：端口被占用

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
./run.sh --port 8899
```

### Q10：前端改了代码但页面没变

前端产物是**预构建入库**的，改完源码必须重新构建：

```bash
cd frontend && npm run build     # 产物输出到 ../static
```

或者用开发模式（热更新）：`cd frontend && npm run dev`（API 自动代理到 8765）。

### Q11：`--cookies-from-browser safari` 失败

macOS 对 Safari 的 Cookie 存储有额外保护，yt-dlp 常读取失败。
改用 Chrome/Edge/Firefox，或用扩展导出 `cookies.txt`。

### Q12：想局域网内其它设备访问

```bash
./run.sh --host 0.0.0.0
```

⚠️ 无鉴权，同一局域网内任何人都能提交下载任务、浏览你的下载目录。自行承担风险。

---

## 诊断工具与手法

### 1. 环境自查

```bash
./run.sh --py tools/verify_env.py [--url <链接>] [--proxy host:port] [--browser chrome]
```

检查项：yt-dlp / ffmpeg / JS 运行时 / 目录与磁盘 / 前端产物 / 代理连通性 / 真实解析。

### 2. 直接调用引擎（复现问题最快的方式）

```bash
# 查看可用画质
./run.sh -m yt_dlp --proxy http://127.0.0.1:7897 --cookies-from-browser chrome \
  --list-formats "<链接>"

# 详细日志（定位失败环节）
./run.sh -m yt_dlp --proxy http://127.0.0.1:7897 -v "<链接>" 2>&1 | grep -iE "error|challenge|jsc|warning"

# 模拟下载不落盘
./run.sh -m yt_dlp -f "bv*[height<=720]+ba/b" --simulate "<链接>"
```

> 手工调用时若涉及 YouTube，记得带上 `--js-runtimes node`（应用内是自动注入的）。

### 3. 查看服务端日志

```bash
./run.sh                     # 前台运行，直接看输出
VH_VERBOSE=1 ./run.sh        # 额外打印 HTTP 访问日志
```

### 4. 查看任务完整日志

界面任务卡片 →「查看日志」，或：

```bash
curl -s http://127.0.0.1:8765/api/tasks/<任务id> | python3 -m json.tool | grep -A2 log
```

### 5. 检查代理真实状态

```bash
nc -z -v 127.0.0.1 7897                                    # 端口
curl -o /dev/null -w "%{http_code}\n" -x http://127.0.0.1:7897 https://www.youtube.com/
lsof -nP -iTCP:7897 -sTCP:LISTEN                           # 是哪个客户端在监听
```

**典型症状**：端口开放、客户端在监听，但所有请求 0.3 秒即失败
（`curl` 退出码 35）。这是代理**上游节点**的问题，与本项目无关。

### 6. git 推送/克隆 github.com 失败

**症状**：`git push` 报
`Failed to connect to ghproxy.com port 443 after 75000 ms`。

**原因**：本机全局 git 配置把 github.com 请求改写成第三方镜像：

```bash
git config --global --get-regexp 'url\..*insteadof'
# url.https://ghproxy.com/https://github.com/.insteadof https://github.com/
```

当该镜像失效时，所有 GitHub 操作都会被改写到一个连不上的地址。

**临时绕过（不改配置）**：

```bash
GIT_CONFIG_GLOBAL=/dev/null git \
  -c http.proxy=http://127.0.0.1:7897 \
  -c https.proxy=http://127.0.0.1:7897 push
```

**永久修复**（自行决定）：

```bash
git config --global --unset url.https://ghproxy.com/https://github.com/.insteadof
# 若需要走代理访问 GitHub，再配置（代理不稳定时也可不配，直连实测可用）：
git config --global http.proxy http://127.0.0.1:7897
```

> 注意：`git -c url.<base>.insteadof=` 无法清空已有的多值配置，
> 因此必须用 `--unset` 删除，或用 `GIT_CONFIG_GLOBAL=/dev/null` 一次性屏蔽。

### 7. 运行自测

```bash
./run.sh --py tests/run_tests.py          # 离线，208 项（27 个分组）
./run.sh --py tests/run_tests.py --live   # 额外做真实解析与下载
```

如果离线自测失败，说明是代码/环境问题；如果只有 `--live` 失败，
通常是网络或站点侧问题。

---

## 定位问题的通用思路

本项目开发过程中沉淀出的排障方法，遇到新问题时同样适用：

1. **先分离变量**：把「代理」「Cookies」「JS 运行时」逐个开关做对照实验。
   例：`仅代理` 成功、`代理+Cookies` 失败 → 问题在 Cookies，而不在网络。
2. **走命令行复现**：用 `./run.sh -m yt_dlp …` 直接调用引擎，绕开应用层，
   确认是引擎问题还是应用问题。
3. **看 verbose 日志**：`-v` 能看到 `JS Challenge Providers`、播放器请求、
   extractor 选择等关键内部状态。
4. **读提取器源码**：`runtime/.../yt_dlp/extractor/<site>.py`，
   看 `_VALID_URL` 正则与 `_real_extract` 里的 TODO —— 能直接判断是"引擎不支持"
   还是"配置不对"。
5. **确认是否已有产物**：有些"失败"其实文件已下好（程序会识别为跳过）。
