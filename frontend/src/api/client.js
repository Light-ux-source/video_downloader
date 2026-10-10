// 后端 API 客户端：统一错误处理

async function request(path, { method = 'GET', body, timeout = 180000 } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
    const text = await response.text()
    let data = {}
    if (text) {
      try {
        data = JSON.parse(text)
      } catch {
        throw new Error(`响应不是合法 JSON（HTTP ${response.status}）`)
      }
    }
    if (!response.ok) {
      throw new Error(data.error || `请求失败（HTTP ${response.status}）`)
    }
    return data
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('请求超时：解析或下载耗时过长，请稍后重试')
    }
    throw error
  } finally {
    clearTimeout(timer)
  }
}

export const api = {
  health: () => request('/api/health', { timeout: 30000 }),
  presets: () => request('/api/presets', { timeout: 15000 }),
  probe: (payload) => request('/api/probe', { method: 'POST', body: payload, timeout: 120000 }),
  createTasks: (payload) => request('/api/tasks', { method: 'POST', body: payload, timeout: 30000 }),
  listTasks: (limit = 200) => request(`/api/tasks?limit=${limit}`, { timeout: 20000 }),
  taskDetail: (id) => request(`/api/tasks/${id}`, { timeout: 20000 }),
  cancel: (id) => request(`/api/tasks/${id}/cancel`, { method: 'POST', timeout: 20000 }),
  retry: (id) => request(`/api/tasks/${id}/retry`, { method: 'POST', timeout: 20000 }),
  remove: (id) => request(`/api/tasks/${id}`, { method: 'DELETE', timeout: 20000 }),
  reveal: (path) => request('/api/reveal', { method: 'POST', body: { path }, timeout: 15000 }),
  revealDir: (path) => request('/api/reveal-dir', { method: 'POST', body: { path }, timeout: 15000 }),
  eventsUrl: (id) => `/api/tasks/${id}/events`,
}
