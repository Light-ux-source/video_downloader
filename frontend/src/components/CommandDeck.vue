<script setup>
import { computed, reactive, ref } from 'vue'
import Icon from './Icon.vue'
import { state, persistSettings, notify } from '@/store'
import { parseUrls } from '@/utils/format'

const emit = defineEmits(['inspect', 'batch'])

const url = defineModel('url', { type: String, default: '' })
const mode = ref('single')
const advanced = ref(false)
const batchText = ref('')

const settings = reactive(state.settings)

const batchUrls = computed(() => parseUrls(batchText.value))
const canSubmit = computed(() =>
  mode.value === 'single' ? /^https?:\/\//i.test(url.value.trim()) : batchUrls.value.length > 0,
)

function save() {
  persistSettings()
}

function pasteFromClipboard() {
  if (!navigator.clipboard?.readText) {
    notify('当前浏览器不支持读取剪贴板，请手动粘贴', 'error')
    return
  }
  navigator.clipboard
    .readText()
    .then((text) => {
      const found = parseUrls(text)
      if (mode.value === 'single') {
        url.value = found[0] || text.trim()
      } else {
        batchText.value = found.join('\n') || text
      }
      notify(`已粘贴 ${mode.value === 'single' ? 1 : found.length} 个链接`, 'info')
    })
    .catch(() => notify('读取剪贴板失败，请手动粘贴', 'error'))
}

function submit() {
  if (mode.value === 'single') {
    emit('inspect', url.value.trim())
  } else {
    emit('batch', batchUrls.value)
  }
}

function onEnter(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submit()
  }
}
</script>

<template>
  <section class="deck panel" aria-labelledby="deck-title">
    <div class="panel__head">
      <Icon name="terminal" :size="14" />
      <h2 id="deck-title" class="panel__title">Command Deck · 下载工作台</h2>
      <span class="spacer" />
      <div class="modes" role="tablist" aria-label="输入模式">
        <button
          class="mode"
          role="tab"
          :aria-selected="mode === 'single'"
          :class="{ 'mode--on': mode === 'single' }"
          @click="mode = 'single'"
        >
          单个链接
        </button>
        <button
          class="mode"
          role="tab"
          :aria-selected="mode === 'batch'"
          :class="{ 'mode--on': mode === 'batch' }"
          @click="mode = 'batch'"
        >
          批量投递
        </button>
      </div>
    </div>

    <div class="panel__body">
      <form class="command" @submit.prevent="submit">
        <label class="sr-only" for="deck-url">视频链接</label>
        <span class="command__prompt" aria-hidden="true">▸</span>

        <input
          v-if="mode === 'single'"
          id="deck-url"
          v-model="url"
          class="command__input"
          :class="{ 'command__input--scanning': state.inspecting }"
          type="url"
          inputmode="url"
          autocomplete="off"
          spellcheck="false"
          placeholder="粘贴视频链接，例如 https://www.bilibili.com/video/BV1xx411c7mD"
          @keydown="onEnter"
        />
        <textarea
          v-else
          id="deck-url"
          v-model="batchText"
          class="textarea command__textarea"
          spellcheck="false"
          placeholder="每行一个链接（也可用空格、逗号分隔），一次最多 50 个&#10;https://...&#10;https://..."
        />

        <div class="command__actions">
          <button
            type="button"
            class="btn btn--ghost btn--icon"
            title="从剪贴板粘贴"
            aria-label="从剪贴板粘贴链接"
            @click="pasteFromClipboard"
          >
            <Icon name="copy" :size="15" />
          </button>
          <button type="submit" class="btn btn--primary" :disabled="!canSubmit || state.inspecting">
            <Icon :name="state.inspecting ? 'scan' : mode === 'single' ? 'search' : 'queue'" :size="15" />
            <span v-if="state.inspecting">解析中…</span>
            <span v-else-if="mode === 'single'">解析链接</span>
            <span v-else>投递 {{ batchUrls.length }} 个</span>
          </button>
        </div>

        <span v-if="mode === 'single' && state.inspecting" class="sweep" aria-hidden="true" />
      </form>

      <p v-if="mode === 'batch'" class="hint">
        <Icon name="info" :size="12" />
        批量模式下将使用下方"下载设置"的参数依次排队下载；识别到 {{ batchUrls.length }} 个链接。
      </p>

      <div class="quick">
        <div class="quick__group">
          <span class="quick__label">画质</span>
          <div class="chips" role="radiogroup" aria-label="画质预设">
            <button
              v-for="preset in state.presets"
              :key="preset.id"
              class="chip"
              role="radio"
              :aria-checked="settings.preset === preset.id"
              :class="{ 'chip--on': settings.preset === preset.id }"
              :title="preset.hint"
              @click="
                settings.preset = preset.id;
                save()
              "
            >
              <Icon :name="preset.audio_only ? 'music' : 'film'" :size="12" />
              {{ preset.label }}
            </button>
          </div>
        </div>

        <div class="quick__group">
          <span class="quick__label">附加</span>
          <div class="chips">
            <label class="check">
              <input v-model="settings.subtitles" type="checkbox" @change="save" />
              内嵌字幕
            </label>
            <label class="check">
              <input v-model="settings.playlist" type="checkbox" @change="save" />
              整个播放列表
            </label>
            <label class="check">
              <input v-model="settings.skipExisting" type="checkbox" @change="save" />
              跳过已存在
            </label>
          </div>
        </div>

        <button
          class="btn btn--ghost btn--sm advanced-toggle"
          :aria-expanded="advanced"
          @click="advanced = !advanced"
        >
          <Icon name="settings" :size="13" />
          高级参数{{ advanced ? '（收起）' : '' }}
        </button>
      </div>

      <transition name="fold">
        <div v-if="advanced" class="advanced">
          <div class="grid">
            <label class="field">
              <span class="field__label">音频格式</span>
              <select v-model="settings.audioFormat" class="select" @change="save">
                <option v-for="fmt in state.audioFormats" :key="fmt" :value="fmt">
                  {{ fmt.toUpperCase() }}
                </option>
              </select>
            </label>

            <label class="field">
              <span class="field__label">字幕语言</span>
              <input
                v-model="settings.subtitleLangs"
                class="input"
                placeholder="zh-Hans,zh-CN,en"
                @change="save"
              />
            </label>

            <label class="field">
              <span class="field__label">限速（如 2M / 500K）</span>
              <input v-model="settings.limitRate" class="input" placeholder="不限速" @change="save" />
            </label>

            <label class="field">
              <span class="field__label">并发分片</span>
              <input
                v-model.number="settings.concurrentFragments"
                class="input"
                type="number"
                min="1"
                max="16"
                @change="save"
              />
            </label>

            <label class="field">
              <span class="field__label">代理</span>
              <input
                v-model="settings.proxy"
                class="input"
                placeholder="socks5://127.0.0.1:1080"
                @change="save"
              />
            </label>

            <label class="field">
              <span class="field__label">Cookies 文件路径</span>
              <input
                v-model="settings.cookiesFile"
                class="input"
                placeholder="/path/to/cookies.txt"
                @change="save"
              />
            </label>

            <label class="field">
              <span class="field__label">从浏览器读取 Cookies</span>
              <select v-model="settings.cookiesFromBrowser" class="select" @change="save">
                <option value="">不使用</option>
                <option value="chrome">Chrome</option>
                <option value="edge">Edge</option>
                <option value="firefox">Firefox</option>
                <option value="brave">Brave</option>
                <option value="safari">Safari（可能受限）</option>
              </select>
            </label>

            <label class="field">
              <span class="field__label">播放列表条目（如 1-5,8）</span>
              <input
                v-model="settings.playlistItems"
                class="input"
                placeholder="留空 = 全部"
                @change="save"
              />
            </label>
          </div>

          <div class="custom">
            <label class="check">
              <input v-model="settings.useCustomFormat" type="checkbox" @change="save" />
              覆盖画质，使用自定义 yt-dlp 格式表达式
            </label>
            <input
              v-model="settings.customFormat"
              class="input"
              placeholder="bv*[height<=1080][vcodec^=avc1]+ba/b"
              :disabled="!settings.useCustomFormat"
              @change="save"
            />
          </div>
        </div>
      </transition>
    </div>
  </section>
</template>

<style scoped>
.command {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  padding: var(--sp-2);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  background: rgba(0, 0, 0, 0.42);
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
  overflow: hidden;
}

.command:focus-within {
  border-color: var(--neon-line);
  box-shadow: 0 0 0 3px rgba(34, 211, 238, 0.12), inset 0 0 30px -18px var(--neon);
}

.command__prompt {
  padding: 6px 2px 0 6px;
  color: var(--neon);
  font-weight: 700;
  text-shadow: 0 0 12px rgba(34, 211, 238, 0.6);
}

.command__input,
.command__textarea {
  flex: 1 1 auto;
  border: none;
  background: transparent;
  min-height: 34px;
  padding: 6px 4px;
  font-size: 13.5px;
}

.command__textarea {
  min-height: 92px;
}

.command__input:focus,
.command__textarea:focus {
  outline: none;
  box-shadow: none;
}

.command__input::placeholder,
.command__textarea::placeholder {
  color: #5d6674;
}

.command__actions {
  display: flex;
  gap: var(--sp-2);
  align-items: center;
}

/* 解析时的"信号锁定"扫描线 */
.sweep {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--neon), transparent);
  animation: sweep 1.4s linear infinite;
}

@keyframes sweep {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}

.modes {
  display: flex;
  border: 1px solid var(--border);
  border-radius: 2px;
  overflow: hidden;
}

.mode {
  padding: 3px 10px;
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 11.5px;
  transition: all var(--dur) var(--ease);
}

.mode:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.04);
}

.mode--on {
  color: var(--neon);
  background: var(--neon-soft);
}

.hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: var(--sp-3) 0 0;
  font-size: 11.5px;
  color: var(--muted);
}

.quick {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-4);
  margin-top: var(--sp-4);
}

.quick__group {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

.quick__label {
  font-size: 10.5px;
  letter-spacing: 0.14em;
  color: var(--muted);
  text-transform: uppercase;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.02);
  color: var(--text-dim);
  font-size: 12px;
  transition: all var(--dur) var(--ease);
}

.chip:hover {
  color: var(--text);
  border-color: var(--border-strong);
}

.chip--on {
  color: var(--neon);
  border-color: var(--neon-line);
  background: var(--neon-soft);
  box-shadow: inset 0 0 18px -12px var(--neon);
}

.advanced-toggle {
  margin-left: auto;
}

.advanced {
  margin-top: var(--sp-4);
  padding-top: var(--sp-4);
  border-top: 1px dashed var(--border);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: var(--sp-3);
}

.custom {
  display: grid;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}

.fold-enter-active,
.fold-leave-active {
  transition: opacity var(--dur) var(--ease), transform var(--dur) var(--ease);
}

.fold-enter-from,
.fold-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

@media (max-width: 768px) {
  .command {
    flex-wrap: wrap;
  }

  .command__actions {
    width: 100%;
  }

  .command__actions .btn--primary {
    flex: 1 1 auto;
  }

  .advanced-toggle {
    margin-left: 0;
  }
}
</style>
