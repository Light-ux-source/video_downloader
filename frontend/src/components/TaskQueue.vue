<script setup>
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import TaskCard from './TaskCard.vue'
import { activeCount, clearFinished, revealDownloadDir, state, taskList } from '@/store'
import { subscribeActive } from '@/store'

const filter = ref('all')

const FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'active', label: '进行中' },
  { id: 'done', label: '已完成' },
  { id: 'failed', label: '失败/取消' },
]

const counts = computed(() => ({
  all: taskList.value.length,
  active: taskList.value.filter((t) => ['running', 'queued'].includes(t.state)).length,
  done: taskList.value.filter((t) => t.state === 'done').length,
  failed: taskList.value.filter((t) => ['error', 'canceled'].includes(t.state)).length,
}))

const filtered = computed(() => {
  switch (filter.value) {
    case 'active':
      return taskList.value.filter((t) => ['running', 'queued'].includes(t.state))
    case 'done':
      return taskList.value.filter((t) => t.state === 'done')
    case 'failed':
      return taskList.value.filter((t) => ['error', 'canceled'].includes(t.state))
    default:
      return taskList.value
  }
})

const hasFinished = computed(() => counts.value.done + counts.value.failed > 0)
</script>

<template>
  <section class="queue panel" aria-labelledby="queue-title">
    <div class="panel__head">
      <Icon name="queue" :size="14" />
      <h2 id="queue-title" class="panel__title">Download Queue · 任务队列</h2>
      <span v-if="activeCount" class="tag tag--neon">
        <Icon name="activity" :size="11" />
        {{ activeCount }} 进行中
      </span>
      <span class="spacer" />
      <nav class="filters" aria-label="任务筛选">
        <button
          v-for="item in FILTERS"
          :key="item.id"
          class="filter"
          :class="{ 'filter--on': filter === item.id }"
          :aria-pressed="filter === item.id"
          @click="filter = item.id"
        >
          {{ item.label }}
          <span class="filter__num mono-num">{{ counts[item.id] }}</span>
        </button>
      </nav>
      <button class="btn btn--ghost btn--sm" @click="revealDownloadDir">
        <Icon name="folder" :size="12" />
        打开目录
      </button>
      <button class="btn btn--ghost btn--sm" :disabled="!hasFinished" @click="clearFinished">
        <Icon name="trash" :size="12" />
        清理已结束
      </button>
      <button class="btn btn--ghost btn--sm" @click="subscribeActive">
        <Icon name="refresh" :size="12" />
        重连进度
      </button>
    </div>

    <div class="panel__body">
      <p v-if="!filtered.length" class="queue__empty">
        <Icon name="info" :size="13" />
        当前筛选下暂无任务。在上方"下载工作台"解析链接后即可投递下载。
      </p>

      <transition-group v-else name="queue" tag="div" class="queue__list">
        <TaskCard v-for="task in filtered" :key="task.id" :task="task" />
      </transition-group>
    </div>
  </section>
</template>

<style scoped>
.filters {
  display: flex;
  gap: 2px;
  border: 1px solid var(--border);
  border-radius: 2px;
  overflow: hidden;
}

.filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 11.5px;
  transition: all var(--dur) var(--ease);
}

.filter:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.04);
}

.filter--on {
  color: var(--neon);
  background: var(--neon-soft);
}

.filter__num {
  font-size: 10px;
  padding: 0 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.08);
}

.queue__empty {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: var(--sp-5) var(--sp-3);
  justify-content: center;
  font-size: 12.5px;
  color: var(--muted);
  border: 1px dashed var(--border);
  border-radius: var(--radius);
}

.queue__list {
  display: grid;
  gap: var(--sp-3);
}

.queue-enter-active,
.queue-leave-active {
  transition: opacity var(--dur-slow) var(--ease), transform var(--dur-slow) var(--ease);
}

.queue-enter-from {
  opacity: 0;
  transform: translateY(-10px);
}

.queue-leave-to {
  opacity: 0;
  transform: translateX(14px);
}

@media (max-width: 900px) {
  .filters {
    order: 3;
  }
}
</style>
