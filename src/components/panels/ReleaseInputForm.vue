<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ReleaseInput } from '@/domain/types'
import { PARAM_TYPES, parameterEmpty } from '@/domain/parameters'
import ParamValueInput from './ParamValueInput.vue'

const props = defineProps<{
  /** 发布时冻结的启动输入，顺序即展示顺序 */
  inputs: ReleaseInput[]
  readonly?: boolean
}>()

const emit = defineEmits<{ (e: 'update:missing', value: string[]): void }>()

const values = ref<Record<string, unknown>>({})
const invalid = ref<Record<string, boolean>>({})
const formVersion = ref(0)

function keyOf(item: ReleaseInput): string {
  return item.name
}

function setValue(key: string, value: unknown): void {
  values.value = { ...values.value, [key]: value }
}

/** 发布时的设计默认值先填好，执行者只改需要改的 */
function seed(): void {
  formVersion.value += 1
  invalid.value = {}
  const next: Record<string, unknown> = {}
  for (const item of props.inputs) {
    if (item.default === undefined) continue
    next[keyOf(item)] = item.default
  }
  values.value = next
}

watch(() => props.inputs, seed, { immediate: true, deep: true })

const missingList = computed<string[]>(() => props.inputs
  .filter((item) => invalid.value[keyOf(item)]
    || (item.required && parameterEmpty(values.value[keyOf(item)], item.type)))
  .map((item) => item.name))

watch(missingList, (list) => emit('update:missing', list), { immediate: true })

/** 空值不提交，交给后端用发布默认值补齐 */
function build(): Record<string, unknown> {
  const global: Record<string, unknown> = {}
  for (const item of props.inputs) {
    const value = values.value[keyOf(item)]
    if (!parameterEmpty(value, item.type)) global[item.name] = value
  }
  return { global }
}

function typeLabel(item: ReleaseInput): string {
  return PARAM_TYPES.find((option) => option.value === item.type)?.label ?? '文本'
}

defineExpose({ missing: () => missingList.value, build, seed })
</script>

<template>
  <div class="release-inputs">
    <div v-if="inputs.length" class="group">
      <div class="group-title">启动参数</div>
      <div v-for="item in inputs" :key="`${formVersion}:${keyOf(item)}`" class="field">
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
          @validity="invalid[keyOf(item)] = !$event"
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
