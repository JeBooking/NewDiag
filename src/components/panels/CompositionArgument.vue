<script setup lang="ts">
import { computed } from 'vue'
import type { CompositionArgument } from '@/domain/types'
import DraftInput from '@/components/panels/DraftInput.vue'

/**
 * 一个参数来源的可视化编辑：先选来源，再填细节。
 * 嵌套的 items/fields 用 JSON 文本编辑——它们通常是常量结构，表单化收益不大。
 */
const props = defineProps<{
  modelValue: CompositionArgument
  sources: Array<{ value: string; label: string }>
  readonly?: boolean
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: CompositionArgument): void }>()

const KEYS = ['lit', 'input', 'param', 'step', 'resource', 'object', 'system', 'items', 'fields'] as const
type Key = (typeof KEYS)[number]

const source = computed<Key>(() => KEYS.find((key) => key in props.modelValue) ?? 'lit')
const value = computed(() => props.modelValue[source.value])

const literalText = computed(() =>
  typeof value.value === 'string' ? value.value : JSON.stringify(value.value ?? null),
)
const nestedText = computed(() => JSON.stringify(value.value ?? (source.value === 'items' ? [] : {}), null, 2))

function changeSource(next: string): void {
  const key = next as Key
  if (key === source.value) return
  emit('update:modelValue', blank(key))
}

function blank(key: Key): CompositionArgument {
  if (key === 'items') return { items: [] }
  if (key === 'fields') return { fields: {} }
  if (key === 'system') return { system: 'directory' }
  return { [key]: '' } as CompositionArgument
}

function patch(changes: Partial<CompositionArgument>): void {
  emit('update:modelValue', { ...props.modelValue, ...changes })
}

function replace(key: Key, next: unknown): void {
  emit('update:modelValue', { [key]: next } as CompositionArgument)
}

function onLiteral(text: string): void {
  const trimmed = text.trim()
  if (!trimmed) return replace('lit', '')
  try {
    replace('lit', JSON.parse(trimmed))
  } catch {
    replace('lit', text)
  }
}

function onNested(text: string): void {
  try {
    replace(source.value, JSON.parse(text))
  } catch {
    // 解析失败时不写回，避免把用户正在输入的半截 JSON 变成脏数据
  }
}
</script>

<template>
  <div class="argument">
    <el-select
      :model-value="source" :disabled="readonly" class="source" size="small"
      @update:model-value="changeSource"
    >
      <el-option v-for="item in sources" :key="item.value" :label="item.label" :value="item.value" />
    </el-select>

    <template v-if="source === 'lit'">
      <DraftInput
        :model-value="literalText" :readonly="readonly" class="detail"
        placeholder="常量；数字、true、[..]、{..} 按 JSON 解析"
        @update:model-value="onLiteral"
      />
    </template>
    <template v-else-if="source === 'system'">
      <el-select
        :model-value="modelValue.system ?? 'directory'" :disabled="readonly" size="small" class="source"
        @update:model-value="replace('system', $event)"
      >
        <el-option label="输出目录" value="directory" />
        <el-option label="输出文件" value="file" />
      </el-select>
      <el-input
        v-if="modelValue.system === 'file'" :model-value="modelValue.name ?? ''" :disabled="readonly"
        size="small" class="detail" placeholder="文件名，如 chart.png"
        @update:model-value="patch({ name: String($event) })"
      />
    </template>
    <template v-else-if="source === 'items' || source === 'fields'">
      <DraftInput
        :model-value="nestedText" :readonly="readonly" type="textarea" :rows="2"
        class="detail" placeholder="JSON 结构，元素可以继续写来源表达式"
        :revert="false"
        @update:model-value="onNested"
      />
    </template>
    <template v-else-if="source === 'step' || source === 'resource'">
      <el-input
        :model-value="String(value ?? '')" :disabled="readonly" size="small" class="name"
        :placeholder="source === 'step' ? '步骤 id' : '资源 id'"
        @update:model-value="replace(source, String($event))"
      />
      <el-input
        :model-value="modelValue.path ?? ''" :disabled="readonly" size="small" class="detail"
        placeholder="字段路径，可空"
        @update:model-value="patch({ path: String($event) })"
      />
    </template>
    <template v-else>
      <el-input
        :model-value="String(value ?? '')" :disabled="readonly" size="small" class="detail"
        :placeholder="source === 'input' ? '启动参数名' : source === 'param' ? '操作参数名' : '对象 id'"
        @update:model-value="replace(source, String($event))"
      />
    </template>
  </div>
</template>

<style scoped>
.argument { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.source { width: 96px; flex: none; }
.name { width: 120px; flex: none; }
.detail { flex: 1; min-width: 140px; }
</style>
