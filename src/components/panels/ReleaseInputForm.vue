<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ReleaseInput } from '@/domain/types'
import ParamValueInput from './ParamValueInput.vue'

const props = defineProps<{
  /** 发布时冻结的启动输入，顺序即展示顺序 */
  inputs: ReleaseInput[]
  readonly?: boolean
}>()

const emit = defineEmits<{ (e: 'update:missing', value: string[]): void }>()

/** 函数名缺失时归到同一组，保证同一个 key 的读写一致 */
const FALLBACK_FUNCTION = '未标注函数'

const values = ref<Record<string, unknown>>({})

function globalKey(name: string): string {
  return `g:${name}`
}

function functionKey(fnName: string, name: string): string {
  return `f:${fnName}:${name}`
}

function keyOf(item: ReleaseInput): string {
  return item.scope === 'global'
    ? globalKey(item.name)
    : functionKey(item.function || FALLBACK_FUNCTION, item.name)
}

const globalInputs = computed(() => props.inputs.filter((item) => item.scope === 'global'))

const groups = computed<{ name: string; items: ReleaseInput[] }[]>(() => {
  const map = new Map<string, ReleaseInput[]>()
  for (const item of props.inputs) {
    if (item.scope !== 'function') continue
    const name = item.function || FALLBACK_FUNCTION
    const list = map.get(name) ?? []
    list.push(item)
    map.set(name, list)
  }
  return [...map.entries()].map(([name, items]) => ({ name, items }))
})

function isEmpty(value: unknown): boolean {
  if (value === undefined || value === null) return true
  return typeof value === 'string' && value.trim() === ''
}

function setValue(key: string, value: unknown): void {
  values.value = { ...values.value, [key]: value }
}

/** 发布时的设计默认值先填好，执行者只改需要改的 */
function seed(): void {
  const next: Record<string, unknown> = {}
  for (const item of props.inputs) {
    if (item.default === undefined) continue
    next[keyOf(item)] = item.default
  }
  values.value = next
}

watch(() => props.inputs, seed, { immediate: true, deep: true })

/** 缺失的必填项，函数局部参数带上函数名 */
const missingList = computed<string[]>(() => {
  const result: string[] = []
  for (const item of globalInputs.value) {
    if (item.required && isEmpty(values.value[globalKey(item.name)])) result.push(item.name)
  }
  for (const group of groups.value) {
    for (const item of group.items) {
      if (item.required && isEmpty(values.value[functionKey(group.name, item.name)])) {
        result.push(`${group.name}.${item.name}`)
      }
    }
  }
  return result
})

watch(missingList, (list) => emit('update:missing', list), { immediate: true })

/** 空值不提交，交给后端用发布默认值补齐 */
function build(): Record<string, unknown> {
  const global: Record<string, unknown> = {}
  for (const item of globalInputs.value) {
    const value = values.value[globalKey(item.name)]
    if (!isEmpty(value)) global[item.name] = value
  }
  const scope: Record<string, Record<string, unknown>> = {}
  for (const group of groups.value) {
    const bucket: Record<string, unknown> = {}
    for (const item of group.items) {
      const value = values.value[functionKey(group.name, item.name)]
      if (!isEmpty(value)) bucket[item.name] = value
    }
    scope[group.name] = bucket
  }
  return { global, scope }
}

function typeLabel(item: ReleaseInput): string {
  if (item.type === 'number') return '数字'
  if (item.type === 'boolean') return '布尔'
  if (item.type === 'datetime') return '时间'
  return '文本'
}

defineExpose({ missing: () => missingList.value, build, seed })
</script>

<template>
  <div class="release-inputs">
    <div v-if="globalInputs.length" class="group">
      <div class="group-title">全局参数</div>
      <div v-for="item in globalInputs" :key="keyOf(item)" class="field">
        <div class="field-head">
          <span class="field-name">{{ item.name }}</span>
          <span v-if="item.required" class="required">必填</span>
          <span class="field-type">{{ typeLabel(item) }}</span>
        </div>
        <ParamValueInput
          :model-value="values[keyOf(item)]"
          :type="item.type"
          :readonly="readonly"
          placeholder="不填则用默认值"
          @update:model-value="setValue(keyOf(item), $event)"
        />
      </div>
    </div>

    <div v-for="group in groups" :key="group.name" class="group">
      <div class="group-title">
        函数局部参数
        <span class="group-fn">{{ group.name }}</span>
      </div>
      <div v-for="item in group.items" :key="keyOf(item)" class="field">
        <div class="field-head">
          <span class="field-name">{{ item.name }}</span>
          <span v-if="item.required" class="required">必填</span>
          <span class="field-type">{{ typeLabel(item) }}</span>
        </div>
        <ParamValueInput
          :model-value="values[keyOf(item)]"
          :type="item.type"
          :readonly="readonly"
          placeholder="不填则用默认值"
          @update:model-value="setValue(keyOf(item), $event)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.release-inputs {
  display: flex;
  flex-direction: column;
}

.group {
  margin-top: 14px;
}

.group:first-child {
  margin-top: 0;
}

.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.group-fn {
  font-weight: 400;
  color: var(--el-text-color-secondary);
  font-family: ui-monospace, Consolas, monospace;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 10px;
}

.field-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.field-name {
  font-weight: 500;
}

.required {
  padding: 0 4px;
  border-radius: 3px;
  font-size: 11px;
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.field-type {
  color: var(--el-text-color-placeholder);
}
</style>
