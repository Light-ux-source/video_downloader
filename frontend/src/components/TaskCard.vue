<script setup>
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import ProgressBar from './ProgressBar.vue'
import { api } from '@/api/client'
import { cancelTask, notify, removeTask, retryTask, revealTask, state } from '@/store'
import { formatBytes, formatEta, formatSpeed, percentText, shortPath, timeAgo } from '@/utils/format'

const props = defineProps({
  task: { type: Object, required: true },
})

const expanded = ref(false)
const detail = ref(null)
const loadingLog = ref(false)

const STATE_TEXT = {
  queued: '排队中',
  running: '下载中',
  done: '已完成',
  error: '失败',
  canceled: '已取消',
}

const stateLabel = computed(() =>
  props.task.state === 'done' && props.task.incomplete ? '部分完成' : STATE_TEXT[props.task.state] || props.task.state,
)

const stateTone = computed(() => {
  if (props.task.state === 'done' && props.task.incomplete) return 'warn'
  return {
    queued: 'warn',
    running: 'neon',
    done: 'ok',
    error: 'danger',
    canceled: 'warn',
  }[props.task.state] || 'warn'
})

const itemProgress = computed(() => {
  const total = props.task.playlist_total
  if (!total) return ''
  return `${Math.min(props.task.item_index || 0, total)}/${total}`
})

const fileCountText = computed(() => {
  const count = props.task.file_count || (props.task.filepath ? 1 : 0)
  return count > 1 ? `${count} 个文件` : ''
})

const stateClass = computed(() => `tag--${stateTone.value}`)

const active = computed(() => props.task.state === 'running' || props.task.state === 'queued')

const progressLabel = computed(() => {
  const t = props.task
  if (t.state === 'done') return formatBytes(t.filesize || t.total_bytes || t.downloaded_bytes)
  if (t.state === 'error') return t.error ? String(t.error).slice(0, 60) : '未知错误'
  const parts = [percentText(t.percent)]
  if (t.speed) parts.push(formatSpeed(t.speed))
  if (t.eta) parts.push(`剩 ${formatEta(t.eta)}`)
  return parts.join(' · ')
})

const canReveal = computed(() => props.task.state === 'done' && props.task.filepath)

async function toggleLog() {
  expanded.value = !expanded.value
  if (!expanded.value) return
  loadingLog.value = true
  try {
    const data = await api.taskDetail(props.task.id)
    detail.value = data.task
  } catch (error) {
    notify(`读取日志失败：${error.message}`, 'error')
  } finally {
    loadingLog.value = false
  }
}

function copyLog() {
  const lines = (detail.value?.log || props.task.log || []).join('\n')
  if (!lines) return
  navigator.clipboard?.writeText(lines).then(
    () => notify('日志已复制到剪贴板', 'success'),
    () => notify('复制失败', 'error'),
  )
}
</script>

<template>
  <article class="card" :class="[`card--${task.state}`, { 'card--busy': active }]">
    <div class="card__top">
      <span class="tag" :class="stateClass">
        <Icon :name="active ? 'activity' : task.state === 'done' ? 'check' : task.state === 'error' ? 'alert' : 'pause'" :size="11" />
        {{ stateLabel }}
      </span>
      <span class="card__phase">{{ task.phase_text || task.phase }}</span>
      <span v-if="task.quality_label" class="tag tag--neon">{{ task.quality_label }}</span>
      <span v-if="itemProgress" class="tag" :title="`播放列表条目进度`">
        <Icon name="list" :size="11" />
        {{ itemProgress }}
      </span>
      <span v-if="fileCountText" class="tag" :title="`本次任务产出文件数`">{{ fileCountText }}</span>
      <span v-if="task.incomplete" class="tag tag--warn" title="有条目未能完成，可查看日志后重试">
        <Icon name="alert" :size="11" />
        存在未完成条目
      </span>
      <span v-if="task.warnings" class="tag tag--warn" :title="`${task.warnings} 次传输异常已自动重试`">
        <Icon name="warn" :size="11" />
        {{ task.warnings }} 次重试
      </span>
      <span class="spacer" />
      <span class="card__time mono-num">{{ timeAgo(task.created_at) }}</span>
    </div>

    <h3 class="card__title" :title="task.title || task.url">
      {{ task.title || task.url }}
    </h3>

    <ProgressBar
      :percent="task.percent"
      :state="task.state"
      :label="progressLabel"
      :width="28"
    />

    <dl class="card__stats mono-num">
      <div>
        <dt>已下载</dt>
        <dd>{{ formatBytes(task.downloaded_bytes) }}</dd>
      </div>
      <div>
        <dt>总大小</dt>
        <dd>{{ task.total_bytes ? formatBytes(task.total_bytes) : '未知' }}</dd>
      </div>
      <div>
        <dt>速度</dt>
        <dd>{{ formatSpeed(task.speed) }}</dd>
      </div>
      <div>
        <dt>剩余</dt>
        <dd>{{ formatEta(task.eta) }}</dd>
      </div>
      <div>
        <dt>耗时</dt>
        <dd>{{ formatEta(task.elapsed) }}</dd>
      </div>
    </dl>

    <p v-if="task.filepath" class="card__path mono-num" :title="task.filepath">
      <Icon name="folder" :size="12" />
      {{ shortPath(task.filepath, 64) }}
    </p>
    <p v-else-if="task.message" class="card__message">{{ task.message }}</p>

    <div class="card__actions">
      <button v-if="active" class="btn btn--sm btn--danger" @click="cancelTask(task.id)">
        <Icon name="x" :size="12" />
        取消
      </button>
      <button
        v-if="canReveal"
        class="btn btn--sm"
        @click="revealTask(task)"
      >
        <Icon name="external" :size="12" />
        在访达中显示
      </button>
      <button
        v-if="['error', 'canceled'].includes(task.state)"
        class="btn btn--sm"
        @click="retryTask(task.id)"
      >
        <Icon name="retry" :size="12" />
        重试
      </button>
      <button class="btn btn--ghost btn--sm" :aria-expanded="expanded" @click="toggleLog">
        <Icon name="terminal" :size="12" />
        {{ expanded ? '收起日志' : '查看日志' }}
      </button>
      <span class="spacer" />
      <button
        v-if="['done', 'error', 'canceled'].includes(task.state)"
        class="btn btn--ghost btn--sm btn--icon"
        aria-label="删除该记录"
        title="删除记录"
        @click="removeTask(task.id)"
      >
        <Icon name="trash" :size="12" />
      </button>
    </div>

    <transition name="fold">
      <div v-if="expanded" class="logbox">
        <div class="logbox__head">
          <Icon name="terminal" :size="12" />
          <span>yt-dlp 原始输出</span>
          <span class="spacer" />
          <button class="btn btn--ghost btn--sm" @click="copyLog">
            <Icon name="copy" :size="11" />
            复制
          </button>
        </div>
        <pre v-if="loadingLog" class="logbox__body">读取中…</pre>
        <pre v-else class="logbox__body">{{
          (detail?.log || task.log || []).join('\n') || '（暂无输出）'
        }}</pre>
      </div>
    </transition>
  </article>
</template>

<style scoped>
.card {
  border: 1px solid var(--border);
  border-left: 2px solid var(--border-strong);
  border-radius: var(--radius);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.018), transparent);
  padding: var(--sp-3) var(--sp-4);
  display: grid;
  gap: var(--sp-2);
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}

.card:hover {
  border-color: var(--border-strong);
  background: rgba(255, 255, 255, 0.028);
}

.card--running {
  border-left-color: var(--neon);
  box-shadow: inset 0 0 40px -34px var(--neon);
}

.card--done {
  border-left-color: var(--ok);
}

.card--error {
  border-left-color: var(--danger);
}

.card--canceled {
  border-left-color: var(--warn);
}

.card__top {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.card__phase {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.08em;
}

.card__time {
  font-size: 10.5px;
  color: var(--muted);
}

.card__title {
  margin: 0;
  font-size: 13.5px;
  font-weight: 500;
  line-height: 1.5;
  color: var(--text);
  word-break: break-word;
}

.card__stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(88px, 1fr));
  gap: var(--sp-2);
  margin: 0;
}

.card__stats dt {
  font-size: 9.5px;
  letter-spacing: 0.12em;
  color: var(--muted);
  text-transform: uppercase;
}

.card__stats dd {
  margin: 1px 0 0;
  font-size: 12px;
  color: var(--text-dim);
}

.card__path,
.card__message {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 11px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card__message {
  color: var(--warn);
}

.card__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.logbox {
  margin-top: var(--sp-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: #05060a;
  overflow: hidden;
}

.logbox__head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px var(--sp-3);
  border-bottom: 1px solid var(--border);
  font-size: 10.5px;
  letter-spacing: 0.1em;
  color: var(--muted);
  background: rgba(255, 255, 255, 0.02);
}

.logbox__body {
  margin: 0;
  padding: var(--sp-3);
  max-height: 240px;
  overflow: auto;
  font-size: 11px;
  line-height: 1.65;
  color: #9fb0c6;
  white-space: pre-wrap;
  word-break: break-all;
}

.fold-enter-active,
.fold-leave-active {
  transition: opacity var(--dur) var(--ease), transform var(--dur) var(--ease);
}

.fold-enter-from,
.fold-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
