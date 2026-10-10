// 数值/时间格式化工具（全部按中文习惯输出）

export function formatBytes(bytes, digits = 2) {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '—'
  const value = Number(bytes)
  if (value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let idx = 0
  let num = value
  while (num >= 1024 && idx < units.length - 1) {
    num /= 1024
    idx += 1
  }
  return `${num.toFixed(idx === 0 ? 0 : digits)} ${units[idx]}`
}

export function formatSpeed(bytesPerSecond) {
  if (!bytesPerSecond || bytesPerSecond <= 0) return '—'
  return `${formatBytes(bytesPerSecond, 1)}/s`
}

export function formatEta(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) return '—'
  const total = Math.round(seconds)
  if (total < 60) return `${total}s`
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m < 60) return `${m}m${String(s).padStart(2, '0')}s`
  const h = Math.floor(m / 60)
  return `${h}h${String(m % 60).padStart(2, '0')}m`
}

export function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return '—'
  const total = Math.round(Number(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

export function formatCount(num) {
  if (!num && num !== 0) return '—'
  if (num >= 100000000) return `${(num / 100000000).toFixed(1)} 亿`
  if (num >= 10000) return `${(num / 10000).toFixed(1)} 万`
  return String(num)
}

export function formatDate(raw) {
  if (!raw) return '—'
  const text = String(raw)
  if (/^\d{8}$/.test(text)) {
    return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  }
  return text
}

export function percentText(percent) {
  if (percent === null || percent === undefined) return '—'
  const value = Math.max(0, Math.min(100, Number(percent)))
  return `${value.toFixed(1)}%`
}

/** ASCII 块状进度条：独特点之一，比通用圆角进度条更有"神器"感 */
export function asciiBar(percent, width = 24) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0))
  const filled = Math.round((value / 100) * width)
  return `${'█'.repeat(filled)}${'░'.repeat(Math.max(0, width - filled))}`
}

/** 解析多行/逗号分隔的链接 */
export function parseUrls(text) {
  return String(text || '')
    .split(/[\s,;]+/)
    .map((item) => item.trim())
    .filter((item) => /^https?:\/\//i.test(item))
}

export function shortPath(path, max = 52) {
  if (!path) return '—'
  if (path.length <= max) return path
  const parts = path.split('/')
  const name = parts.pop() || ''
  return `…/${name.length > max - 6 ? `${name.slice(0, max - 8)}…` : name}`
}

export function timeAgo(timestamp) {
  if (!timestamp) return '—'
  const diff = Date.now() / 1000 - timestamp
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}
