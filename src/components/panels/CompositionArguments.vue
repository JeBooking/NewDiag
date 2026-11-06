<script setup lang="ts">
import { computed } from 'vue'
import type { CompositionArgument } from '@/domain/types'
import CompositionArgumentEditor from '@/components/panels/CompositionArgument.vue'

/** 参数名 → 来源 的表格；参数名用失焦提交，避免每敲一个字母就重建整张表 */
const props = defineProps<{
  modelValue: Record<string, CompositionArgument>
  sources: Array<{ value: string; label: string }>
  readonly?: boolean
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: Record<string, CompositionArgument>): void }>()

const rows = computed(() => Object.entries(props.modelValue ?? {}))

function set(name: string, value: CompositionArgument): void {
  emit('update:modelValue', { ...props.modelValue, [name]: value })
}

function rename(oldName: string, nextName: string): void {
  const name = nextName.trim()
  if (!name || name === oldName || name in props.modelValue) return
  const next: Record<string, CompositionArgument> = {}
  for (const [key, value] of Object.entries(props.modelValue)) {
    next[key === oldName ? name : key] = value
  }
  emit('update:modelValue', next)
}

function remove(name: string): void {
  const next = { ...props.modelValue }
  delete next[name]
  emit('update:modelValue', next)
}

function add(): void {
  let index = Object.keys(props.modelValue).length + 1
  while (`arg${index}` in props.modelValue) index += 1
  emit('update:modelValue', { ...props.modelValue, [`arg${index}`]: { lit: '' } })
}
</script>

<template>
  <div class="arguments">
    <div v-for="[name, value] in rows" :key="name" class="row">
      <el-input
        :model-value="name" :disabled="readonly" size="small" class="name"
        @change="rename(name, String($event))"
      />
      <CompositionArgumentEditor
        :model-value="value" :sources="sources" :readonly="readonly"
        @update:model-value="set(name, $event)"
      />
      <el-button v-if="!readonly" link size="small" type="danger" @click="remove(name)">删除</el-button>
    </div>
    <el-button v-if="!readonly" link size="small" type="primary" @click="add">+ 参数</el-button>
  </div>
</template>

<style scoped>
.arguments { display: flex; flex-direction: column; gap: 6px; }
.row { display: flex; gap: 6px; align-items: center; }
.name { width: 110px; flex: none; }
</style>
