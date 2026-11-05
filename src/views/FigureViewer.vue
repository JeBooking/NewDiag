<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { fileUrl } from '@/services/files'
import { linePoints, loadFigure, type Figure } from '@/services/figures'

const route = useRoute()
const src = typeof route.query.src === 'string' && /^[a-f0-9]{64}$/.test(route.query.src)
  ? route.query.src : null
const figure: Figure | null = loadFigure(typeof route.query.id === 'string' ? route.query.id : '')
  ?? (src ? { type: 'image', src } : null)
const zoom = ref(100)
const original = ref(false)
const fitted = ref(true)
const broken = ref(false)
const title = figure?.title || (figure?.type === 'line' ? '数据曲线' : '数据图片')
const width = computed(() => original.value ? 'auto' : `${zoom.value}%`)
document.title = `${title} · 图形查看`
// 预览只读取创建窗口时复制的数据，不再需要访问来源页面。
window.opener = null

function scale(delta: number): void {
  original.value = false
  fitted.value = false
  zoom.value = Math.min(400, Math.max(25, zoom.value + delta))
}

function fit(): void {
  original.value = false
  fitted.value = true
  zoom.value = 100
}
</script>

<template>
  <main class="figure-viewer">
    <header>
      <h1>{{ title }}</h1>
      <div v-if="figure && !broken" class="tools" role="group" aria-label="图形缩放">
        <el-button :disabled="!original && zoom <= 25" @click="scale(-25)">缩小</el-button>
        <span aria-live="polite">{{ original ? '原始尺寸' : `${zoom}%` }}</span>
        <el-button :disabled="!original && zoom >= 400" @click="scale(25)">放大</el-button>
        <el-button @click="fit">适应窗口</el-button>
        <el-button v-if="figure.type === 'image'" @click="original = true; fitted = false">原始尺寸</el-button>
      </div>
    </header>
    <div v-if="!figure" class="empty" role="status">图形数据已失效，请回到诊断页面重新点击图形打开。</div>
    <div v-else-if="broken" class="empty" role="alert">图片不可用，请回到诊断页面检查文件。</div>
    <div v-else class="viewport" :class="{ fitted }" tabindex="0" aria-label="图形内容，可滚动查看放大部分">
      <figure :style="{ width }" :class="{ original }">
        <template v-if="figure.type === 'line'">
          <svg viewBox="0 0 400 120" role="img" :aria-label="title">
            <polyline :points="linePoints(figure.values)" fill="none" stroke="currentColor"
              stroke-width="2" vector-effect="non-scaling-stroke" />
            <circle v-if="figure.values.length === 1" cx="10" cy="110" r="2" fill="currentColor" />
          </svg>
          <p v-if="!figure.values.length">暂无曲线数据</p>
        </template>
        <img v-else :src="fileUrl(figure.src, true)" :alt="figure.alt || title" @error="broken = true" />
        <figcaption v-if="figure.type === 'image' && figure.caption">{{ figure.caption }}</figcaption>
      </figure>
      <details v-if="figure.type === 'line'">
        <summary>查看曲线数值 {{ figure.unit }}</summary>
        <p>{{ figure.values.join('，') }}</p>
      </details>
    </div>
  </main>
</template>

<style scoped>
.figure-viewer { height: 100%; display: flex; flex-direction: column; background: var(--el-bg-color); }
header { display: flex; align-items: center; flex-wrap: wrap; gap: 16px; padding: 16px 24px;
  border-bottom: 1px solid var(--el-border-color-lighter); }
h1 { flex: 1; min-width: 120px; margin: 0; font-size: 18px; overflow-wrap: anywhere; }
.tools { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.tools .el-button + .el-button { margin-left: 0; }
.tools span { min-width: 64px; text-align: center; font-size: 13px; }
.viewport { flex: 1; min-height: 0; overflow: auto; padding: 24px; }
.viewport.fitted { display: flex; flex-direction: column; }
.fitted figure { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.fitted img, .fitted svg { flex: 1; min-height: 0; height: 0; object-fit: contain; }
figure { margin: 0 auto; }
figure.original { width: max-content !important; }
img, svg { display: block; width: 100%; height: auto; }
.original img { width: auto; max-width: none; }
svg { color: var(--el-color-primary); min-height: 200px; }
figcaption, details { margin-top: 16px; font-size: 14px; overflow-wrap: anywhere; }
figcaption { color: var(--el-text-color-secondary); white-space: pre-wrap; }
.empty { margin: auto; padding: 24px; color: var(--el-text-color-secondary); }
:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; }
</style>
