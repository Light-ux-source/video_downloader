<script setup>
import { computed } from 'vue'
import { asciiBar } from '@/utils/format'

const props = defineProps({
  percent: { type: Number, default: null },
  state: { type: String, default: 'queued' },
  width: { type: Number, default: 26 },
  label: { type: String, default: '' },
})

const value = computed(() => Math.max(0, Math.min(100, Number(props.percent) || 0)))
const bar = computed(() => asciiBar(props.state === 'done' ? 100 : value.value, props.width))
const tone = computed(() => {
  if (props.state === 'done') return 'ok'
  if (props.state === 'error') return 'danger'
  if (props.state === 'canceled') return 'muted'
  if (props.percent === null || props.percent === undefined) return 'indeterminate'
  return 'neon'
})
</script>

<template>
  <div class="bar" :class="`bar--${tone}`">
    <span class="bar__ascii mono-num" aria-hidden="true">{{ bar }}</span>
    <span v-if="label" class="bar__label">{{ label }}</span>
    <div
      class="sr-only"
      role="progressbar"
      :aria-valuenow="state === 'done' ? 100 : value"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="label || '下载进度'"
    />
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  font-size: 12px;
  letter-spacing: 0.02em;
  white-space: nowrap;
  overflow: hidden;
}

.bar__ascii {
  color: var(--neon);
  text-shadow: 0 0 12px rgba(34, 211, 238, 0.35);
}

.bar--ok .bar__ascii {
  color: var(--ok);
  text-shadow: 0 0 12px rgba(74, 222, 128, 0.35);
}

.bar--danger .bar__ascii {
  color: var(--danger);
  text-shadow: none;
}

.bar--muted .bar__ascii {
  color: var(--muted);
  text-shadow: none;
}

.bar--indeterminate .bar__ascii {
  color: var(--violet);
  animation: flicker 1.6s steps(2, end) infinite;
}

.bar__label {
  color: var(--muted);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
}

@keyframes flicker {
  50% {
    opacity: 0.45;
  }
}
</style>
