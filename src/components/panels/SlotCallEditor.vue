<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type { SlotCall, SlotView } from '@/domain/types'
import { listProjects, fetchProjectCatalog, type Project, type ProjectFunction } from '@/services/projects'
import { errorText } from '@/services/api'
import SlotArgumentEditor from '@/components/panels/SlotArgumentEditor.vue'

const props = defineProps<{
  title: string
  hint?: string
  value?: SlotCall
  objectNames?: string[]
  inputs?: string[]
  parameters?: string[]
  allowPrevious?: boolean
  construct?: boolean
  allowHuman?: boolean
  allowCache?: boolean
  showView?: boolean
  required?: boolean
  readonly?: boolean
}>()
const emit = defineEmits<{ (e: 'update', value: SlotCall | undefined): void }>()

const projects = ref<Project[]>([])
const catalogues = reactive<Record<string, ProjectFunction[]>>({})
const errors = reactive<Record<string, string>>({})
const loading = reactive<Record<string, boolean>>({})
const human = ref(Boolean(props.value?.human))
watch(() => props.value?.human, (value) => { human.value = Boolean(value) })

// 面板复用时 props 会换节点，但组件不会重新 mount；只靠 onMounted 会把上一个节点的
// 接口目录留在缓存里，新节点匹配不到接口，参数区就整块消失（重新打开面板又好了）。
async function ensureCatalog(): Promise<void> {
  const call = props.value
  if (!call?.project) return
  const name = `${call.project}:${call.module ?? ''}`
  if (catalogues[name] || loading[name]) return
  const project = projects.value.find((item) => item.name === call.project)
  if (!project) return
  await load(call)
}

watch(() => [props.value?.project, props.value?.module], () => { void ensureCatalog() })

onMounted(async () => {
  try {
    projects.value = await listProjects()
  } catch (error) {
    errors.projects = errorText(error, '读取工程失败')
    return
  }
  void ensureCatalog()
})

const useCache = computed(() => props.value?.useCache !== false)

// 只要槽位下还有字段，就当成可折叠的板块：已配置的默认展开，空槽位收起省空间
const open = ref(Boolean(props.value?.project || props.value?.human
  || (props.required && !props.value)))
watch(
  () => [props.value?.project, props.value?.human] as const,
  ([project, human]) => {
    if (project || human) open.value = true
    else if (!props.value) open.value = Boolean(props.required)
  },
)
const key = computed(() => `${props.value?.project ?? ''}:${props.value?.module ?? ''}`)
const selected = computed(() =>
  catalogues[key.value]?.find((item) => item.name === (props.value?.interface || props.value?.function)))

const choices = computed(() => {
  const items = catalogues[key.value] ?? []
  if (props.construct) return items.filter((item) => item.kind === 'constructor')
  if (props.value?.object) return items.filter((item) => item.kind === 'instance_method')
  return items.filter((item) => !item.kind || item.kind === 'function' || item.kind === 'static_method' || item.kind === 'class_method')
})

async function load(call: SlotCall): Promise<void> {
  const project = projects.value.find((item) => item.name === call.project)
  if (!project) return
  const name = `${call.project}:${call.module ?? ''}`
  loading[name] = true
  errors[name] = ''
  try {
    catalogues[name] = await fetchProjectCatalog(project.id, call.module || project.module)
  } catch (error) {
    errors[name] = errorText(error, '读取接口失败')
  } finally {
    loading[name] = false
  }
}

function patch(value: Partial<SlotCall>): void {
  const base: SlotCall = props.value ?? { project: '', interface: '', function: '', arguments: {} }
  emit('update', { ...base, ...value })
}

function projectChanged(name: string): void {
  const project = projects.value.find((item) => item.name === name)
  const call: SlotCall = {
    project: name, module: project?.module ?? '', interface: '', function: '', arguments: {},
    ...(props.showView ? { view: (props.value?.view ?? 'image') as SlotView } : {}),
  }
  emit('update', call)
  void load(call)
}

function choose(name: string): void {
  patch({ interface: name, function: name.split('.').at(-1) ?? name, arguments: {} })
}

function setArgument(name: string, value?: SlotCall['arguments'][string]): void {
  const argumentsMap = { ...(props.value?.arguments ?? {}) }
  if (value) argumentsMap[name] = value
  else delete argumentsMap[name]
  patch({ arguments: argumentsMap })
}

function toggleHuman(value: boolean): void {
  human.value = value
  // 人工判断是槽位的一种填法，不能把槽位本身删掉，否则校验会报「缺少判定槽」
  if (value) emit('update', { human: true, project: '', interface: '', function: '', arguments: {} })
  else emit('update', { project: '', interface: '', function: '', arguments: {} })
}

function toggleCache(value: boolean): void {
  const next: SlotCall = { ...(props.value ?? { project: '', interface: '', function: '', arguments: {} }) }
  if (value) delete next.useCache
  else next.useCache = false
  emit('update', next)
}

function clear(): void {
  human.value = false
  emit('update', undefined)
}
</script>

<template>
  <section class="slot">
    <header class="slot-head" @click="open = !open">
      <svg class="chevron" :class="{ open }" viewBox="0 0 14 14" width="12" height="12" aria-hidden="true">
        <path d="M4 5.5 7 8.5 10 5.5" fill="none" stroke="currentColor" stroke-width="1.4"
          stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <span class="name">{{ title }}</span>
      <el-tag v-if="required" size="small" type="info">必填</el-tag>
      <el-tag v-if="human" size="small" type="warning">人工判断</el-tag>
      <span v-else-if="value?.project" class="owner">{{ value.project }}<template v-if="value.module"> / {{ value.module }}</template></span>
      <span v-else class="owner muted">未配置</span>
      <el-button v-if="!readonly && value" link size="small" class="clear" @click.stop="clear">清空</el-button>
    </header>
    <div v-show="open" class="slot-body">
      <p v-if="hint" class="hint">{{ hint }}</p>
      <p v-if="errors.projects" class="error" role="alert">{{ errors.projects }}</p>

      <template v-if="allowHuman">
        <el-switch :model-value="human" :disabled="readonly" active-text="人工判断" inactive-text="方法判定"
          @change="toggleHuman(Boolean($event))" />
      </template>

      <template v-if="!human">
        <el-select :model-value="value?.project" :disabled="readonly" size="small" filterable placeholder="选择工程"
          @change="projectChanged(String($event))">
          <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.name" />
        </el-select>
        <div class="row">
          <el-input :model-value="value?.module" :disabled="readonly" size="small" placeholder="模块（默认用工程登记模块）"
            @change="patch({ module: String($event) })" />
          <el-button size="small" :loading="loading[key]" @click="value && load(value)">读取接口</el-button>
        </div>
        <p v-if="errors[key]" class="error" role="alert">{{ errors[key] }}</p>
        <p v-if="selected?.interfaceError" class="error" role="alert">{{ selected.interfaceError }}</p>

        <template v-if="construct">
          <label>对象名</label>
          <el-input :model-value="value?.object" :disabled="readonly" size="small" placeholder="例如 presenter"
            @change="patch({ object: String($event).trim() })" />
        </template>
        <template v-else>
          <label>目标对象</label>
          <el-select :model-value="value?.object ?? ''" :disabled="readonly" size="small"
            @change="patch({ object: String($event) || undefined, interface: '', function: '' })">
            <el-option label="模块级函数" value="" />
            <el-option v-for="name in objectNames ?? []" :key="name" :label="name" :value="name" />
          </el-select>
        </template>

        <label>接口</label>
        <el-select :model-value="value?.interface || value?.function" :disabled="readonly" size="small" filterable
          placeholder="选择公开接口" @change="choose(String($event))">
          <el-option v-for="item in choices" :key="item.name" :label="item.name" :value="item.name"
            :disabled="Boolean(item.interfaceError)" />
        </el-select>
        <p v-if="selected?.doc" class="hint">{{ selected.doc }}</p>
        <p v-if="value?.project && !selected && !loading[key] && catalogues[key]" class="hint">
          接口目录里找不到这个接口，重新选一次或点「读取接口」刷新目录。
        </p>
        <p v-if="value?.project && !catalogues[key] && !loading[key]" class="hint">
          接口目录还没加载，点「读取接口」后才会列出参数。
        </p>

        <div v-for="parameter in selected?.params ?? []" :key="parameter.name" class="parameter">
          <div class="row">
            <span class="parameter-name">{{ parameter.name }}</span>
            <span class="parameter-type">{{ parameter.required ? '必填' : '可选' }}</span>
            <el-checkbox :model-value="parameter.name in (value?.arguments ?? {})" :disabled="readonly"
              @change="setArgument(parameter.name, $event ? { lit: null } : undefined)">填写</el-checkbox>
          </div>
          <SlotArgumentEditor v-if="parameter.name in (value?.arguments ?? {})"
            :value="value?.arguments[parameter.name]" :inputs="inputs" :parameters="parameters"
            :previous="allowPrevious" :readonly="readonly"
            @update="setArgument(parameter.name, $event)" />
        </div>

        <el-checkbox v-if="allowCache" :model-value="useCache" :disabled="readonly"
          @change="toggleCache(Boolean($event))">
          使用缓存
        </el-checkbox>

        <template v-if="showView">
          <label>展示类型</label>
          <el-select :model-value="value?.view ?? 'image'" :disabled="readonly" size="small"
            @change="patch({ view: $event as SlotView })">
            <el-option label="图片" value="image" />
            <el-option label="曲线（数值数组）" value="line" />
            <el-option label="指标（label/value）" value="metrics" />
            <el-option label="表格（columns/rows）" value="table" />
            <el-option label="文字" value="text" />
          </el-select>
        </template>
      </template>
      <p v-else class="hint">执行到这个节点时由人看展示内容后选是／否，不需要判定方法。</p>
    </div>
  </section>
</template>

<style scoped>
.slot { display: grid; gap: 6px; padding: 6px 0; border-top: 1px solid var(--el-border-color-lighter); }
.slot:first-of-type { border-top: 0; }
.slot-head { display: flex; align-items: center; gap: 6px; min-height: 28px; cursor: pointer;
  user-select: none; }
.slot-head:hover .name { color: var(--el-color-primary); }
.chevron { flex: none; color: var(--el-text-color-tertiary); transition: transform 0.15s ease; }
.chevron.open { transform: rotate(180deg); }
.name { font-size: 12px; font-weight: 600; }
.owner { flex: 1; overflow: hidden; font-size: 12px; color: var(--el-text-color-secondary);
  white-space: nowrap; text-overflow: ellipsis; }
.owner.muted { color: var(--el-text-color-tertiary); }
.clear { flex: none; }
.slot-body { display: grid; gap: 6px; padding: 2px 0 4px 18px; }
label { font-size: 12px; color: var(--el-text-color-secondary); }
.hint { margin: 0; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.error { margin: 0; font-size: 12px; color: var(--el-color-danger); }
.row { display: flex; gap: 6px; align-items: center; }
.parameter { display: grid; gap: 4px; border-top: 1px dashed var(--el-border-color-lighter); padding-top: 6px; }
.parameter-name { flex: 1; font-size: 12px; }
.parameter-type { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
