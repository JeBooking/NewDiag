<script setup lang="ts">
import { computed } from 'vue'
import type { ParamType } from '@/domain/types'

const props = defineProps<{
  modelValue?: unknown
  type?: ParamType
  placeholder?: string
  readonly?: boolean
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: unknown): void }>()

const kind = computed<ParamType>(() => props.type ?? 'text')

const textValue = computed(() =>
  props.modelValue === undefined || props.modelValue === null ? '' : String(props.modelValue),
)
const numberValue = computed(() =>
  typeof props.modelValue === 'number' && Number.isFinite(props.modelValue)
    ? props.modelValue
    : undefined,
)
const boolValue = computed(
  () => props.modelValue === true || props.modelValue === 'true' || props.modelValue === 1,
)

function toText(value: unknown): string {
  return typeof value === 'string' ? value : String(value ?? '')
}

function onText(value: unknown): void {
  const next = toText(value)
  emit('update:modelValue', next === '' ? undefined : next)
}

function onNumber(value: unknown): void {
  emit('update:modelValue', typeof value === 'number' && Number.isFinite(value) ? value : undefined)
}

function onBool(value: unknown): void {
  emit('update:modelValue', Boolean(value))
}

function onDate(value: unknown): void {
  const next = toText(value)
  emit('update:modelValue', next === '' ? undefined : next)
}
</script>

<template>
  <el-input
    v-if="kind === 'text'"
    :model-value="textValue"
    :placeholder="placeholder"
    :disabled="readonly"
    size="small"
    clearable
    @update:model-value="onText"
  />
  <el-input-number
    v-else-if="kind === 'number'"
    :model-value="numberValue"
    :placeholder="placeholder"
    :disabled="readonly"
    size="small"
    controls-position="right"
    class="number"
    @update:model-value="onNumber"
  />
  <el-switch
    v-else-if="kind === 'boolean'"
    :model-value="boolValue"
    :disabled="readonly"
    size="small"
    @update:model-value="onBool"
  />
  <el-date-picker
    v-else
    :model-value="textValue"
    type="datetime"
    value-format="YYYY-MM-DDTHH:mm:ss"
    :placeholder="placeholder || '选择时间'"
    :disabled="readonly"
    size="small"
    class="date"
    @update:model-value="onDate"
  />
</template>

<style scoped>
.number,
.date {
  width: 100%;
}
</style>
