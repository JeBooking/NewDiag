<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { NodeBinding } from '@/domain/types'
import { errorText } from '@/services/api'
import {
  fetchProjectFunctions,
  listProjects,
  type Project,
  type ProjectFunction,
} from '@/services/projects'

const props = defineProps<{
  binding?: NodeBinding
  readonly?: boolean
  plainOnly?: boolean
  diagnosisOnly?: boolean
}>()
const emit = defineEmits<{
  (e: 'update', value: NodeBinding | undefined): void
  (e: 'describe', value: ProjectFunction | null): void
}>()

const projects = ref<Project[]>([])
const functions = ref<ProjectFunction[]>([])
const loadingProjects = ref(false)
const loadingFunctions = ref(false)
let functionsRequest = 0
const loadError = ref<string | null>(null)
const configDraft = ref('{}')
const configError = ref('')
watch(() => props.binding?.config, (value) => {
  configDraft.value = JSON.stringify(value ?? {}, null, 2)
  configError.value = ''
}, { immediate: true })

// doc 里的 binding.project 存工程名：签名 key、导出代码、换机器导入都按名字走
const projectName = computed(() => props.binding?.project ?? '')
const functionName = computed(() => props.binding?.function ?? '')

const currentProject = computed(
  () => projects.value.find((item) => item.name === projectName.value) ?? null,
)

const selectedFunction = computed(
  () => functions.value.find((item) => item.name === functionName.value) ?? null,
)
const availableFunctions = computed(() => functions.value.filter((item) =>
  props.diagnosisOnly ? item.protocol === 'diagnosis/v1'
    : props.plainOnly ? !item.protocol : item.protocol !== 'diagnosis/v1',
))
watch(selectedFunction, (value) => emit('describe', value), { immediate: true })

watch(projectName, (name, previous) => {
  if (name === previous || !projects.value.length) return
  const found = projects.value.find((item) => item.name === name)
  void loadFunctions(found?.id ?? '')
})

function asText(value: unknown): string {
  return typeof value === 'string' ? value : String(value ?? '')
}

async function loadFunctions(projectId: string): Promise<void> {
  const request = ++functionsRequest
  functions.value = []
  if (!projectId) {
    loadingFunctions.value = false
    return
  }
  loadingFunctions.value = true
  try {
    const result = await fetchProjectFunctions(projectId)
    if (request === functionsRequest) functions.value = result
  } catch (error) {
    ElMessage.error(errorText(error, '读取函数列表失败'))
  } finally {
    if (request === functionsRequest) loadingFunctions.value = false
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
}

function onFunctionChange(value: unknown): void {
  const fn = asText(value)
  if (!fn) {
    emit('update', undefined)
    return
  }
  const selected = functions.value.find((item) => item.name === fn)
  const config = Object.fromEntries((selected?.configParams ?? [])
    .filter((param) => param.default !== undefined)
    .map((param) => [param.name, param.default]))
  emit('update', {
    project: projectName.value, function: fn,
    ...(selected?.protocol ? { protocol: selected.protocol, version: selected.version, config } : {}),
  })
}

function saveConfig(): void {
  try {
    const value: unknown = JSON.parse(configDraft.value)
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('配置必须为 JSON 对象')
    if (props.binding) emit('update', { ...props.binding, config: value as Record<string, unknown> })
    configError.value = ''
  } catch (error) {
    configError.value = error instanceof Error ? error.message : '配置格式错误'
  }
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
        :placeholder="diagnosisOnly ? '选择诊断类' : '选择函数'"
        :aria-label="diagnosisOnly ? '诊断业务类' : '业务函数'"
        :loading="loadingFunctions"
        filterable
        class="field"
        @change="onFunctionChange"
      >
        <el-option
          v-for="fn in availableFunctions"
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
    <template v-if="binding?.protocol">
      <div v-if="binding.protocol === 'investigation/v1'" class="hint">
        四阶段适配器 · {{ binding.version }}<br>
        采集：{{ selectedFunction?.automaticAcquisition ? '自动' : '人工' }}；
        判断：{{ selectedFunction?.automaticJudgment ? '自动' : '人工' }}
      </div>
      <div v-else class="hint">诊断实例 · {{ binding.version }}。启动时初始化，节点共用数据，人工等待后恢复。</div>
      <label for="adapter-config">初始化配置（JSON）</label>
      <div v-if="selectedFunction?.configParams?.length" class="hint">
        {{ selectedFunction.configParams.map((p) => `${p.name}${p.required ? '（必填）' : '（可选）'}`).join('、') }}
      </div>
      <el-input
        id="adapter-config" v-model="configDraft" type="textarea" :rows="5"
        :disabled="readonly" @blur="saveConfig" :aria-invalid="!!configError"
      />
      <div v-if="configError" class="hint error" role="alert">{{ configError }}</div>
      <div class="hint">配置随流程发布保存；凭证请填写环境变量名称，由业务代码读取。</div>
    </template>
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
