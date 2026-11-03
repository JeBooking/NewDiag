<script setup lang="ts">
import { computed } from 'vue'

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
details { font-size: 12px; overflow-wrap: anywhere; }
</style>
