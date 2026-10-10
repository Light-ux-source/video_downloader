# 排障指南

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
| 抖音 | ❌ 当前不可用 | 见下节 |

### 抖音不可用原因

**现象**：报 `Fresh cookies are needed`，即使已经配置了完整 cookies。

**原因**（两条，都在 yt-dlp 侧）：

1. **`/note/` 链接形态不被支持**。yt-dlp 的抖音提取器只写了 `/video/` 的正则：
   ```python
   _VALID_URL = r'https?://(?:www\.)?douyin\.com/video/(?P<id>[0-9]+)'
   ```
   `/note/` 会落到通用提取器，报 `Unsupported URL`。
   程序已自动把 `/note/<id>` 改写为 `/video/<id>` 再试。

2. **`/video/` 形态受签名限制**。yt-dlp 源码中明确留着未实现的 TODO：
   ```python
   detail = traverse_obj(self._download_json('…/aweme/detail/', …), ('aweme_detail', {dict}))
   if not detail:
       # TODO: Run verification challenge code to generate signature cookies
       raise ExtractorError('Fresh cookies (not necessarily logged in) are needed', …)
   ```
   抖音要求 `a_bogus` / `msToken` 等请求签名，引擎尚未实现，因此接口返回空。

**补充**：`v.douyin.com` 短链引擎同样不支持（源码中未出现该正则）。

**结论**：这是 yt-dlp 上游缺口，不是本项目配置问题，也不是 cookies 能解决的。
程序会给出明确提示而不是含糊报错。要根治需要换用别的签名实现（工作量属另一个量级）。

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
./run.sh --py tests/run_tests.py          # 离线，126 项
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
