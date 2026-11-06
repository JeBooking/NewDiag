<script setup lang="ts">
import { computed, reactive } from 'vue'
import { fileUrl } from '@/services/files'

const props = defineProps<{ value?: unknown }>()

interface Block {
  type: string
  title?: string
  text?: string
  items?: { label: string; value: unknown; unit?: string }[]
  columns?: string[]
  rows?: unknown[][]
  values?: number[]
  unit?: string
  src?: string
  alt?: string
  caption?: string
}

// 按 src 记失败：同一份数据里多张图互不牵连，也避免重复请求坏图
const failed = reactive(new Set<string>())

function imageBroken(src?: string): boolean {
  return !src || failed.has(src)
}

function markBroken(src?: string): void {
  if (src) failed.add(src)
}

function openImage(src?: string): void {
  if (src) window.open(fileUrl(src, true), '_blank', 'noopener,noreferrer,width=1100,height=760')
}

const blocks = computed<Block[]>(() => {
  if (!props.value || typeof props.value !== 'object') return []
  const raw = (props.value as { blocks?: unknown }).blocks
  return Array.isArray(raw)
    ? raw.filter((block): block is Block =>
      !!block && typeof block === 'object' && typeof block.type === 'string')
    : []
})

function points(values: number[] = []): string {
  const min = values.reduce((value, item) => Math.min(value, item), Infinity)
  const max = values.reduce((value, item) => Math.max(value, item), -Infinity)
  return values.map((value, index) =>
    [10 + index * 380 / Math.max(values.length - 1, 1),
      110 - (value - min) * 100 / (max - min || 1)].join(','),
  ).join(' ')
}

function text(value: unknown): string {
  return typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')
}
</script>

<template>
  <div v-if="blocks.length" class="presentation">
    <section v-for="(block, index) in blocks" :key="index" class="block">
      <strong v-if="block.title">{{ block.title }}</strong>
      <p v-if="block.type === 'text'">{{ block.text }}</p>
      <dl v-else-if="block.type === 'metrics'">
        <div v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <dt>{{ item.label }}</dt>
          <dd>{{ text(item.value) }} {{ item.unit }}</dd>
        </div>
      </dl>
      <div v-else-if="block.type === 'table'" class="table-wrap" tabindex="0">
        <table>
          <caption>{{ block.title || '数据表' }}</caption>
          <thead>
            <tr><th v-for="column in block.columns" :key="column" scope="col">{{ column }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in block.rows" :key="rowIndex">
              <td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ text(cell) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <template v-else-if="block.type === 'line'">
        <svg viewBox="0 0 400 120" role="img" :aria-label="block.title || '数据曲线'">
          <polyline
            :points="points(block.values)" fill="none" stroke="currentColor" stroke-width="2"
          />
          <circle v-if="block.values?.length === 1" cx="10" cy="110" r="3" fill="currentColor" />
        </svg>
        <details><summary>查看曲线数值 {{ block.unit }}</summary>{{ block.values?.join('，') }}</details>
      </template>
      <figure v-else-if="block.type === 'image'" class="image-block">
        <img
          v-if="!imageBroken(block.src)"
          :src="fileUrl(block.src ?? '', true)"
          :alt="block.alt || '数据图片'"
          loading="lazy"
          class="image"
          @error="markBroken(block.src)"
          tabindex="0"
          role="button"
          aria-label="在独立窗口查看图片"
          @click="openImage(block.src)"
          @keydown.enter="openImage(block.src)"
          @keydown.space.prevent="openImage(block.src)"
        />
        <p v-else class="image-missing">图片不可用{{ block.src ? `（${block.src.slice(0, 8)}）` : '' }}</p>
        <figcaption v-if="block.caption">{{ block.caption }}</figcaption>
      </figure>
    </section>
  </div>
</template>

<style scoped>
.presentation { display: grid; gap: 12px; }
.block {
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  min-width: 0;
}
.block p { white-space: pre-wrap; margin: 8px 0 0; }
dl { display: flex; flex-wrap: wrap; gap: 20px; margin: 8px 0 0; }
dt { font-size: 12px; color: var(--el-text-color-secondary); }
dd { margin: 4px 0 0; font-size: 18px; font-weight: 600; }
.table-wrap { overflow: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
caption { text-align: left; margin: 8px 0; }
th, td { text-align: left; border-bottom: 1px solid var(--el-border-color-lighter); padding: 8px; }
svg { display: block; width: 100%; color: var(--el-color-primary); }
.image-block { margin: 8px 0 0; min-width: 0; }
.image-block img { display: block; max-width: 100%; height: auto; border-radius: 6px; }
figcaption {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}
.image-missing { font-size: 12px; color: var(--el-text-color-secondary); }
details { font-size: 12px; overflow-wrap: anywhere; }
</style>
