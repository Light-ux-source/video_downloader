# API 参考

所有接口均为 `JSON`（SSE 除外），基地址 `http://127.0.0.1:8765`。
成功响应统一带 `"ok": true`；业务失败返回 HTTP 200 + `{"ok": false, "error": "..."}`
（便于前端统一展示）；参数错误返回 HTTP 400，资源不存在返回 404。

---

## GET `/api/health`

服务与依赖自检。

```bash
curl -s http://127.0.0.1:8765/api/health
```

```json
{
  "ok": true,
  "engine": {
    "engine": "yt-dlp",
    "version": "2026.08.19",
    "ffmpeg": "8.1.2",
    "python": null,
    "js_runtime": "/opt/homebrew/bin/node",
    "ok": true,
    "error": null
  },
  "ffmpeg_ok": true,
  "js_runtime": "/opt/homebrew/bin/node",
  "concurrency": 2,
  "tasks": { "total": 4, "running": 0, "queued": 0, "done": 4, "error": 0, "canceled": 0, "concurrency": 2 },
  "disk_free": 315000000000,
  "disk_free_text": "294.1GB",
  "download_dir": "/path/to/video_downloader/downloads",
  "uptime": 128.5
}
```

> `js_runtime` 为空表示未找到 node/deno/bun/qjs。此时 YouTube 会报
> `The page needs to be reloaded.`，需 `brew install node`。

---

## GET `/api/presets`

返回画质预设与可选音频格式，前端据此渲染按钮。

```json
{
  "ok": true,
  "presets": [
    { "id": "best", "label": "最佳画质", "hint": "自动选择最高画质与音轨", "format": "bv*+ba/b" },
    { "id": "1080", "label": "1080P", "hint": "不超过 1920×1080", "format": "bv*[height<=1080]+ba/b[height<=1080]/b" },
    { "id": "audio", "label": "仅音频", "hint": "提取音轨（默认 MP3）", "format": null, "audio_only": true }
  ],
  "audio_formats": ["mp3", "m4a", "opus", "flac", "wav"]
}
```

---

## POST `/api/probe`

解析视频或播放列表元信息。**不会下载**。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url` | string | ✅ | 必须 `http(s)://` 开头 |
| `playlist` | bool | | `true` 时按播放列表解析（`--flat-playlist`） |
| `cookies_file` | string | | cookies.txt 路径 |
| `cookies_from_browser` | string | | `chrome` / `edge` / `firefox` / `brave` / `safari` |
| `proxy` | string | | 支持 `127.0.0.1:7897` 或 `socks5://...`，缺协议时自动补 `http://` |

```bash
curl -s -X POST http://127.0.0.1:8765/api/probe \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=qmhPMkPZOKw","proxy":"127.0.0.1:7897","cookies_from_browser":"chrome"}'
```

**单视频响应**（`info.kind = "video"`）：

```json
{
  "ok": true,
  "info": {
    "kind": "video",
    "url": "https://www.youtube.com/watch?v=qmhPMkPZOKw",
    "title": "……",
    "uploader": "……",
    "extractor": "Youtube",
    "duration": 44,
    "duration_text": "0:44",
    "thumbnail": "https://…",
    "view_count": 12345,
    "upload_date": "20260101",
    "is_live": false,
    "has_subtitles": false,
    "subtitle_langs": [],
    "formats": [
      {
        "format_id": "137",
        "label": "1080p · mp4",
        "ext": "mp4",
        "height": 1080, "width": 1920, "fps": 30,
        "vcodec": "avc1.640028", "acodec": null,
        "abr": null, "tbr": 3045,
        "filesize": 16800000, "filesize_text": "16.0MB",
        "has_video": true, "has_audio": false, "is_audio_only": false,
        "note": "1080p", "protocol": "https"
      }
    ],
    "presets": [ "……同 /api/presets" ]
  }
}
```

**播放列表响应**（`info.kind = "playlist"`）：

```json
{
  "ok": true,
  "info": {
    "kind": "playlist",
    "title": "合集标题",
    "uploader": "……",
    "entry_count": 2,
    "entries": [
      { "id": "…", "title": "…", "url": "…", "duration": 98, "uploader": "…", "thumbnail": "…" }
    ],
    "formats": [],
    "presets": []
  }
}
```

**解析失败**：HTTP 200 + `{"ok": false, "error": "原始报错\n提示：可操作的中文建议"}`。
若链接形态被自动纠正（例如抖音 `/note/` → `/video/`），会额外返回
`info.note`（说明）与 `info.original_url`（原始链接）。

---

## POST `/api/tasks`

批量创建下载任务。一次最多 **50** 个链接（去重后）。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `url` | string | | 单个链接 |
| `urls` | string[] \| string | | 多个链接；字符串会按空白/逗号/分号拆分 |
| `preset` | string | `best` | `best`/`1080`/`720`/`480`/`360`/`audio` |
| `custom_format` | string | | 自定义 `-f` 表达式，优先级高于 `preset` |
| `audio_only` | bool | | 强制仅音频 |
| `audio_format` | string | `mp3` | `mp3`/`m4a`/`opus`/`flac`/`wav` |
| `subtitles` | bool | `false` | 下载并内嵌字幕（仅视频） |
| `subtitle_langs` | string | `zh-Hans,zh-CN,en` | 逗号分隔 |
| `auto_subs` | bool | `false` | 包含自动生成字幕 |
| `embed_subs` | bool | `true` | 内嵌（false 则仅下载字幕文件） |
| `playlist` | bool | `false` | 下载整个播放列表 |
| `playlist_items` | string | | 如 `1-5,8` |
| `cookies_file` / `cookies_from_browser` / `proxy` | string | | 同 probe |
| `limit_rate` | string | | 如 `2M`、`500K` |
| `concurrent_fragments` | int | `4` | 1–16 |
| `chunk_size` | string | | 如 `1M`，分片下载以抗 CDN 断连 |
| `skip_existing` | bool | `false` | `--no-overwrites` |
| `sponsorblock` | bool | `false` | 移除赞助片段 |

```bash
curl -s -X POST http://127.0.0.1:8765/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://…","https://…"],"preset":"1080","proxy":"127.0.0.1:7897"}'
```

响应 `HTTP 201`：

```json
{ "ok": true, "tasks": [ { "id": "9c8de853eddf", "state": "queued", "phase": "queued", "…": "…" } ] }
```

---

## GET `/api/tasks`

任务列表（按创建时间倒序）。查询参数 `limit`（1–500，默认 200）。

```json
{ "ok": true, "tasks": [ /* 任务对象 */ ], "stats": { "total": 4, "running": 0, "done": 4, "…": 0 } }
```

### 任务对象字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 12 位十六进制 |
| `url` | string | 目标链接（已被纠正后的） |
| `state` | string | `queued` / `running` / `done` / `error` / `canceled` |
| `phase` | string | `queued`/`preparing`/`downloading`/`merging`/`embedding`/`converting`/`finalizing`/`done`/`failed` |
| `phase_text` | string | 阶段中文名 |
| `title` / `thumbnail` / `uploader` / `duration` | | 元信息（来自前端解析或产物文件名） |
| `quality_label` | string | 如 `1080P`、`仅音频 · MP3`、`480P · 内嵌字幕` |
| `percent` | float \| null | 当前流进度（多流时按当前流计） |
| `downloaded_bytes` / `total_bytes` | int | 字节数 |
| `speed` | float \| null | 字节/秒 |
| `eta` | int \| null | 剩余秒数 |
| `message` | string \| null | 人类可读的当前状态 |
| `error` | string \| null | 失败原因（含中文提示） |
| `filepath` | string \| null | 首个产物绝对路径 |
| `filesize` | int \| null | 首个产物体积 |
| `files` | string[] | 全部产物文件名 |
| `file_count` | int | 产物数量 |
| `warnings` | int | 传输异常/重试次数 |
| `last_warning` | string \| null | 最近一次告警原文 |
| `playlist_total` / `item_index` | int \| null / int | 播放列表预期条目数 / 已完成条目数 |
| `incomplete` | bool | **是否部分完成**（`done` 但有条目失败） |
| `failed_items` | int | 判定失败的条目数 |
| `created_at` / `started_at` / `finished_at` / `updated_at` | float | Unix 时间戳（秒） |
| `elapsed` | float | 已耗时/总耗时（秒） |

---

## GET `/api/tasks/{id}`

单任务详情，比列表多一个 `log` 字段（yt-dlp 原始输出，最多 400 行）。

```json
{ "ok": true, "task": { "…": "…", "log": ["$ -m yt_dlp …", "[download] Destination: …"] } }
```

---

## GET `/api/tasks/{id}/events` — SSE 进度流

`Content-Type: text/event-stream`。每帧格式：

```
data: {"type":"state","task":{…}}

data: {"type":"update","task":{…}}

: ping                       ← 空闲 10s 心跳

data: {"type":"end"}
```

| `type` | 时机 |
|---|---|
| `state` | 连接建立时推送一次当前快照 |
| `update` | 阶段/进度变化（进度最多每 300ms 一次） |
| `removed` | 任务记录被删除 |
| `end` | 任务进入终态，服务端随后关闭连接 |

任务不存在时返回 HTTP 404（非 SSE）。

```bash
curl -N http://127.0.0.1:8765/api/tasks/9c8de853eddf/events
```

---

## POST `/api/tasks/{id}/cancel`

取消任务：排队中直接标记取消；运行中向进程组发 `SIGTERM`，5 秒后 `SIGKILL`。
未完成的 `.part` 文件保留，便于重试续传。

```json
{ "ok": true, "task": { "state": "canceled", "…": "…" } }
```

任务已是终态时 `ok: false`；任务不存在返回 404。

---

## POST `/api/tasks/{id}/retry`

以相同参数复制一个新任务（新 id）并排队。任务仍在进行中时返回 400。

```json
{ "ok": true, "task": { "id": "新任务id", "state": "queued" } }
```

---

## DELETE `/api/tasks/{id}`

删除任务记录（仅允许终态任务）。不会删除已下载的文件。

```json
{ "ok": true }
```

---

## POST `/api/reveal`

在访达（Finder）中定位文件。**路径必须位于下载目录内**，否则 400。

```bash
curl -s -X POST http://127.0.0.1:8765/api/reveal \
  -H 'Content-Type: application/json' -d '{"path":"/…/downloads/xxx.mp4"}'
```

## POST `/api/reveal-dir`

打开下载目录（或指定子目录）。同样受下载目录限制。

```json
{ "ok": true, "path": "/…/downloads" }
```

---

## 错误码约定

| HTTP | 场景 |
|---|---|
| 200 | 正常；业务失败也在 200 里用 `ok:false` 表达（如解析失败） |
| 201 | 批量创建任务成功 |
| 400 | 参数非法（URL 格式、超量、越权路径、请求体过大/非 JSON） |
| 404 | 任务不存在 / 接口不存在 |
| 500 | 服务内部异常（已兜底，不会让进程崩溃） |

## 请求体限制

- 最大 2 MB（`Content-Length` 超出即 400）
- 一次最多 50 个链接
- 路径操作仅限 `downloads/` 目录内（防目录穿越）

## 静态资源

| 路径 | 行为 |
|---|---|
| `GET /` | 返回 `static/index.html`（未构建时返回提示页） |
| `GET /assets/*` | 带 hash 的构建产物，`Cache-Control: public, max-age=31536000, immutable` |
| `GET /其它` | SPA 兜底返回 `index.html`；路径穿越返回 403 |
