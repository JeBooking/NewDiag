<script setup lang="ts">
import { computed } from 'vue'
import type { FlowMethod, ManualField, ParamType } from '@/domain/types'
import { PARAM_TYPES } from '@/domain/parameters'

const props = defineProps<{
  methods: FlowMethod[]
  value?: string
  inputs?: ManualField[]
  readonly?: boolean
}>()
const emit = defineEmits<{
  (e: 'update', value: string | undefined): void
  (e: 'update-inputs', value: ManualField[]): void
}>()

const selected = computed(() => props.methods.find((item) => item.name === props.value))
const field = (name: string) => props.inputs?.find((item) => item.name === name)

function source(name: string, value: unknown): void {
  const next = (props.inputs ?? []).filter((item) => item.name !== name)
  if (value === 'manual') {
    const param = selected.value?.params?.find((item) => item.name === name)
    next.push({ name, type: (param?.type ?? 'text') as ParamType })
  }
  emit('update-inputs', next)
}

function inputType(name: string, value: unknown): void {
  emit('update-inputs', (props.inputs ?? []).map((item) => item.name === name
    ? { ...item, type: value as ParamType }
    : item))
}
</script>

<template>
  <div class="method-editor">
    <label for="flow-class-method">排查方法</label>
    <el-select
      id="flow-class-method" :model-value="value" :disabled="readonly"
      placeholder="选择流程类的方法；留空为人工排查" clearable
      @update:model-value="emit('update', typeof $event === 'string' && $event ? $event : undefined)"
    >
      <el-option v-for="method in methods" :key="method.name" :label="method.name" :value="method.name" />
    </el-select>
    <div class="hint">
      方法由流程类声明，节点只负责选方法和填参数。同构检查（振动/温度）可以共用一个方法。
      <span v-if="selected?.params?.length">参数：{{ selected.params.map((p) => p.name).join('、') }}</span>
    </div>
    <div v-for="param in selected?.params ?? []" :key="param.name" class="parameter">
      <label :for="`flow-param-${param.name}`">{{ param.name }} · {{ param.type || '任意类型' }}</label>
      <el-select :id="`flow-param-${param.name}`" :model-value="field(param.name) ? 'manual' : 'auto'"
        :disabled="readonly" @update:model-value="source(param.name, $event)">
        <el-option label="自动绑定；缺少必填值时现场填写" value="auto" />
        <el-option label="执行到此节点时填写" value="manual" />
      </el-select>
      <template v-if="field(param.name)">
        <label :for="`flow-param-type-${param.name}`">{{ param.name }} 的输入类型</label>
        <el-select :id="`flow-param-type-${param.name}`" :model-value="field(param.name)?.type || 'text'"
          :disabled="readonly" @update:model-value="inputType(param.name, $event)">
          <el-option v-for="option in PARAM_TYPES" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </template>
    </div>
    <div v-if="value && !selected" class="error" role="alert">该方法不在流程类里，请重新选择</div>
  </div>
</template>

<style scoped>
.method-editor { display: flex; flex-direction: column; gap: 8px; }
.parameter { display: grid; gap: 6px; padding-top: 8px; border-top: 1px solid var(--el-border-color-lighter); }
.hint { font-size: 12px; line-height: 1.6; color: var(--el-text-color-secondary); }
.error { font-size: 12px; color: var(--el-color-danger); }
</style>
