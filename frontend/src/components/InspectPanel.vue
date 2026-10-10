<script setup>
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { state } from '@/store'
import { formatBytes, formatCount, formatDate, formatDuration } from '@/utils/format'

const props = defineProps({
  info: { type: Object, default: null },
  error: { type: String, default: '' },
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['download', 'clear', 'retry'])

const showSpecs = ref(false)
const selectedFormat = ref(null)
const thumbFailed = ref(false)

watch(
  () => props.info,
  () => {
    thumbFailed.value = false
    selectedFormat.value = null
  },
)

const isPlaylist = computed(() => props.info?.kind === 'playlist')

const videoFormats = computed(
  () => (props.info?.formats || []).filter((f) => f.has_video && f.height).slice(0, 40),
)

const audioFormats = computed(() =>
  (props.info?.formats || []).filter((f) => f.is_audio_only).slice(0, 8),
)

const metaRows = computed(() => {
  if (!props.info || isPlaylist.value) return []
  return [
    { label: '站点', value: props.info.extractor || '—' },
    { label: '时长', value: props.info.duration_text || formatDuration(props.info.duration) },
    { label: '播放量', value: formatCount(props.info.view_count) },
    { label: '发布', value: formatDate(props.info.upload_date) },
    { label: '字幕', value: props.info.has_subtitles ? (props.info.subtitle_langs || []).join(', ') || '有' : '无' },
    { label: '直播', value: props.info.is_live ? '是' : '否' },
  ]
})

function downloadWith(preset, override = {}) {
  emit('download', { preset, ...override })
}

function downloadSelected() {
  if (!selectedFormat.value) return
  downloadWith(state.settings.preset, {
    custom_format: selectedFormat.value,
    preset: 'best',
  })
}
</script>

<template>
  <section class="inspect panel" aria-labelledby="inspect-title">
    <div class="panel__head">
      <Icon name="scan" :size="14" />
      <h2 id="inspect-title" class="panel__title">Signal Inspect · 视频解析</h2>
      <span class="spacer" />
      <button
        v-if="info || error"
        class="btn btn--ghost btn--sm"
        aria-label="清除解析结果"
        @click="emit('clear')"
      >
        <Icon name="x" :size="13" />
        清除
      </button>
    </div>

    <div class="panel__body">
      <!-- 空态 -->
      <div v-if="!info && !error" class="empty">
        <div class="empty__glyph mono-num">
          <pre aria-hidden="true">  ▲
 ▼ ▼
▽   ▽</pre>
        </div>
        <p class="empty__title">等待信号</p>
        <p class="empty__text">
          在上方输入框粘贴视频链接并回车，这里会显示标题、时长、封面与全部可用画质。
        </p>
        <ul class="empty__tips">
          <li>支持 yt-dlp 覆盖的绝大多数站点（B 站 / YouTube / 微博 / 抖音需 Cookies 等）</li>
          <li>高清视频会自动分离音视频流，下载后由 ffmpeg 合流</li>
          <li>勾选"整个播放列表"可一次投递整季内容</li>
        </ul>
      </div>

      <!-- 错误态 -->
      <div v-else-if="error" class="error" role="alert">
        <Icon name="alert" :size="18" />
        <div>
          <p class="error__title">解析失败</p>
          <p class="error__text">{{ error }}</p>
          <p class="error__hint">
            常见原因：链接失效／地区限制／需要登录（在"高级参数"里配置 Cookies 或代理）。
          </p>
        </div>
        <button class="btn btn--sm" @click="emit('retry')">
          <Icon name="retry" :size="13" />
          重试
        </button>
      </div>

      <!-- 播放列表 -->
      <div v-else-if="isPlaylist" class="playlist">
        <header class="playlist__head">
          <div>
            <h3 class="playlist__title">{{ info.title }}</h3>
            <p class="playlist__meta mono-num">
              {{ info.entry_count || info.entries?.length || 0 }} 个条目 ·
              {{ info.uploader || '未知作者' }}
            </p>
          </div>
          <button
            class="btn btn--primary"
            :disabled="submitting"
            @click="emit('download', { playlist: true })"
          >
            <Icon name="download" :size="15" />
            下载整个列表
          </button>
        </header>
        <ol class="playlist__list">
          <li v-for="(entry, index) in info.entries" :key="entry.id || index">
            <span class="playlist__index mono-num">{{ String(index + 1).padStart(2, '0') }}</span>
            <a
              v-if="entry.url"
              class="playlist__name"
              :href="entry.url"
              target="_blank"
              rel="noreferrer noopener"
            >{{ entry.title || '未命名' }}</a>
            <span v-else class="playlist__name">{{ entry.title || '未命名' }}</span>
            <span class="playlist__dur mono-num">{{ formatDuration(entry.duration) }}</span>
          </li>
        </ol>
      </div>

      <!-- 单个视频 -->
      <div v-else class="video">
        <div class="video__main">
          <div class="thumb scanlines">
            <img
              v-if="info.thumbnail && !thumbFailed"
              :src="info.thumbnail"
              :alt="`${info.title} 的封面`"
              loading="lazy"
              referrerpolicy="no-referrer"
              @error="thumbFailed = true"
            />
            <div v-else class="thumb__fallback">
              <Icon name="film" :size="26" />
              <span>无封面</span>
            </div>
            <span v-if="info.duration_text" class="thumb__badge mono-num">
              {{ info.duration_text }}
            </span>
          </div>

          <div class="video__info">
            <h3 class="video__title">{{ info.title }}</h3>
            <p class="video__uploader">
              <Icon name="activity" :size="12" />
              {{ info.uploader || '未知作者' }}
            </p>
            <dl class="meta">
              <div v-for="row in metaRows" :key="row.label" class="meta__row">
                <dt>{{ row.label }}</dt>
                <dd class="mono-num">{{ row.value }}</dd>
              </div>
            </dl>
            <div class="video__actions">
              <button
                class="btn btn--primary"
                :disabled="submitting"
                @click="emit('download', { preset: state.settings.preset })"
              >
                <Icon name="download" :size="15" />
                {{ submitting ? '投递中…' : `按当前设置下载（${state.settings.preset === 'audio' ? '音频' : state.settings.preset}）` }}
              </button>
              <a
                class="btn btn--ghost btn--sm"
                :href="info.url"
                target="_blank"
                rel="noreferrer noopener"
              >
                <Icon name="external" :size="13" />
                打开原页
              </a>
              <button class="btn btn--ghost btn--sm" @click="showSpecs = !showSpecs">
                <Icon name="layers" :size="13" />
                {{ showSpecs ? '隐藏' : '查看' }}全部画质（{{ (info.formats || []).length }}）
              </button>
            </div>
          </div>
        </div>

        <transition name="fold">
          <div v-if="showSpecs" class="specs">
            <p class="specs__hint">
              点击任意一行选择该精确格式；也可直接使用上方"画质"预设（推荐，自动挑选最佳编码）。
            </p>
            <div class="specs__table" role="table" aria-label="可用格式列表">
              <div class="specs__row specs__row--head" role="row">
                <span role="columnheader">格式 ID</span>
                <span role="columnheader">说明</span>
                <span role="columnheader">编码</span>
                <span role="columnheader">体积</span>
                <span role="columnheader">操作</span>
              </div>
              <div
                v-for="fmt in [...videoFormats, ...audioFormats]"
                :key="fmt.format_id"
                class="specs__row"
                role="row"
                :class="{ 'specs__row--on': selectedFormat === fmt.format_id }"
              >
                <span class="mono-num" role="cell">{{ fmt.format_id }}</span>
                <span role="cell">
                  {{ fmt.label }}
                  <span v-if="fmt.note" class="specs__note">{{ fmt.note }}</span>
                </span>
                <span class="specs__codec" role="cell">{{ fmt.vcodec || fmt.acodec || '—' }}</span>
                <span class="mono-num" role="cell">{{ fmt.filesize_text || '未知' }}</span>
                <span role="cell">
                  <button
                    class="btn btn--ghost btn--sm"
                    @click="selectedFormat = selectedFormat === fmt.format_id ? null : fmt.format_id"
                  >
                    {{ selectedFormat === fmt.format_id ? '已选' : '选择' }}
                  </button>
                </span>
              </div>
            </div>
            <button class="btn btn--sm" :disabled="!selectedFormat || submitting" @click="downloadSelected">
              <Icon name="download" :size="13" />
              下载选中格式{{ selectedFormat ? `（${selectedFormat}）` : '' }}
            </button>
          </div>
        </transition>
      </div>
    </div>
  </section>
</template>

<style scoped>
.empty {
  display: grid;
  justify-items: center;
  gap: var(--sp-2);
  padding: var(--sp-6) var(--sp-4);
  text-align: center;
}

.empty__glyph pre {
  margin: 0;
  color: var(--neon);
  opacity: 0.5;
  font-size: 14px;
  line-height: 1.1;
  text-shadow: 0 0 20px rgba(34, 211, 238, 0.5);
}

.empty__title {
  margin: 0;
  font-size: 14px;
  letter-spacing: 0.16em;
  color: var(--text-dim);
}

.empty__text {
  max-width: 46ch;
  margin: 0;
  font-size: 12.5px;
  color: var(--muted);
}

.empty__tips {
  margin: var(--sp-3) 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
  font-size: 11.5px;
  color: var(--muted);
}

.empty__tips li::before {
  content: '· ';
  color: var(--neon);
}

.error {
  display: flex;
  gap: var(--sp-3);
  align-items: flex-start;
  padding: var(--sp-4);
  border: 1px solid rgba(251, 113, 133, 0.35);
  border-radius: var(--radius);
  background: var(--danger-soft);
  color: var(--danger);
}

.error__title {
  margin: 0 0 4px;
  font-weight: 700;
}

.error__text {
  margin: 0;
  font-size: 12.5px;
  color: #ffd7dd;
  word-break: break-word;
}

.error__hint {
  margin: var(--sp-2) 0 0;
  font-size: 11.5px;
  color: rgba(255, 215, 221, 0.75);
}

.video__main {
  display: grid;
  grid-template-columns: minmax(200px, 268px) 1fr;
  gap: var(--sp-4);
}

.thumb {
  position: relative;
  aspect-ratio: 16 / 9;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  background: #05060a;
}

.thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.thumb__fallback {
  display: grid;
  place-content: center;
  gap: 6px;
  justify-items: center;
  height: 100%;
  color: var(--muted);
  font-size: 11px;
}

.thumb__badge {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 1px 6px;
  border-radius: 2px;
  background: rgba(0, 0, 0, 0.75);
  border: 1px solid var(--border-strong);
  font-size: 11px;
}

.video__info {
  display: grid;
  gap: var(--sp-3);
  align-content: start;
  min-width: 0;
}

.video__title {
  font-size: 16px;
  line-height: 1.45;
  color: var(--text);
  word-break: break-word;
}

.video__uploader {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  color: var(--violet);
}

.meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 6px var(--sp-4);
  margin: 0;
}

.meta__row {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-2);
  border-bottom: 1px dashed rgba(255, 255, 255, 0.06);
  padding-bottom: 3px;
}

.meta__row dt {
  font-size: 10.5px;
  letter-spacing: 0.1em;
  color: var(--muted);
}

.meta__row dd {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}

.video__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  align-items: center;
}

.specs {
  margin-top: var(--sp-4);
  padding-top: var(--sp-4);
  border-top: 1px dashed var(--border);
}

.specs__hint {
  margin: 0 0 var(--sp-3);
  font-size: 11.5px;
  color: var(--muted);
}

.specs__table {
  display: grid;
  gap: 2px;
  max-height: 300px;
  overflow: auto;
  margin-bottom: var(--sp-3);
}

.specs__row {
  display: grid;
  grid-template-columns: 92px 1fr 150px 92px 84px;
  gap: var(--sp-2);
  align-items: center;
  padding: 5px var(--sp-2);
  border: 1px solid transparent;
  border-radius: 2px;
  font-size: 12px;
  color: var(--text-dim);
}

.specs__row:hover {
  background: rgba(255, 255, 255, 0.03);
}

.specs__row--head {
  position: sticky;
  top: 0;
  background: var(--panel-2);
  border-color: var(--border);
  font-size: 10.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
}

.specs__row--on {
  border-color: var(--neon-line);
  background: var(--neon-soft);
  color: var(--text);
}

.specs__note {
  color: var(--muted);
  font-size: 11px;
}

.specs__codec {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--muted);
}

.playlist__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-3);
  margin-bottom: var(--sp-3);
}

.playlist__title {
  font-size: 15px;
}

.playlist__meta {
  margin: 4px 0 0;
  font-size: 11.5px;
  color: var(--muted);
}

.playlist__list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 340px;
  overflow: auto;
  display: grid;
  gap: 2px;
}

.playlist__list li {
  display: grid;
  grid-template-columns: 34px 1fr auto;
  gap: var(--sp-2);
  align-items: baseline;
  padding: 5px var(--sp-2);
  border-bottom: 1px dashed rgba(255, 255, 255, 0.06);
  font-size: 12.5px;
}

.playlist__index {
  color: var(--neon);
  font-size: 11px;
}

.playlist__name {
  color: var(--text-dim);
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

a.playlist__name:hover {
  color: var(--neon);
  text-decoration: underline;
}

.playlist__dur {
  color: var(--muted);
  font-size: 11px;
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
  .video__main {
    grid-template-columns: 1fr;
  }

  .specs__row {
    grid-template-columns: 70px 1fr 70px;
  }

  .specs__row > :nth-child(3),
  .specs__row > :nth-child(4) {
    display: none;
  }
}
</style>
