// 应用状态（轻量自研 store，避免额外依赖）
import { computed, reactive } from 'vue'
import { api } from '@/api/client'
import { parseUrls } from '@/utils/format'

const SETTINGS_KEY = 'videoharbor.settings.v1'

const defaultSettings = {
  preset: 'best',
  audioFormat: 'mp3',
  subtitles: false,
  subtitleLangs: 'zh-Hans,zh-CN,en',
  autoSubs: false,
  playlist: false,
  playlistItems: '',
  cookiesFile: '',
  cookiesFromBrowser: '',
  proxy: '',
  limitRate: '',
  concurrentFragments: 4,
  skipExisting: false,
  sponsorblock: false,
  customFormat: '',
  useCustomFormat: false,
  scanlines: true,
  autoOpenFolder: false,
}

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (!raw) return { ...defaultSettings }
    const parsed = JSON.parse(raw)
    return { ...defaultSettings, ...parsed }
  } catch {
    return { ...defaultSettings }
  }
}

export const state = reactive({
  ready: false,
  engine: null,
  health: null,
  presets: [],
  audioFormats: [],
  tasks: {},
  order: [],
  stats: null,
  inspection: null,
  inspecting: false,
  inspectError: '',
  submitting: false,
  toast: null,
  settings: loadSettings(),
  streams: {},
  serverOnline: false,
})

let toastTimer = null

export function notify(message, kind = 'info') {
  state.toast = { message, kind, at: Date.now() }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    state.toast = null
  }, kind === 'error' ? 8000 : 4200)
}

export function persistSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings))
  } catch {
    /* 忽略隐私模式下的写入失败 */
  }
}

export const taskList = computed(() =>
  state.order.map((id) => state.tasks[id]).filter(Boolean),
)

export const activeCount = computed(
  () => taskList.value.filter((t) => t.state === 'running' || t.state === 'queued').length,
)

// ---------------------------------------------------------------- 初始化
export async function initialize() {
  await Promise.all([refreshHealth(), refreshPresets()])
  await refreshTasks()
  state.ready = true
  // 兜底轮询：SSE 断开时也能保持状态同步
  setInterval(() => {
    refreshTasks({ silent: true })
    refreshHealth({ silent: true })
  }, 20000)
}

export async function refreshHealth({ silent = false } = {}) {
  try {
    const data = await api.health()
    state.health = data
    state.engine = data.engine
    state.stats = data.tasks
    state.serverOnline = true
  } catch (error) {
    state.serverOnline = false
    if (!silent) notify(`后端未就绪：${error.message}`, 'error')
  }
}

export async function refreshPresets() {
  try {
    const data = await api.presets()
    state.presets = data.presets || []
    state.audioFormats = data.audio_formats || []
  } catch {
    /* 预设获取失败不阻塞主流程 */
  }
}

export async function refreshTasks({ silent = false } = {}) {
  try {
    const data = await api.listTasks()
    const incoming = data.tasks || []
    state.stats = data.stats
    const seen = new Set()
    for (const task of incoming) {
      seen.add(task.id)
      const existing = state.tasks[task.id]
      // 已有进度更新时不要回退到旧快照
      if (!existing || (task.updated_at || 0) >= (existing.updated_at || 0)) {
        state.tasks[task.id] = task
      }
      if (!state.order.includes(task.id)) state.order.push(task.id)
    }
    state.order = state.order.filter((id) => seen.has(id))
    for (const id of Object.keys(state.tasks)) {
      if (!seen.has(id)) delete state.tasks[id]
    }
    state.serverOnline = true
  } catch (error) {
    state.serverOnline = false
    if (!silent) notify(`获取任务列表失败：${error.message}`, 'error')
  }
}

// ---------------------------------------------------------------- 解析
export async function inspect(url) {
  const target = String(url || '').trim()
  if (!/^https?:\/\//i.test(target)) {
    state.inspectError = '请输入以 http:// 或 https:// 开头的视频链接'
    return null
  }
  state.inspecting = true
  state.inspectError = ''
  state.inspection = null
  try {
    const data = await api.probe({
      url: target,
      playlist: state.settings.playlist,
      cookies_file: state.settings.cookiesFile || undefined,
      cookies_from_browser: state.settings.cookiesFromBrowser || undefined,
      proxy: state.settings.proxy || undefined,
    })
    if (!data.ok) {
      state.inspectError = data.error || '解析失败'
      return null
    }
    state.inspection = { ...data.info, sourceUrl: target }
    if (state.settings.playlist && data.info.kind === 'playlist') {
      notify(`播放列表解析完成，共 ${data.info.entry_count || 0} 个条目`, 'info')
    }
    return state.inspection
  } catch (error) {
    state.inspectError = error.message
    return null
  } finally {
    state.inspecting = false
  }
}

export function clearInspection() {
  state.inspection = null
  state.inspectError = ''
}

// ---------------------------------------------------------------- 提交下载
function collectDownloadOptions(settings, overrides = {}) {
  const preset = state.presets.find((p) => p.id === settings.preset)
  const audioOnly = Boolean(preset?.audio_only) || settings.preset === 'audio'
  const options = {
    preset: settings.preset,
    audio_format: settings.audioFormat,
    audio_only: audioOnly,
    subtitles: settings.subtitles && !audioOnly,
    subtitle_langs: settings.subtitleLangs,
    auto_subs: settings.autoSubs,
    embed_subs: true,
    playlist: settings.playlist,
    playlist_items: settings.playlistItems,
    cookies_file: settings.cookiesFile || undefined,
    cookies_from_browser: settings.cookiesFromBrowser || undefined,
    proxy: settings.proxy || undefined,
    limit_rate: settings.limitRate || undefined,
    concurrent_fragments: Number(settings.concurrentFragments) || 1,
    skip_existing: settings.skipExisting,
    sponsorblock: settings.sponsorblock,
  }
  if (settings.useCustomFormat && settings.customFormat.trim()) {
    options.custom_format = settings.customFormat.trim()
  }
  return { ...options, ...overrides }
}

export async function submitUrls(urls, overrides = {}) {
  const list = Array.isArray(urls) ? urls.filter(Boolean) : parseUrls(urls)
  if (!list.length) {
    notify('没有检测到有效链接', 'error')
    return []
  }
  state.submitting = true
  try {
    const data = await api.createTasks({
      urls: list,
      ...collectDownloadOptions(state.settings, overrides),
    })
    const created = data.tasks || []
    for (const task of created) {
      state.tasks[task.id] = task
      if (!state.order.includes(task.id)) state.order.unshift(task.id)
      subscribe(task.id)
    }
    state.inspection = null
    notify(`已加入队列：${created.length} 个任务`, 'success')
    return created
  } catch (error) {
    notify(`创建任务失败：${error.message}`, 'error')
    return []
  } finally {
    state.submitting = false
  }
}

export async function submitInspection(overrides = {}) {
  if (!state.inspection) return []
  const urls = [state.inspection.sourceUrl || state.inspection.url]
  return submitUrls(urls, overrides)
}

// ---------------------------------------------------------------- 任务操作
export async function cancelTask(id) {
  try {
    await api.cancel(id)
    notify('已请求取消', 'info')
  } catch (error) {
    notify(`取消失败：${error.message}`, 'error')
  }
}

export async function retryTask(id) {
  try {
    const data = await api.retry(id)
    if (data.task) {
      const task = data.task
      state.tasks[task.id] = task
      state.order.unshift(task.id)
      subscribe(task.id)
      notify('已重新排队', 'success')
    }
  } catch (error) {
    notify(`重试失败：${error.message}`, 'error')
  }
}

export async function removeTask(id) {
  try {
    await api.remove(id)
    closeStream(id)
    delete state.tasks[id]
    state.order = state.order.filter((item) => item !== id)
  } catch (error) {
    notify(`删除失败：${error.message}`, 'error')
  }
}

export async function clearFinished() {
  const finished = taskList.value.filter((t) => ['done', 'error', 'canceled'].includes(t.state))
  if (!finished.length) return
  await Promise.all(finished.map((t) => api.remove(t.id).catch(() => null)))
  for (const task of finished) {
    closeStream(task.id)
    delete state.tasks[task.id]
  }
  state.order = state.order.filter((id) => state.tasks[id])
  notify(`已清理 ${finished.length} 条记录`, 'info')
}

export async function revealTask(task) {
  const target = task.filepath
  if (!target) return
  try {
    await api.reveal(target)
  } catch (error) {
    notify(`打开失败：${error.message}`, 'error')
  }
}

export async function revealDownloadDir() {
  try {
    const data = await api.revealDir(state.health?.download_dir)
    notify(`已打开下载目录：${data.path}`, 'info')
  } catch (error) {
    notify(`打开失败：${error.message}`, 'error')
  }
}

// ---------------------------------------------------------------- SSE
export function subscribe(taskId) {
  if (state.streams[taskId]) return
  const task = state.tasks[taskId]
  if (task && ['done', 'error', 'canceled'].includes(task.state)) return

  const source = new EventSource(api.eventsUrl(taskId))
  state.streams[taskId] = { source, retries: 0 }

  source.onmessage = (event) => {
    let payload
    try {
      payload = JSON.parse(event.data)
    } catch {
      return
    }
    if (payload.type === 'removed') {
      closeStream(taskId)
      return
    }
    if (payload.task) {
      const previous = state.tasks[payload.task.id]
      if (!previous || (payload.task.updated_at || 0) >= (previous.updated_at || 0)) {
        state.tasks[payload.task.id] = payload.task
      }
      if (!state.order.includes(payload.task.id)) state.order.unshift(payload.task.id)
      if (payload.task.state === 'done' && (!previous || previous.state !== 'done')) {
        notify(`完成：${payload.task.title || '任务'}`, 'success')
        if (state.settings.autoOpenFolder) revealDownloadDir()
      }
      if (payload.task.state === 'error' && (!previous || previous.state !== 'error')) {
        notify(`失败：${payload.task.error || '未知错误'}`, 'error')
      }
    }
    if (payload.type === 'end') {
      closeStream(taskId)
    }
  }

  source.onerror = () => {
    const entry = state.streams[taskId]
    closeStream(taskId)
    const current = state.tasks[taskId]
    if (!entry || !current || ['done', 'error', 'canceled'].includes(current.state)) return
    if (entry.retries >= 3) return
    // 断线重连（SSE 结束后若任务仍在跑，说明连接被打断）
    const delay = 800 * (entry.retries + 1)
    setTimeout(() => {
      state.streams[taskId] = { source: null, retries: entry.retries + 1 }
      delete state.streams[taskId]
      subscribe(taskId)
    }, delay)
  }
}

export function closeStream(taskId) {
  const entry = state.streams[taskId]
  if (entry?.source) {
    try {
      entry.source.close()
    } catch {
      /* 忽略 */
    }
  }
  delete state.streams[taskId]
}

export function subscribeActive() {
  for (const task of taskList.value) {
    if (task.state === 'running' || task.state === 'queued') subscribe(task.id)
  }
}
