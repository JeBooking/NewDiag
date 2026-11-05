<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ManualField, NodeBinding, ParamType } from '@/domain/types'
import { PARAM_TYPES, parameterType } from '@/domain/parameters'
import { errorText } from '@/services/api'
import { fetchProjectFunctions, listProjects, type ProjectFunction } from '@/services/projects'

const props = defineProps<{ diagnosis: NodeBinding; value?: string; inputs?: ManualField[]; readonly?: boolean }>()
const emit = defineEmits<{
  (e: 'update', value: string | undefined): void
  (e: 'update-inputs', value: ManualField[]): void
}>()
const description = ref<ProjectFunction | null>(null)
const loading = ref(false)
const error = ref('')
let sequence = 0
const selected = computed(() => description.value?.methods?.find((item) => item.name === props.value))
const field = (name: string) => props.inputs?.find((item) => item.name === name)

function source(name: string, value: unknown): void {
  const next = (props.inputs ?? []).filter((item) => item.name !== name)
  const param = selected.value?.params.find((item) => item.name === name)
  if (value === 'manual') next.push({ name, type: parameterType(param?.annotation) })
  emit('update-inputs', next)
}

function inputType(name: string, value: unknown): void {
  emit('update-inputs', (props.inputs ?? []).map((item) => item.name === name
    ? { ...item, type: value as ParamType } : item))
}

watch(() => [props.diagnosis.project, props.diagnosis.function], async () => {
  const current = ++sequence
  loading.value = true
  error.value = ''
  description.value = null
  try {
    const projects = await listProjects()
    const project = projects.find((item) => item.name === props.diagnosis.project)
    if (!project) throw new Error('诊断工程尚未登记')
    const functions = await fetchProjectFunctions(project.id)
    if (current === sequence) {
      description.value = functions.find((item) => item.name === props.diagnosis.function) ?? null
      if (!description.value) error.value = '诊断类不存在，请检查流程属性'
    }
  } catch (failure) {
    if (current === sequence) error.value = errorText(failure, '读取诊断方法失败')
  } finally {
    if (current === sequence) loading.value = false
  }
}, { immediate: true })
</script>

<template>
  <div class="method-editor">
    <label for="diagnosis-method">排查方法</label>
    <el-select
      id="diagnosis-method" :model-value="value" :disabled="readonly" :loading="loading"
      placeholder="选择实例方法；留空为人工排查" clearable
      :aria-invalid="!!error || (!!value && !loading && !selected)"
      aria-describedby="diagnosis-method-hint"
      @update:model-value="emit('update', typeof $event === 'string' && $event ? $event : undefined)"
    >
      <el-option v-for="method in description?.methods ?? []" :key="method.name"
        :label="method.name" :value="method.name" />
    </el-select>
    <div id="diagnosis-method-hint" class="hint">
      {{ diagnosis.function }} 的实例方法直接使用共享数据。未找到来源的必填参数，执行到本节点时填写。
      <span v-if="selected?.params.length">参数：{{ selected.params.map((p) => p.name).join('、') }}</span>
    </div>
    <div v-for="param in selected?.params ?? []" :key="param.name" class="parameter">
      <label :for="`runtime-source-${param.name}`">{{ param.name }} · {{ param.annotation || '任意类型' }}</label>
      <el-select :id="`runtime-source-${param.name}`" :model-value="field(param.name) ? 'manual' : 'auto'"
        :disabled="readonly" @update:model-value="source(param.name, $event)">
        <el-option label="自动绑定；缺少必填值时现场填写" value="auto" />
        <el-option label="执行到此节点时填写" value="manual" />
      </el-select>
      <template v-if="field(param.name)">
        <label :for="`runtime-type-${param.name}`">{{ param.name }} 的输入类型</label>
        <el-select :id="`runtime-type-${param.name}`" :model-value="field(param.name)?.type || 'text'"
          :disabled="readonly" @update:model-value="inputType(param.name, $event)">
          <el-option v-for="option in PARAM_TYPES" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </template>
    </div>
    <div v-if="error || (value && !loading && !selected)" class="error" role="alert">
      {{ error || '该方法不在当前诊断类中，请重新选择' }}
    </div>
  </div>
</template>

<style scoped>
.method-editor { display: flex; flex-direction: column; gap: 8px; }
.parameter { display: grid; gap: 6px; padding-top: 8px; border-top: 1px solid var(--el-border-color-lighter); }
.hint { font-size: 12px; line-height: 1.6; color: var(--el-text-color-secondary); }
.error { font-size: 12px; color: var(--el-color-danger); }
</style>
