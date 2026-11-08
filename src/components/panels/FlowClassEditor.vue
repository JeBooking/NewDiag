<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { FlowClass, FlowImport, FlowMethod, FlowParam } from '@/domain/types'
import { listProjects } from '@/services/projects'

const props = defineProps<{ value?: FlowClass; readonly?: boolean }>()
const emit = defineEmits<{ (e: 'update', value: FlowClass | undefined): void }>()

const projects = ref<{ id: string; name: string }[]>([])
const drafts = ref<Record<string, string>>({})

onMounted(async () => {
  try {
    projects.value = await listProjects()
  } catch {
    projects.value = []
  }
})

const enabled = computed(() => !!props.value)
const value = computed<FlowClass>(() => props.value ?? {
  runtime: '', imports: [], initialize: [], methods: [],
})

watch(() => props.value, () => {
  drafts.value = {}
})

function patch(next: Partial<FlowClass>): void {
  emit('update', { ...value.value, ...next })
}

function enable(): void {
  emit('update', {
    runtime: '',
    imports: [],
    initialize: [],
    methods: [{ name: 'check', params: [], body: [], returns: { result: '', reason: '' } }],
  })
}

function disable(): void {
  emit('update', undefined)
}

function editImport(index: number, key: keyof FlowImport, next: string): void {
  const imports = value.value.imports.map((item, position) =>
    position === index ? { ...item, [key]: next } : item)
  patch({ imports })
}

function addImport(): void {
  patch({ imports: [...value.value.imports, { alias: '', project: '', module: '' }] })
}

function removeImport(index: number): void {
  patch({ imports: value.value.imports.filter((_, position) => position !== index) })
}

function editStep(list: 'initialize', index: number, key: 'name' | 'call', next: string): void {
  const steps = value.value[list].map((item, position) =>
    position === index ? { ...item, [key]: next } : item)
  patch({ [list]: steps } as Partial<FlowClass>)
}

function stepArgs(list: 'initialize', index: number): string {
  const key = `${list}-${index}`
  if (drafts.value[key] !== undefined) return drafts.value[key]
  const step = value.value[list][index]
  return JSON.stringify(step?.args ?? {}, null, 0)
}

function writeStepArgs(list: 'initialize', index: number, text: string): void {
  drafts.value = { ...drafts.value, [`${list}-${index}`]: text }
  try {
    const parsed = JSON.parse(text || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return
    const steps = value.value[list].map((item, position) =>
      position === index ? { ...item, args: parsed } : item)
    patch({ [list]: steps } as Partial<FlowClass>)
  } catch {
    // 半截 JSON 不写回 doc，等下一次失焦再试
  }
}

function addStep(): void {
  patch({ initialize: [...value.value.initialize, { name: '', call: '', args: {} }] })
}

function removeStep(index: number): void {
  patch({ initialize: value.value.initialize.filter((_, position) => position !== index) })
}

function addMethod(): void {
  patch({
    methods: [...value.value.methods, {
      name: '', params: [], body: [], returns: { result: '', reason: '' },
    }],
  })
}

function removeMethod(index: number): void {
  patch({ methods: value.value.methods.filter((_, position) => position !== index) })
}

function editMethod(index: number, next: Partial<FlowMethod>): void {
  patch({
    methods: value.value.methods.map((item, position) =>
      position === index ? { ...item, ...next } : item),
  })
}

function addParam(method: number): void {
  const item = value.value.methods[method]
  editMethod(method, { params: [...(item.params ?? []), { name: '', type: 'text' }] })
}

function editParam(method: number, index: number, next: Partial<FlowParam>): void {
  const item = value.value.methods[method]
  editMethod(method, {
    params: (item.params ?? []).map((param, position) =>
      position === index ? { ...param, ...next } : param),
  })
}

function removeParam(method: number, index: number): void {
  const item = value.value.methods[method]
  editMethod(method, { params: (item.params ?? []).filter((_, position) => position !== index) })
}

function addBody(method: number): void {
  const item = value.value.methods[method]
  editMethod(method, { body: [...item.body, { name: '', call: '', args: {} }] })
}

function editBody(method: number, index: number, key: 'name' | 'call', next: string): void {
  const item = value.value.methods[method]
  editMethod(method, {
    body: item.body.map((step, position) =>
      position === index ? { ...step, [key]: next } : step),
  })
}

function removeBody(method: number, index: number): void {
  const item = value.value.methods[method]
  editMethod(method, { body: item.body.filter((_, position) => position !== index) })
}

function bodyArgs(method: number, index: number): string {
  const key = `body-${method}-${index}`
  if (drafts.value[key] !== undefined) return drafts.value[key]
  return JSON.stringify(value.value.methods[method]?.body[index]?.args ?? {}, null, 0)
}

function writeBodyArgs(method: number, index: number, text: string): void {
  drafts.value = { ...drafts.value, [`body-${method}-${index}`]: text }
  try {
    const parsed = JSON.parse(text || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return
    const item = value.value.methods[method]
    editMethod(method, {
      body: item.body.map((step, position) =>
        position === index ? { ...step, args: parsed } : step),
    })
  } catch {
    // 同上：写一半的 JSON 先留在本地草稿
  }
}

function returnsOf(method: number): NonNullable<FlowMethod['returns']> {
  return value.value.methods[method]?.returns ?? {}
}

function editReturns(method: number, key: string, next: string): void {
  editMethod(method, { returns: { ...returnsOf(method), [key]: next } })
}

function defaultValue(param: FlowParam): string {
  return param.default === undefined ? '' : JSON.stringify(param.default)
}

function writeDefault(method: number, index: number, text: string): void {
  const param = value.value.methods[method]?.params?.[index]
  if (!param) return
  if (!text.trim()) {
    const rest = { ...param }
    delete rest.default
    editParam(method, index, rest)
    return
  }
  try {
    editParam(method, index, { default: JSON.parse(text) })
  } catch {
    editParam(method, index, { default: text })
  }
}
</script>

<template>
  <div class="flow-class">
    <div v-if="!enabled" class="empty">
      <div class="hint">把流程声明成一个类：导入已登记的工程，构造一次，再由排查节点调用方法。声明后不用写 Python。</div>
      <el-button v-if="!readonly" size="small" @click="enable">声明流程类</el-button>
    </div>

    <template v-else>
      <label class="label">运行解释器</label>
      <el-input
        :model-value="value.runtime" :disabled="readonly" placeholder="留空用平台解释器"
        @update:model-value="patch({ runtime: String($event) })"
      />
      <div class="hint">类会被导入这个解释器。三个工程里有绘图依赖时填那个工程的 venv；发布时导入失败会直接拦下。</div>

      <label class="label">工程导入</label>
      <div v-for="(item, index) in value.imports" :key="`import-${index}`" class="card">
        <div class="row two">
          <el-input :model-value="item.alias" :disabled="readonly" placeholder="别名"
            @update:model-value="editImport(index, 'alias', String($event))" />
          <el-input :model-value="item.module" :disabled="readonly" placeholder="模块"
            @update:model-value="editImport(index, 'module', String($event))" />
        </div>
        <div class="row two">
          <el-select :model-value="item.project" :disabled="readonly" placeholder="工程" filterable allow-create
            @update:model-value="editImport(index, 'project', String($event))">
            <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.name" />
          </el-select>
          <el-button v-if="!readonly" text type="danger" @click="removeImport(index)">删除</el-button>
        </div>
      </div>
      <el-button v-if="!readonly" size="small" @click="addImport">+ 导入</el-button>

      <label class="label">构造步骤（只执行一次）</label>
      <div v-for="(item, index) in value.initialize" :key="`init-${index}`" class="card">
        <div class="row two">
          <el-input :model-value="item.name" :disabled="readonly" placeholder="存为"
            @update:model-value="editStep('initialize', index, 'name', String($event))" />
          <el-button v-if="!readonly" text type="danger" @click="removeStep(index)">删除</el-button>
        </div>
        <el-input :model-value="item.call" :disabled="readonly" placeholder="别名.类或方法"
          @update:model-value="editStep('initialize', index, 'call', String($event))" />
        <el-input type="textarea" :rows="2" :model-value="stepArgs('initialize', index)"
          :disabled="readonly" placeholder='参数，如 {"path": {"var": "data_file"}}'
          @update:model-value="writeStepArgs('initialize', index, String($event))" />
      </div>
      <el-button v-if="!readonly" size="small" @click="addStep">+ 构造步骤</el-button>

      <label class="label">方法</label>
      <div v-for="(method, index) in value.methods" :key="`method-${index}`" class="method">
        <div class="row two">
          <el-input :model-value="method.name" :disabled="readonly" placeholder="方法名"
            @update:model-value="editMethod(index, { name: String($event) })" />
          <el-button v-if="!readonly" text type="danger" @click="removeMethod(index)">删除</el-button>
        </div>

        <div class="sub">参数</div>
        <div v-for="(param, position) in method.params ?? []" :key="`param-${position}`" class="card">
          <div class="row two">
            <el-input :model-value="param.name" :disabled="readonly" placeholder="名字"
              @update:model-value="editParam(index, position, { name: String($event) })" />
            <el-select :model-value="param.type ?? 'text'" :disabled="readonly"
              @update:model-value="editParam(index, position, { type: $event as FlowParam['type'] })">
              <el-option label="文本" value="text" />
              <el-option label="数字" value="number" />
              <el-option label="布尔" value="boolean" />
              <el-option label="JSON" value="json" />
            </el-select>
          </div>
          <div class="row two">
            <el-input :model-value="defaultValue(param)" :disabled="readonly" placeholder="默认值（可空）"
              @update:model-value="writeDefault(index, position, String($event))" />
            <el-button v-if="!readonly" text type="danger" @click="removeParam(index, position)">删除</el-button>
          </div>
        </div>
        <el-button v-if="!readonly" size="small" @click="addParam(index)">+ 参数</el-button>

        <div class="sub">方法体</div>
        <div v-for="(step, position) in method.body" :key="`body-${position}`" class="card">
          <div class="row two">
            <el-input :model-value="step.name" :disabled="readonly" placeholder="存为（可空）"
              @update:model-value="editBody(index, position, 'name', String($event))" />
            <el-button v-if="!readonly" text type="danger" @click="removeBody(index, position)">删除</el-button>
          </div>
          <el-input :model-value="step.call" :disabled="readonly" placeholder="对象.方法"
            @update:model-value="editBody(index, position, 'call', String($event))" />
          <el-input type="textarea" :rows="2" :model-value="bodyArgs(index, position)"
            :disabled="readonly" placeholder='参数，如 {"metric": {"param": "metric"}}'
            @update:model-value="writeBodyArgs(index, position, String($event))" />
        </div>
        <el-button v-if="!readonly" size="small" @click="addBody(index)">+ 步骤</el-button>

        <div class="sub">返回</div>
        <el-input :model-value="returnsOf(index).result ?? ''" :disabled="readonly"
          placeholder="result：字段路径，留空转人工"
          @update:model-value="editReturns(index, 'result', String($event))" />
        <el-input :model-value="returnsOf(index).reason ?? ''" :disabled="readonly" placeholder="reason：依据字段"
          @update:model-value="editReturns(index, 'reason', String($event))" />
        <el-input :model-value="returnsOf(index).figure ?? ''" :disabled="readonly" placeholder="figure：图片变量"
          @update:model-value="editReturns(index, 'figure', String($event))" />
        <el-input :model-value="returnsOf(index).series ?? ''" :disabled="readonly" placeholder="series：曲线变量"
          @update:model-value="editReturns(index, 'series', String($event))" />
        <div class="hint">result 写成 summary.overall 这样的字段路径；留空表示算完停下让人判断。</div>
      </div>
      <el-button v-if="!readonly" size="small" @click="addMethod">+ 方法</el-button>

      <el-button v-if="!readonly" class="remove" text type="danger" @click="disable">移除流程类</el-button>
    </template>
  </div>
</template>

<style scoped>
.flow-class { display: flex; flex-direction: column; gap: 8px; }
.empty { display: flex; flex-direction: column; gap: 8px; }
.row { display: grid; gap: 6px; align-items: center; }
.row.two { grid-template-columns: 1fr 1fr; }
.card { display: flex; flex-direction: column; gap: 6px; padding: 8px; border-radius: 6px; background: var(--el-fill-color-lighter); }
.method { display: flex; flex-direction: column; gap: 6px; padding: 8px; border: 1px solid var(--el-border-color-lighter); border-radius: 6px; }
.sub { font-size: 12px; font-weight: 600; color: var(--el-text-color-secondary); }
.label { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.hint { font-size: 12px; line-height: 1.6; color: var(--el-text-color-secondary); }
.remove { align-self: flex-start; }
</style>
