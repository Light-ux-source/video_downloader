<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './components/Icon.vue'
import CommandDeck from './components/CommandDeck.vue'
import InspectPanel from './components/InspectPanel.vue'
import TaskQueue from './components/TaskQueue.vue'
import TelemetryRail from './components/TelemetryRail.vue'
import {
  activeCount,
  closeStream,
  initialize,
  inspect,
  notify,
  persistSettings,
  state,
  submitInspection,
  submitUrls,
  subscribeActive,
  taskList,
} from '@/store'

const urlInput = ref('')
const showAbout = ref(false)

const engineLabel = computed(() => {
  const engine = state.health?.engine
  if (!engine?.ok) return '引擎不可用'
  return `yt-dlp ${engine.version}`
})

const now = ref(new Date())
let clockTimer = null

onMounted(async () => {
  await initialize()
  subscribeActive()
  clockTimer = setInterval(() => {
    now.value = new Date()
  }, 1000)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  window.removeEventListener('keydown', onKeydown)
  for (const id of Object.keys(state.streams)) closeStream(id)
})

function onKeydown(event) {
  // "/" 聚焦输入框，Esc 清除解析结果
  if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
    event.preventDefault()
    document.getElementById('deck-url')?.focus()
  }
  if (event.key === 'Escape' && state.inspection) {
    state.inspection = null
  }
}

async function handleInspect(value) {
  if (!value) {
    notify('请输入视频链接', 'error')
    return
  }
  await inspect(value)
}

async function handleBatch(urls) {
  if (!urls.length) {
    notify('没有识别到有效链接', 'error')
    return
  }
  await submitUrls(urls)
}

async function handleDownload(overrides = {}) {
  await submitInspection(overrides)
}

function toggleScanlines() {
  state.settings.scanlines = !state.settings.scanlines
  persistSettings()
}

const clockText = computed(() =>
  now.value.toLocaleTimeString('zh-CN', { hour12: false }),
)
</script>

<template>
  <a class="skip-link" href="#main">跳到下载工作台</a>

  <div class="shell scanlines" :class="{ 'shell--noscan': !state.settings.scanlines }">
    <header class="topbar">
      <div class="brand">
        <svg class="brand__mark" viewBox="0 0 32 32" width="30" height="30" aria-hidden="true">
          <rect x="0.5" y="0.5" width="31" height="31" rx="5" fill="#0b0e13" stroke="rgba(34,211,238,.45)" />
          <path
            d="M16 6v13m0 0l-5-5m5 5l5-5M7 25h18"
            stroke="#22d3ee"
            stroke-width="2.4"
            fill="none"
            stroke-linecap="round"
          />
        </svg>
        <div class="brand__text">
          <span class="brand__name display">VIDEOHARBOR</span>
          <span class="brand__sub">本地视频下载工作台 · 基于 yt-dlp</span>
        </div>
      </div>

      <span class="spacer" />

      <div class="statusbar">
        <span class="tag" :class="state.health?.engine?.ok ? 'tag--ok' : 'tag--danger'">
          <Icon name="cpu" :size="11" />
          {{ engineLabel }}
        </span>
        <span class="tag" :class="state.health?.ffmpeg_ok ? 'tag--ok' : 'tag--warn'">
          <Icon name="bolt" :size="11" />
          ffmpeg {{ state.health?.ffmpeg_ok ? '就绪' : '缺失' }}
        </span>
        <span class="tag">
          <Icon name="activity" :size="11" />
          {{ activeCount }} / {{ state.health?.concurrency || 0 }} 并发
        </span>
        <span class="clock mono-num" :title="`本地时间 ${clockText}`">
          <Icon name="clock" :size="11" />
          {{ clockText }}
        </span>
        <button
          class="btn btn--ghost btn--sm btn--icon"
          :aria-pressed="state.settings.scanlines"
          :title="state.settings.scanlines ? '关闭扫描线效果' : '开启扫描线效果'"
          aria-label="切换扫描线效果"
          @click="toggleScanlines"
        >
          <Icon name="scan" :size="13" />
        </button>
        <button class="btn btn--ghost btn--sm" @click="showAbout = !showAbout">
          <Icon name="info" :size="13" />
          使用说明
        </button>
      </div>
    </header>

    <transition name="fold">
      <aside v-if="showAbout" class="about panel" aria-label="使用说明">
        <div class="about__grid">
          <div>
            <h3 class="about__title">三步下载</h3>
            <ol class="about__list">
              <li>粘贴链接 → 回车解析（快捷键 <kbd>/</kbd> 聚焦输入框）</li>
              <li>选择画质预设，需要字幕/整列表就在"附加"里勾选</li>
              <li>点下载 → 在任务队列里看实时进度与原始日志</li>
            </ol>
          </div>
          <div>
            <h3 class="about__title">需要登录的站点</h3>
            <p class="about__text">
              抖音、微博、部分 B 站高清内容需要 Cookies。在"高级参数"里选择「从浏览器读取 Cookies」，
              或导出 cookies.txt 后填写路径，然后重新解析。
            </p>
          </div>
          <div>
            <h3 class="about__title">文件位置</h3>
            <p class="about__text mono-num">
              {{ state.health?.download_dir || 'downloads/' }}
            </p>
            <p class="about__text">
              只监听本机回环地址（127.0.0.1），下载内容不会离开这台机器。
            </p>
          </div>
        </div>
        <button class="btn btn--ghost btn--sm about__close" @click="showAbout = false">
          <Icon name="x" :size="12" />
          关闭
        </button>
      </aside>
    </transition>

    <main id="main" class="main">
      <TelemetryRail />

      <div class="stage">
        <CommandDeck v-model:url="urlInput" @inspect="handleInspect" @batch="handleBatch" />

        <InspectPanel
          :info="state.inspection"
          :error="state.inspectError"
          :submitting="state.submitting"
          @download="handleDownload"
          @clear="state.inspection = null; state.inspectError = ''"
          @retry="handleInspect(urlInput)"
        />

        <TaskQueue />
      </div>
    </main>

    <footer class="footer">
      <span>VideoHarbor · 仅供个人学习与备份使用，请尊重内容版权与站点条款</span>
      <span class="spacer" />
      <span class="mono-num">任务 {{ taskList.length }} · 已完成 {{ state.stats?.done || 0 }}</span>
    </footer>

    <transition name="toast">
      <div
        v-if="state.toast"
        class="toast"
        :class="`toast--${state.toast.kind}`"
        role="status"
        aria-live="polite"
      >
        <Icon
          :name="state.toast.kind === 'error' ? 'alert' : state.toast.kind === 'success' ? 'check' : 'info'"
          :size="14"
        />
        <span>{{ state.toast.message }}</span>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  padding: var(--sp-4) var(--sp-5) var(--sp-5);
  max-width: 1560px;
  margin: 0 auto;
  position: relative;
}

.shell--noscan::before {
  display: none;
}

.topbar {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  padding: var(--sp-3) var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: linear-gradient(180deg, rgba(15, 18, 24, 0.92), rgba(8, 9, 13, 0.75));
  backdrop-filter: blur(6px);
  position: relative;
  overflow: hidden;
}

.topbar::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--neon-line), transparent);
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-width: 0;
}

.brand__mark {
  filter: drop-shadow(0 0 12px rgba(34, 211, 238, 0.45));
  flex: 0 0 auto;
}

.brand__text {
  display: grid;
  min-width: 0;
}

.brand__name {
  font-size: 16px;
  letter-spacing: 0.24em;
  color: var(--text);
  text-shadow: 0 0 18px rgba(34, 211, 238, 0.35);
}

.brand__sub {
  font-size: 10.5px;
  color: var(--muted);
  letter-spacing: 0.08em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.statusbar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.clock {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  color: var(--text-dim);
  padding: 2px 8px;
  border: 1px dashed var(--border-strong);
  border-radius: 2px;
}

.about {
  padding: var(--sp-4);
  position: relative;
}

.about__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--sp-4);
}

.about__title {
  font-size: 11.5px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--neon);
  margin-bottom: var(--sp-2);
}

.about__list {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  color: var(--text-dim);
  display: grid;
  gap: 3px;
}

.about__text {
  margin: 0 0 6px;
  font-size: 12.5px;
  color: var(--text-dim);
  line-height: 1.65;
  word-break: break-all;
}

.about__close {
  margin-top: var(--sp-3);
}

kbd {
  padding: 1px 5px;
  border: 1px solid var(--border-strong);
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.05);
  font-size: 11px;
}

.main {
  display: grid;
  grid-template-columns: 236px minmax(0, 1fr);
  gap: var(--sp-4);
  align-items: start;
}

.stage {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.footer {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border);
  font-size: 11px;
  color: var(--muted);
  flex-wrap: wrap;
}

.toast {
  position: fixed;
  left: 50%;
  bottom: var(--sp-5);
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  max-width: min(560px, 92vw);
  padding: var(--sp-3) var(--sp-4);
  border: 1px solid var(--neon-line);
  border-radius: var(--radius);
  background: rgba(9, 12, 17, 0.97);
  box-shadow: var(--glow), 0 20px 40px -20px rgba(0, 0, 0, 0.9);
  font-size: 12.5px;
  z-index: 40;
}

.toast--error {
  border-color: rgba(251, 113, 133, 0.55);
  color: #ffdbe1;
}

.toast--success {
  border-color: rgba(74, 222, 128, 0.5);
  color: #dcffe8;
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity var(--dur) var(--ease), transform var(--dur) var(--ease);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, 12px);
}

.fold-enter-active,
.fold-leave-active {
  transition: opacity var(--dur) var(--ease), transform var(--dur) var(--ease);
}

.fold-enter-from,
.fold-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

@media (max-width: 1024px) {
  .shell {
    padding: var(--sp-3);
  }

  .main {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .brand__sub {
    display: none;
  }

  .statusbar {
    justify-content: flex-start;
  }

  .topbar {
    flex-wrap: wrap;
  }
}
</style>
