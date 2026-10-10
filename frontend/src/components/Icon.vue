<script setup>
// 统一 SVG 图标（不使用 emoji，保证跨平台渲染一致与可访问性）
const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
  strokeWidth: { type: [Number, String], default: 1.7 },
})

const PATHS = {
  download: [
    'M12 4v11',
    'M7.5 10.5 12 15l4.5-4.5',
    'M5 19h14',
  ],
  link: ['M10 13.5a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-1 1'],
  unlink: ['M9.5 14.5 5 19', 'M14.5 9.5 19 5', 'M8 8l-2.5-2.5', 'M16 16l2.5 2.5'],
  search: ['M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z', 'M16.5 16.5 21 21'],
  scan: ['M4 8V5h3', 'M20 8V5h-3', 'M4 16v3h3', 'M20 16v3h-3', 'M4 12h16'],
  queue: ['M4 6h10', 'M4 12h10', 'M4 18h7', 'M17 14v6l4-3-4-3Z'],
  check: ['M5 13l4 4L19 7'],
  x: ['M6 6l12 12', 'M18 6 6 18'],
  alert: ['M12 4 2.5 20h19L12 4Z', 'M12 10v5', 'M12 17.6v.4'],
  info: ['M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17Z', 'M12 11v6', 'M12 7.6v.4'],
  folder: ['M3.5 6.5h5l2 2.5h10v9h-17v-11.5Z'],
  refresh: ['M20 11a8 8 0 1 0-2.3 6.3', 'M20 5v6h-6'],
  retry: ['M21 12a9 9 0 1 1-3-6.7', 'M21 4v5h-5'],
  play: ['M7 4.5 19 12 7 19.5v-15Z'],
  pause: ['M8 5v14', 'M16 5v14'],
  music: ['M9 18V6l10-2v12', 'M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z', 'M19 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z'],
  film: ['M3.5 5.5h17v13h-17v-13Z', 'M3.5 9.5h17', 'M3.5 14.5h17', 'M8 5.5v13', 'M16 5.5v13'],
  list: ['M4 6h1', 'M4 12h1', 'M4 18h1', 'M8 6h12', 'M8 12h12', 'M8 18h12'],
  cpu: ['M8 8h8v8H8V8Z', 'M4.5 4.5h15v15h-15v-15Z', 'M12 2v2.5', 'M12 19.5V22', 'M2 12h2.5', 'M19.5 12H22'],
  disk: ['M4 5.5h16v13H4v-13Z', 'M7 12h10', 'M7.5 15.5h2'],
  clock: ['M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z', 'M12 8v4.5l3 2'],
  external: ['M14 4h6v6', 'M20 4 11 13', 'M18 14v5.5H4.5V6H10'],
  copy: ['M9.5 9.5h10v10h-10v-10Z', 'M5 15V4.5h10'],
  trash: ['M5 7h14', 'M9 7V5h6v2', 'M7 7l1 13h8l1-13'],
  warn: ['M12 4 2.5 20h19L12 4Z', 'M12 10v5'],
  settings: ['M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z', 'M4.5 12h2M17.5 12h2M12 4.5v2M12 17.5v2'],
  bolt: ['M13 3 5 14h6l-1 7 8-11h-6l1-7Z'],
  activity: ['M3 12h4l2.5-6 4 12 2.5-6h5'],
  shield: ['M12 3.5 5 6v6c0 4 3 7.2 7 8.5 4-1.3 7-4.5 7-8.5V6l-7-2.5Z'],
  terminal: ['M4.5 5.5h15v13h-15v-13Z', 'M8 10l2.5 2.5L8 15', 'M13 15h4'],
  layers: ['M12 3.5 3.5 8 12 12.5 20.5 8 12 3.5Z', 'M3.5 12.5 12 17l8.5-4.5'],
}
</script>

<template>
  <svg
    class="icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    :stroke-width="strokeWidth"
    stroke="currentColor"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="(d, index) in PATHS[name] || []" :key="index" :d="d" />
  </svg>
</template>

<style scoped>
.icon {
  flex: 0 0 auto;
  display: block;
}
</style>
