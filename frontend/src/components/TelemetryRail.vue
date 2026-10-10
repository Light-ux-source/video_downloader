<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'
import { state, activeCount } from '@/store'
import { formatBytes } from '@/utils/format'

const engine = computed(() => state.health?.engine || {})
const stats = computed(() => state.stats || {})

const rows = computed(() => [
  {
    label: 'ENGINE',
    value: engine.value.version ? `yt-dlp ${engine.value.version}` : '不可用',
    ok: Boolean(engine.value.ok),
  },
  {
    label: 'FFMPEG',
    value: engine.value.ffmpeg ? `v${engine.value.ffmpeg.split(' ')[0]}` : '缺失',
    ok: Boolean(engine.value.ffmpeg),
  },
  {
    label: 'DOWNLOAD',
    value: `${stats.value.running || 0}/${state.health?.concurrency || '—'}`,
    ok: true,
  },
  {
    label: 'QUEUE',
    value: `${stats.value.queued || 0} 等待`,
    ok: (stats.value.queued || 0) === 0,
  },
])

const doneRatio = computed(() => {
  const total = stats.value.total || 0
  if (!total) return 0
  return Math.round(((stats.value.done || 0) / total) * 100)
})

const diskPercent = computed(() => {
  const free = state.health?.disk_free
  if (!free) return 0
  // 以 1TB 作为参考刻度展示剩余占比
  return Math.max(2, Math.min(100, (free / 1024 ** 4) * 100))
})
</script>

<template>
  <aside class="rail panel panel--flush" aria-label="系统遥测">
    <div class="rail__head">
      <span class="pulse" :class="{ 'pulse--off': !state.serverOnline }" aria-hidden="true" />
      <span class="rail__title display">TELEMETRY</span>
    </div>

    <div class="rail__status">
      <span class="tag" :class="state.serverOnline ? 'tag--ok' : 'tag--danger'">
        <Icon :name="state.serverOnline ? 'check' : 'alert'" :size="12" />
        {{ state.serverOnline ? 'LINK UP' : 'OFFLINE' }}
      </span>
      <span class="rail__active mono-num">{{ activeCount }} 进行中</span>
    </div>

    <dl class="rows">
      <div v-for="row in rows" :key="row.label" class="row">
        <dt>{{ row.label }}</dt>
        <dd :class="{ 'row--warn': !row.ok }" class="mono-num">{{ row.value }}</dd>
      </div>
    </dl>

    <div class="gauge">
      <div class="gauge__label">
        <span>完成率</span>
        <span class="mono-num">{{ doneRatio }}%</span>
      </div>
      <div
        class="gauge__track"
        role="progressbar"
        :aria-valuenow="doneRatio"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-label="任务完成率"
      >
        <div class="gauge__fill" :style="{ width: `${doneRatio}%` }" />
      </div>
    </div>

    <div class="gauge">
      <div class="gauge__label">
        <span>磁盘剩余</span>
        <span class="mono-num">{{ state.health?.disk_free_text || '—' }}</span>
      </div>
      <div
        class="gauge__track gauge__track--violet"
        role="progressbar"
        :aria-valuenow="Math.round(diskPercent)"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-label="磁盘剩余空间"
      >
        <div class="gauge__fill" :style="{ width: `${diskPercent}%` }" />
      </div>
    </div>

    <div class="counters">
      <div class="counter">
        <span class="counter__num mono-num">{{ stats.done || 0 }}</span>
        <span class="counter__label">完成</span>
      </div>
      <div class="counter">
        <span class="counter__num mono-num counter__num--danger">{{ stats.error || 0 }}</span>
        <span class="counter__label">失败</span>
      </div>
      <div class="counter">
        <span class="counter__num mono-num">{{ stats.canceled || 0 }}</span>
        <span class="counter__label">取消</span>
      </div>
    </div>

    <p class="rail__foot">
      <Icon name="shield" :size="12" />
      仅监听本机回环地址，数据不出本机
    </p>
  </aside>
</template>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  padding: var(--sp-4);
  position: sticky;
  top: var(--sp-4);
}

.rail__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

.rail__title {
  font-size: 12px;
  letter-spacing: 0.22em;
  color: var(--neon);
  text-shadow: 0 0 14px rgba(34, 211, 238, 0.4);
}

.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.6);
  animation: pulse 2.4s var(--ease) infinite;
}

.pulse--off {
  background: var(--danger);
  animation: none;
}

@keyframes pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.55);
  }
  70% {
    box-shadow: 0 0 0 8px rgba(74, 222, 128, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(74, 222, 128, 0);
  }
}

.rail__status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
}

.rail__active {
  font-size: 11px;
  color: var(--muted);
}

.rows {
  margin: 0;
  display: grid;
  gap: 2px;
}

.row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  padding: 5px 0;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.06);
}

.row dt {
  font-size: 10.5px;
  letter-spacing: 0.14em;
  color: var(--muted);
}

.row dd {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-dim);
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}

.row--warn {
  color: var(--warn) !important;
}

.gauge {
  display: grid;
  gap: 6px;
}

.gauge__label {
  display: flex;
  justify-content: space-between;
  font-size: 10.5px;
  letter-spacing: 0.12em;
  color: var(--muted);
}

.gauge__track {
  height: 6px;
  border: 1px solid var(--border);
  border-radius: 1px;
  background: rgba(0, 0, 0, 0.4);
  overflow: hidden;
}

.gauge__fill {
  height: 100%;
  background: linear-gradient(90deg, rgba(34, 211, 238, 0.5), var(--neon));
  transition: width var(--dur-slow) var(--ease);
}

.gauge__track--violet .gauge__fill {
  background: linear-gradient(90deg, rgba(167, 139, 250, 0.45), var(--violet));
}

.counters {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-2);
  padding-top: var(--sp-2);
  border-top: 1px solid var(--border);
}

.counter {
  display: grid;
  gap: 2px;
  text-align: center;
}

.counter__num {
  font-size: 18px;
  font-weight: 700;
  color: var(--text);
}

.counter__num--danger {
  color: var(--danger);
}

.counter__label {
  font-size: 10px;
  letter-spacing: 0.1em;
  color: var(--muted);
}

.rail__foot {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  margin: auto 0 0;
  padding-top: var(--sp-3);
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--muted);
}

@media (max-width: 1024px) {
  .rail {
    position: static;
  }
}
</style>
