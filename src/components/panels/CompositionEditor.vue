<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type {
  CompositionBlock,
  CompositionObject,
  CompositionOperation,
  CompositionResource,
  CompositionResult,
  FlowComposition,
} from '@/domain/types'
import type { Project, ProjectFunction } from '@/services/projects'
import { fetchProjectFunctions, listProjects } from '@/services/projects'
import CompositionArguments from '@/components/panels/CompositionArguments.vue'
import CompositionSteps from '@/components/panels/CompositionSteps.vue'

/**
 * 组合配置：声明工程对象、启动资源和节点操作。
 * 这里只编辑配置，不执行任何业务代码；真正的调用由运行期按配置发起。
 */
const props = defineProps<{
  composition: FlowComposition
  readonly?: boolean
}>()
const emit = defineEmits<{ (e: 'update', value: FlowComposition): void }>()

const projects = ref<Project[]>([])
const objectCatalog = reactive<Record<string, ProjectFunction[]>>({})
const sources = [
  { value: 'lit', label: '常量' },
  { value: 'input', label: '启动参数' },
  { value: 'system', label: '平台路径' },
  { value: 'items', label: '列表' },
  { value: 'fields', label: '字典' },
]

const objects = computed<CompositionObject[]>(() => props.composition.objects ?? [])
const resources = computed(() => props.composition.resources ?? [])
const operations = computed<[string, CompositionOperation][]>(
  () => Object.entries(props.composition.operations ?? {}),
)
const objectNames = computed(() => objects.value.map((item) => item.id).filter(Boolean))
const resourceNames = computed(() => resources.value.map((item) => item.id).filter(Boolean))
const startupObjectNames = computed(() => {
  const names: string[] = []
  for (const resource of resources.value) {
    if (resource.trigger !== 'start') continue
    for (const step of resource.steps ?? []) {
      if (step.saveAs) names.push(step.saveAs)
    }
  }
  return names
})

watch(
  () => objects.value.map((item) => `${item.project}|${item.module}`).join(','),
  () => {
    for (const item of objects.value) void ensureObjectCatalog(item.project, item.module)
  },
  { immediate: true },
)

onMounted(async () => {
  try {
    projects.value = await listProjects()
  } catch {
    projects.value = []
  }
})

async function ensureObjectCatalog(projectName: string, module: string): Promise<void> {
  const key = `${projectName}|${module}`
  if (!projectName || objectCatalog[key]) return
  const project = projects.value.find((item) => item.name === projectName)
  if (!project) return
  try {
    objectCatalog[key] = await fetchProjectFunctions(project.id, module, true)
  } catch {
    objectCatalog[key] = []
  }
}

function constructors(item: CompositionObject): ProjectFunction[] {
  return (objectCatalog[`${item.project}|${item.module}`] ?? [])
    .filter((entry) => entry.kind === 'constructor')
}

function update(next: Partial<FlowComposition>): void {
  emit('update', { ...props.composition, ...next })
}

// ---------------------------------------------------------------- 对象

function addObject(): void {
  const project = projects.value[0]
  let index = objects.value.length + 1
  const used = new Set(objectNames.value)
  while (used.has(`object${index}`)) index += 1
  update({ objects: [...objects.value, {
    id: `object${index}`,
    project: project?.name ?? '',
    module: project?.module ?? '',
    entry: '',
    args: {},
  }] })
}

function patchObject(index: number, changes: Partial<CompositionObject>): void {
  update({ objects: objects.value.map((item, i) => (i === index ? { ...item, ...changes } : item)) })
}

function removeObject(index: number): void {
  update({ objects: objects.value.filter((_, i) => i !== index) })
}

// ---------------------------------------------------------------- 资源

function addResource(): void {
  let index = resources.value.length + 1
  const used = new Set(resourceNames.value)
  while (used.has(`resource${index}`)) index += 1
  update({ resources: [...resources.value, {
    id: `resource${index}`, trigger: 'start', steps: [],
  }] })
}

function patchResource(index: number, changes: Partial<CompositionResource>): void {
  update({ resources: resources.value.map((item, i) => (i === index ? { ...item, ...changes } : item)) })
}

function removeResource(index: number): void {
  update({ resources: resources.value.filter((_, i) => i !== index) })
}

// ---------------------------------------------------------------- 操作

function addOperation(): void {
  let index = Object.keys(props.composition.operations ?? {}).length + 1
  const used = new Set(Object.keys(props.composition.operations ?? {}))
  while (used.has(`operation${index}`)) index += 1
  const id = `operation${index}`
  update({ operations: { ...(props.composition.operations ?? {}), [id]: { steps: [] } } })
}

function patchOperation(id: string, changes: Partial<CompositionOperation>): void {
  const all = { ...(props.composition.operations ?? {}) }
  all[id] = { ...all[id], ...changes }
  update({ operations: all })
}

function renameOperation(oldId: string, nextId: string): void {
  const id = nextId.trim()
  const all = props.composition.operations ?? {}
  if (!id || id === oldId || id in all) return
  const next: Record<string, CompositionOperation> = {}
  for (const [key, value] of Object.entries(all)) next[key === oldId ? id : key] = value
  update({ operations: next })
}

function removeOperation(id: string): void {
  const all = { ...(props.composition.operations ?? {}) }
  delete all[id]
  update({ operations: all })
}

function parametersText(operation: CompositionOperation): string {
  return (operation.parameters ?? []).join(', ')
}

function setParameters(id: string, text: string): void {
  const names = text.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean)
  patchOperation(id, { parameters: [...new Set(names)] })
}

function stepIds(operation: CompositionOperation): string[] {
  return (operation.steps ?? []).map((step) => step.id).filter(Boolean)
}

function enableResult(id: string, enabled: boolean): void {
  const operation = props.composition.operations?.[id]
  if (!operation) return
  if (!enabled) {
    const next = { ...operation }
    delete next.result
    patchOperation(id, next)
    return
  }
  const result: CompositionResult = {
    step: stepIds(operation)[0] ?? '', outcome: 'overall', reason: 'reasons',
  }
  patchOperation(id, { result })
}

function patchResult(id: string, changes: Partial<CompositionResult>): void {
  const operation = props.composition.operations?.[id]
  if (!operation?.result) return
  patchOperation(id, { result: { ...operation.result, ...changes } })
}

function addBlock(id: string): void {
  const operation = props.composition.operations?.[id]
  if (!operation) return
  const block: CompositionBlock = { type: 'image', step: stepIds(operation)[0] ?? '' }
  patchOperation(id, { presentation: [...(operation.presentation ?? []), block] })
}

function patchBlock(id: string, index: number, changes: Partial<CompositionBlock>): void {
  const operation = props.composition.operations?.[id]
  if (!operation) return
  const blocks = (operation.presentation ?? []).map((item, i) => (i === index ? { ...item, ...changes } : item))
  patchOperation(id, { presentation: blocks })
}

function removeBlock(id: string, index: number): void {
  const operation = props.composition.operations?.[id]
  if (!operation) return
  patchOperation(id, { presentation: (operation.presentation ?? []).filter((_, i) => i !== index) })
}

function columnsText(block: CompositionBlock): string {
  return (block.columns ?? []).join(', ')
}

function setColumns(id: string, index: number, text: string): void {
  const columns = text.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean)
  patchBlock(id, index, { columns })
}
</script>

<template>
  <div class="composition">
    <div class="section-title">工程对象</div>
    <div class="hint">对象的构造参数只支持启动参数、常量与平台分配的目录；对象留在所属工程进程内。</div>
    <div v-for="(item, index) in objects" :key="`object-${index}`" class="card">
      <div class="row">
        <el-input
          :model-value="item.id" :disabled="readonly" size="small" class="id"
          @change="patchObject(index, { id: String($event).trim() || item.id })"
        />
        <el-select
          :model-value="item.project" :disabled="readonly" size="small" filterable class="project"
          @update:model-value="patchObject(index, {
            project: String($event),
            module: projects.find((p) => p.name === String($event))?.module ?? item.module,
            entry: '',
          })"
        >
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.name" />
        </el-select>
        <el-input
          :model-value="item.module" :disabled="readonly" size="small" class="module"
          placeholder="模块"
          @update:model-value="patchObject(index, { module: String($event) })"
        />
        <el-select
          :model-value="item.entry" :disabled="readonly" size="small" filterable allow-create
          class="entry" placeholder="构造器"
          @update:model-value="patchObject(index, { entry: String($event) })"
          @visible-change="(open: boolean) => open && ensureObjectCatalog(item.project, item.module)"
        >
          <el-option v-for="fn in constructors(item)" :key="fn.name" :label="fn.name" :value="fn.name" />
        </el-select>
        <el-button v-if="!readonly" link size="small" type="danger" @click="removeObject(index)">
          删除
        </el-button>
      </div>
      <CompositionArguments
        :model-value="item.args ?? {}" :sources="sources" :readonly="readonly"
        @update:model-value="patchObject(index, { args: $event })"
      />
    </div>
    <el-button v-if="!readonly" size="small" plain @click="addObject">+ 对象</el-button>

    <div class="section-title">启动资源</div>
    <div class="hint">
      触发方式为「启动」的资源在开始节点执行一次；「按需」在第一次被引用时执行。资源结果按实例缓存。
    </div>
    <div v-for="(item, index) in resources" :key="`resource-${index}`" class="card">
      <div class="row">
        <el-input
          :model-value="item.id" :disabled="readonly" size="small" class="id"
          @change="patchResource(index, { id: String($event).trim() || item.id })"
        />
        <el-select
          :model-value="item.trigger" :disabled="readonly" size="small" class="trigger"
          @update:model-value="patchResource(index, { trigger: $event as 'start' | 'lazy' })"
        >
          <el-option label="启动时" value="start" />
          <el-option label="按需" value="lazy" />
        </el-select>
        <el-button v-if="!readonly" link size="small" type="danger" @click="removeResource(index)">
          删除资源
        </el-button>
      </div>
      <CompositionSteps
        :steps="item.steps ?? []" :projects="projects" :object-names="objectNames"
        :resource-names="resourceNames.slice(0, index)" scope="resource" :readonly="readonly"
        @update:steps="patchResource(index, { steps: $event })"
      />
    </div>
    <el-button v-if="!readonly" size="small" plain @click="addResource">+ 资源</el-button>

    <div class="section-title">节点操作</div>
    <div class="hint">
      操作是排查节点内部的有序调用；配置「判断结果」则由平台自动分支，不配置则取数绘图后交给人判断。
    </div>
    <div v-for="[id, operation] in operations" :key="`operation-${id}`" class="card">
      <div class="row">
        <el-input
          :model-value="id" :disabled="readonly" size="small" class="id"
          @change="renameOperation(id, String($event))"
        />
        <el-input
          :model-value="parametersText(operation)" :disabled="readonly" size="small" class="params"
          placeholder="操作参数，逗号分隔，如 metric"
          @change="setParameters(id, String($event))"
        />
        <el-button v-if="!readonly" link size="small" type="danger" @click="removeOperation(id)">
          删除
        </el-button>
      </div>

      <CompositionSteps
        :steps="operation.steps ?? []" :projects="projects"
        :object-names="[...objectNames, ...startupObjectNames]" :resource-names="resourceNames"
        scope="operation" :readonly="readonly"
        @update:steps="patchOperation(id, { steps: $event })"
      />

      <div class="result">
        <el-checkbox
          :model-value="!!operation.result" :disabled="readonly"
          @update:model-value="enableResult(id, Boolean($event))"
        >
          自动判断
        </el-checkbox>
        <template v-if="operation.result">
          <div class="row">
            <el-select
              :model-value="operation.result.step" :disabled="readonly" size="small" class="grow"
              @update:model-value="patchResult(id, { step: String($event) })"
            >
              <el-option v-for="name in stepIds(operation)" :key="name" :label="name" :value="name" />
            </el-select>
            <el-input
              :model-value="operation.result.outcome ?? ''" :disabled="readonly" size="small"
              placeholder="判断字段，如 overall"
              @update:model-value="patchResult(id, { outcome: String($event) })"
            />
            <el-input
              :model-value="operation.result.reason ?? ''" :disabled="readonly" size="small"
              placeholder="依据字段，如 reasons"
              @update:model-value="patchResult(id, { reason: String($event) })"
            />
          </div>
          <el-input
            :model-value="operation.result.normalReason ?? ''" :disabled="readonly" size="small"
            placeholder="正常时的依据模板（可空）"
            @update:model-value="patchResult(id, { normalReason: String($event) })"
          />
        </template>
      </div>

      <div class="presentation">
        <div class="sub-title">展示</div>
        <div v-for="(block, index) in operation.presentation ?? []" :key="`block-${index}`" class="row">
          <el-select
            :model-value="block.type" :disabled="readonly" size="small" class="block-type"
            @update:model-value="patchBlock(id, index, { type: $event as CompositionBlock['type'] })"
          >
            <el-option label="图片" value="image" />
            <el-option label="表格" value="table" />
            <el-option label="文本" value="text" />
            <el-option label="指标" value="metrics" />
            <el-option label="曲线" value="line" />
          </el-select>
          <el-select
            v-if="block.type !== 'text' || block.step" :model-value="block.step ?? ''" :disabled="readonly"
            size="small" class="grow" placeholder="来源步骤"
            @update:model-value="patchBlock(id, index, { step: String($event) || undefined })"
          >
            <el-option v-for="name in stepIds(operation)" :key="name" :label="name" :value="name" />
          </el-select>
          <el-input
            v-if="block.type === 'text'"
            :model-value="block.text ?? ''" :disabled="readonly" size="small" class="grow"
            placeholder="文本内容"
            @update:model-value="patchBlock(id, index, { text: String($event) })"
          />
          <el-input
            v-if="block.type === 'table' || block.type === 'metrics' || block.type === 'line'"
            :model-value="block.path ?? ''" :disabled="readonly" size="small" class="grow"
            placeholder="字段路径，可空"
            @update:model-value="patchBlock(id, index, { path: String($event) })"
          />
          <el-input
            v-if="block.type === 'table'"
            :model-value="columnsText(block)" :disabled="readonly" size="small" class="grow"
            placeholder="列，逗号分隔"
            @change="setColumns(id, index, String($event))"
          />
          <el-input
            v-if="block.type === 'image'"
            :model-value="block.caption ?? ''" :disabled="readonly" size="small" class="grow"
            placeholder="图注（可空）"
            @update:model-value="patchBlock(id, index, { caption: String($event) })"
          />
          <el-button v-if="!readonly" link size="small" type="danger" @click="removeBlock(id, index)">
            删除
          </el-button>
        </div>
        <el-button v-if="!readonly" link size="small" type="primary" @click="addBlock(id)">
          + 展示块
        </el-button>
      </div>
    </div>
    <el-button v-if="!readonly" size="small" plain @click="addOperation">+ 操作</el-button>
  </div>
</template>

<style scoped>
.composition { display: flex; flex-direction: column; gap: 8px; }
.section-title {
  font-size: 12px; font-weight: 600; margin-top: 12px; padding-top: 10px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.sub-title { font-size: 12px; color: var(--el-text-color-secondary); }
.hint { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.card {
  display: flex; flex-direction: column; gap: 6px; padding: 8px;
  border: 1px solid var(--el-border-color-lighter); border-radius: 6px;
}
.row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.id { width: 110px; flex: none; }
.project { width: 130px; flex: none; }
.module { width: 120px; flex: none; }
.entry { flex: 1; min-width: 120px; }
.trigger { width: 100px; flex: none; }
.params { flex: 1; min-width: 140px; }
.grow { flex: 1; min-width: 120px; }
.block-type { width: 90px; flex: none; }
.result { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
.presentation { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
</style>
