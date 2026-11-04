<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { NodeBinding } from '@/domain/types'
import { errorText } from '@/services/api'
import { fetchProjectFunctions, listProjects, type ProjectFunction } from '@/services/projects'

const props = defineProps<{ diagnosis: NodeBinding; value?: string; readonly?: boolean }>()
const emit = defineEmits<{ (e: 'update', value: string | undefined): void }>()
const description = ref<ProjectFunction | null>(null)
const loading = ref(false)
const error = ref('')
let sequence = 0
const selected = computed(() => description.value?.methods?.find((item) => item.name === props.value))

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
      {{ diagnosis.function }} 的实例方法直接使用共享数据。需要的额外参数按开始节点输入自动绑定。
      <span v-if="selected?.params.length">参数：{{ selected.params.map((p) => p.name).join('、') }}</span>
    </div>
    <div v-if="error || (value && !loading && !selected)" class="error" role="alert">
      {{ error || '该方法不在当前诊断类中，请重新选择' }}
    </div>
  </div>
</template>

<style scoped>
.method-editor { display: flex; flex-direction: column; gap: 8px; }
.hint { font-size: 12px; line-height: 1.6; color: var(--el-text-color-secondary); }
.error { font-size: 12px; color: var(--el-color-danger); }
</style>
