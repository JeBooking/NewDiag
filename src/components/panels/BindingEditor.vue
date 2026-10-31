<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { NodeBinding } from '@/domain/types'
import { errorText } from '@/services/api'
import {
  fetchProjectFunctions,
  listProjects,
  type Project,
  type ProjectFunction,
} from '@/services/projects'

const props = defineProps<{ binding?: NodeBinding; readonly?: boolean }>()
const emit = defineEmits<{ (e: 'update', value: NodeBinding | undefined): void }>()

const projects = ref<Project[]>([])
const functions = ref<ProjectFunction[]>([])
const loadingProjects = ref(false)
const loadingFunctions = ref(false)
const loadError = ref<string | null>(null)

// doc 里的 binding.project 存工程名：签名 key、导出代码、换机器导入都按名字走
const projectName = computed(() => props.binding?.project ?? '')
const functionName = computed(() => props.binding?.function ?? '')

const currentProject = computed(
  () => projects.value.find((item) => item.name === projectName.value) ?? null,
)

const selectedFunction = computed(
  () => functions.value.find((item) => item.name === functionName.value) ?? null,
)

function asText(value: unknown): string {
  return typeof value === 'string' ? value : String(value ?? '')
}

async function loadFunctions(projectId: string): Promise<void> {
  functions.value = []
  if (!projectId) return
  loadingFunctions.value = true
  try {
    functions.value = await fetchProjectFunctions(projectId)
  } catch (error) {
    ElMessage.error(errorText(error, '读取函数列表失败'))
  } finally {
    loadingFunctions.value = false
  }
}

onMounted(async () => {
  loadingProjects.value = true
  try {
    projects.value = await listProjects()
    const found = projects.value.find((item) => item.name === props.binding?.project)
    if (found) await loadFunctions(found.id)
  } catch (error) {
    loadError.value = errorText(error, '读取工程列表失败')
  } finally {
    loadingProjects.value = false
  }
})

function onProjectChange(value: unknown): void {
  const name = asText(value)
  emit('update', name ? { project: name, function: '' } : undefined)
  const found = projects.value.find((item) => item.name === name)
  void loadFunctions(found?.id ?? '')
}

function onFunctionChange(value: unknown): void {
  const fn = asText(value)
  if (!fn) {
    emit('update', undefined)
    return
  }
  emit('update', { project: projectName.value, function: fn })
}

function clear(): void {
  functions.value = []
  emit('update', undefined)
}

function paramText(fn: ProjectFunction): string {
  const params = fn.params.map((param) => {
    const suffix = param.default !== null && param.default !== undefined ? `=${param.default}` : ''
    const annotation = param.annotation ? `: ${param.annotation}` : ''
    return `${param.name}${annotation}${suffix}`
  })
  const returns = fn.returns ? ` -> ${fn.returns}` : ''
  return `(${params.join(', ')})${returns}`
}
</script>

<template>
  <div class="binding-editor">
    <div class="row">
      <el-select
        :model-value="projectName"
        :disabled="readonly"
        placeholder="选择工程"
        :loading="loadingProjects"
        filterable
        class="field"
        @change="onProjectChange"
      >
        <el-option
          v-for="project in projects"
          :key="project.id"
          :label="project.name"
          :value="project.name"
        />
      </el-select>
    </div>
    <div class="row">
      <el-select
        :model-value="functionName"
        :disabled="readonly || !currentProject"
        placeholder="选择函数"
        :loading="loadingFunctions"
        filterable
        class="field"
        @change="onFunctionChange"
      >
        <el-option
          v-for="fn in functions"
          :key="fn.name"
          :label="fn.name"
          :value="fn.name"
        />
      </el-select>
      <el-button v-if="binding && !readonly" text type="danger" @click="clear">清除</el-button>
    </div>
    <div v-if="loadError" class="hint error">{{ loadError }}</div>
    <div v-else-if="!projects.length" class="hint">尚未登记工程，请先到「工程」页添加</div>
    <div v-else-if="selectedFunction" class="signature">
      {{ selectedFunction.name }}{{ paramText(selectedFunction) }}
    </div>
    <div v-else-if="currentProject && !loadingFunctions && !functions.length" class="hint">
      该工程没有可用函数
    </div>
  </div>
</template>

<style scoped>
.binding-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.field {
  flex: 1;
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.hint.error {
  color: var(--el-color-danger);
}

.signature {
  font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace;
  font-size: 11px;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  padding: 6px 8px;
  border-radius: 4px;
  word-break: break-all;
  line-height: 1.6;
}
</style>
