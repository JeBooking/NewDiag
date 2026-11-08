<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import type { CompositionArgument, CompositionStep } from '@/domain/types'
import type { Project, ProjectFunction } from '@/services/projects'
import { fetchProjectFunctions } from '@/services/projects'
import CompositionArguments from '@/components/panels/CompositionArguments.vue'
import DraftInput from '@/components/panels/DraftInput.vue'

/**
 * 有序调用列表：一步 = 一次公开接口调用。
 * 接口下拉来自工程探测，允许手填；对象名只能引用已声明的对象或前面步骤 saveAs 的结果。
 */
const props = defineProps<{
  steps: CompositionStep[]
  projects: Project[]
  objectNames: string[]
  resourceNames: string[]
  scope: 'resource' | 'operation'
  readonly?: boolean
}>()
const emit = defineEmits<{ (e: 'update:steps', value: CompositionStep[]): void }>()

const catalog = reactive<Record<string, ProjectFunction[]>>({})
const loading = reactive<Record<string, boolean>>({})

const sources = computed(() => {
  const base = [
    { value: 'lit', label: '常量' },
    { value: 'input', label: '启动参数' },
  ]
  if (props.scope === 'operation') base.push({ value: 'param', label: '操作参数' })
  base.push(
    { value: 'step', label: '前面步骤' },
    { value: 'resource', label: '资源' },
    { value: 'object', label: '对象' },
    { value: 'system', label: '平台路径' },
    { value: 'items', label: '列表' },
    { value: 'fields', label: '字典' },
  )
  return base
})

watch(
  () => props.steps.map((step) => `${step.project}|${step.module}`).join(','),
  () => {
    for (const step of props.steps) void ensure(step.project, step.module)
  },
  { immediate: true },
)

async function ensure(projectName: string, module: string): Promise<void> {
  const key = `${projectName}|${module}`
  if (!projectName || catalog[key] || loading[key]) return
  const project = props.projects.find((item) => item.name === projectName)
  if (!project) return
  loading[key] = true
  try {
    catalog[key] = await fetchProjectFunctions(project.id, module, true)
  } catch {
    catalog[key] = []
  } finally {
    loading[key] = false
  }
}

function options(step: CompositionStep): ProjectFunction[] {
  const list = catalog[`${step.project}|${step.module}`] ?? []
  if (step.kind === 'constructor') return list.filter((item) => item.kind === 'constructor')
  if (step.kind === 'method') return list.filter((item) => item.kind === 'method')
  return list.filter((item) => item.kind !== 'constructor' && item.kind !== 'method')
}

function available(index: number): string[] {
  const names = [...props.objectNames]
  for (const step of props.steps.slice(0, index)) {
    if (step.saveAs) names.push(step.saveAs)
  }
  return names
}

function patch(index: number, changes: Partial<CompositionStep>): void {
  emit('update:steps', props.steps.map((step, i) => (i === index ? { ...step, ...changes } : step)))
}

function changeProject(index: number, name: string): void {
  const project = props.projects.find((item) => item.name === name)
  const step = props.steps[index]
  patch(index, {
    project: name,
    module: project?.module || step.module,
    function: '',
    target: undefined,
  })
}

function changeKind(index: number, kind: string): void {
  patch(index, { kind: kind as CompositionStep['kind'], function: '', target: undefined })
}

function uniqueId(prefix: string): string {
  const used = new Set(props.steps.map((step) => step.id))
  let index = props.steps.length + 1
  while (used.has(`${prefix}${index}`)) index += 1
  return `${prefix}${index}`
}

function add(): void {
  const project = props.projects[0]
  const id = uniqueId('step')
  emit('update:steps', [...props.steps, {
    id,
    project: project?.name ?? '',
    module: project?.module ?? '',
    kind: 'function',
    function: '',
    output: 'auto',
    args: {},
  }])
}

function remove(index: number): void {
  emit('update:steps', props.steps.filter((_, i) => i !== index))
}

function move(index: number, delta: number): void {
  const target = index + delta
  if (target < 0 || target >= props.steps.length) return
  const next = [...props.steps]
  ;[next[index], next[target]] = [next[target], next[index]]
  emit('update:steps', next)
}

function setArgs(index: number, value: Record<string, CompositionArgument>): void {
  patch(index, { args: value })
}
</script>

<template>
  <div class="steps">
    <div v-for="(step, index) in steps" :key="`${index}-${step.id}`" class="step">
      <div class="head">
        <span class="seq">{{ index + 1 }}</span>
        <DraftInput
          :model-value="step.id" :readonly="readonly" class="id" placeholder="步骤 id"
          @update:model-value="patch(index, { id: $event.trim() || step.id })"
        />
        <span class="summary">{{ step.function || '未选择接口' }}</span>
        <template v-if="!readonly">
          <el-button link size="small" :disabled="index === 0" @click="move(index, -1)">上移</el-button>
          <el-button link size="small" :disabled="index === steps.length - 1" @click="move(index, 1)">
            下移
          </el-button>
          <el-button link size="small" type="danger" @click="remove(index)">删除</el-button>
        </template>
      </div>

      <div class="grid">
        <label>工程</label>
        <el-select
          :model-value="step.project" :disabled="readonly" size="small" filterable
          @update:model-value="changeProject(index, String($event))"
        >
          <el-option v-for="item in projects" :key="item.id" :label="item.name" :value="item.name" />
        </el-select>

        <label>模块</label>
        <el-input
          :model-value="step.module" :disabled="readonly" size="small"
          placeholder="如 dataclient / dataset / plot_window"
          @update:model-value="patch(index, { module: String($event) })"
        />

        <label>调用类型</label>
        <el-select
          :model-value="step.kind" :disabled="readonly" size="small"
          @update:model-value="changeKind(index, String($event))"
        >
          <el-option label="普通函数" value="function" />
          <el-option label="构造器" value="constructor" />
          <el-option label="实例方法" value="method" />
        </el-select>

        <template v-if="step.kind === 'method'">
          <label>调用对象</label>
          <el-select
            :model-value="step.target ?? ''" :disabled="readonly" size="small" filterable
            placeholder="声明的对象或前面步骤 saveAs"
            @update:model-value="patch(index, { target: String($event) })"
          >
            <el-option v-for="name in available(index)" :key="name" :label="name" :value="name" />
          </el-select>
        </template>

        <label>接口</label>
        <el-select
          :model-value="step.function" :disabled="readonly" size="small" filterable allow-create
          default-first-option placeholder="从工程接口目录选择，或直接填写"
          @update:model-value="patch(index, { function: String($event) })"
          @visible-change="(open: boolean) => open && ensure(step.project, step.module)"
        >
          <el-option
            v-for="item in options(step)" :key="item.name"
            :label="item.name" :value="item.name"
          >
            <span>{{ item.name }}</span>
            <span v-if="item.doc" class="option-doc">{{ item.doc.slice(0, 60) }}</span>
          </el-option>
        </el-select>

        <label>输出</label>
        <el-select
          :model-value="step.output ?? 'auto'" :disabled="readonly" size="small"
          @update:model-value="patch(index, { output: $event as CompositionStep['output'] })"
        >
          <el-option label="自动识别" value="auto" />
          <el-option label="普通值" value="value" />
          <el-option label="对象（留在本工程）" value="object" />
          <el-option label="数据文件" value="file" />
          <el-option label="文件列表" value="files" />
          <el-option label="图片" value="image" />
        </el-select>

        <template v-if="(step.output ?? 'auto') === 'object' || (step.output ?? 'auto') === 'auto'">
          <label>命名对象</label>
          <el-input
            :model-value="step.saveAs ?? ''" :disabled="readonly" size="small"
            placeholder="可选：saveAs，后续步骤按名字调用它的方法"
            @update:model-value="patch(index, { saveAs: String($event) || undefined })"
          />
        </template>
      </div>

      <div class="args">
        <div class="args-title">参数</div>
        <CompositionArguments
          :model-value="step.args ?? {}" :sources="sources" :readonly="readonly"
          @update:model-value="setArgs(index, $event)"
        />
      </div>
    </div>

    <el-button v-if="!readonly" size="small" type="primary" plain @click="add">+ 添加步骤</el-button>
  </div>
</template>

<style scoped>
.steps { display: flex; flex-direction: column; gap: 10px; }
.step { border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 8px; }
.head { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.seq {
  width: 18px; height: 18px; border-radius: 9px; background: var(--el-fill-color);
  font-size: 11px; display: inline-flex; align-items: center; justify-content: center; flex: none;
}
.id { width: 110px; flex: none; }
.summary {
  font-size: 12px; color: var(--el-text-color-secondary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
}
.grid {
  display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 6px 8px; align-items: center;
}
.grid label { font-size: 12px; color: var(--el-text-color-secondary); }
.args { margin-top: 8px; }
.args-title { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.option-doc { margin-left: 8px; font-size: 11px; color: var(--el-text-color-secondary); }
</style>
