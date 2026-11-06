<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import type {
  DocNode,
  FlowDoc,
  FlowMode,
  InvestigationFields,
  FunctionPipeline,
  LibraryRecipe,
  ParamType,
  StartParam,
} from '@/domain/types'
import { KIND_META } from '@/domain/kinds'
import { errorText } from '@/services/api'
import { probeFlow, type ProbeResult } from '@/services/flows'
import AttachmentField from './AttachmentField.vue'
import BindingEditor from './BindingEditor.vue'
import FunctionPipelineEditor from '@/components/panels/FunctionPipelineEditor.vue'
import ParamValueInput from './ParamValueInput.vue'
import DiagnosisMethodEditor from '@/components/panels/DiagnosisMethodEditor.vue'
import LibraryRecipeEditor from '@/components/panels/LibraryRecipeEditor.vue'

const props = defineProps<{
  node: DocNode
  successorGroups?: {
    branch: string
    label: string
    items: { edgeId: string; title: string }[]
  }[]
  mode?: FlowMode
  readonly?: boolean
  /** 探测函数签名需要流程 id 与当前 doc */
  flowId?: string
  doc?: FlowDoc
}>()
const emit = defineEmits<{
  (e: 'update', patch: Partial<DocNode>): void
  (e: 'delete'): void
  (e: 'reorder', edgeId: string, delta: number): void
}>()

const meta = computed(() => KIND_META[props.node.kind])
const fields = computed<InvestigationFields>(
  () => props.node.fields ?? { observation: '', acquisition: '', assessment: '' },
)
const outputs = computed(() => props.node.outputs ?? [])
const outputDraft = ref('')

const isStart = computed(() => props.node.kind === 'start')
const allowsCustomInputs = computed(
  () =>
    !!props.doc?.diagnosis ||
    props.doc?.nodes.some((node) => node.pipeline || node.recipe),
)
const inputName = ref('')
const inputType = ref<ParamType>('text')
const inputError = ref('')
const connectionMode = computed(() =>
  props.node.recipe ? 'recipe' : props.node.pipeline ? 'functions' : 'legacy',
)
const pipelineValue = computed<FunctionPipeline>(() => ({
  ...props.node.pipeline, manualFields: props.node.pipeline?.manualFields ?? [],
}))

const EMPTY_RECIPE: LibraryRecipe = {
  protocol: 'library/v1',
  project: '',
  steps: [{ call: '', show: 'auto' }],
}

function changeConnectionMode(value: unknown): void {
  if (value === 'functions') {
    emit('update', { pipeline: { manualFields: [] }, binding: undefined, recipe: undefined })
  } else if (value === 'recipe') {
    emit('update', { recipe: { ...EMPTY_RECIPE }, binding: undefined, pipeline: undefined })
  } else {
    emit('update', { pipeline: undefined, recipe: undefined })
  }
}

function patchPipeline(value: FunctionPipeline): void {
  emit('update', { pipeline: value, binding: undefined, recipe: undefined })
}

function patchRecipe(value: LibraryRecipe): void {
  emit('update', { recipe: value, binding: undefined, pipeline: undefined })
}

/** 只有同组内多于一条出边时才需要排序；不同分支之间互不影响 */
const orderGroups = computed(() =>
  (props.successorGroups ?? []).filter((group) => group.items.length > 1),
)

const codeHint = computed(() => {
  if (props.doc?.diagnosis) return '调用本次诊断对象的方法，共享初始化数据与按需缓存'
  if (props.node.kind === 'capability') {
    return '按配方调用现成库，执行完沿默认出线继续；本身不产生是/否结论'
  }
  // 能不能绑代码由 KIND_META 声明，别再按类型名判断，否则新增可编程类型时这里会拦错
  if (!KIND_META[props.node.kind].codeCapable) return '该类型由人工完成，不执行代码'
  if (props.node.kind === 'dataPreparation') return '获取、解析一次，供下游排查共享数据'
  if (props.node.recipe) return '按配方调用现成库；判定指向 yes/no 字段时自动判断，留空转人工'
  if (props.node.pipeline?.dataSource) return '复用数据准备结果，只执行展示与判断'
  if (props.node.pipeline) return '平台依次调用四个阶段，无需编写适配器类'
  if (props.node.binding?.protocol === 'investigation/v1') return '四阶段排查：缺省采集或判断由人工完成'
  return props.node.binding ? '已绑定函数，执行时自动判断' : '未绑定函数，执行时由人工判断'
})

function asText(value: unknown): string {
  return typeof value === 'string' ? value : String(value ?? '')
}

function patchText(key: 'title' | 'detail', value: unknown): void {
  emit('update', { [key]: asText(value) })
}

function patchField(key: keyof InvestigationFields, value: unknown): void {
  emit('update', { fields: { ...fields.value, [key]: asText(value) } })
}

function patchBinding(value: DocNode['binding']): void {
  emit('update', { binding: value })
}

function patchAttachments(value: DocNode['attachments']): void {
  emit('update', { attachments: value })
}

function addOutputs(raw: string): void {
  const items = raw
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
  if (items.length === 0) {
    outputDraft.value = ''
    return
  }
  const next = [...outputs.value]
  for (const item of items) {
    if (!next.includes(item)) next.push(item)
  }
  if (next.length !== outputs.value.length) emit('update', { outputs: next })
  outputDraft.value = ''
}

function onOutputInput(value: unknown): void {
  const text = typeof value === 'string' ? value : String(value ?? '')
  if (/[,，]$/.test(text)) {
    addOutputs(text)
    return
  }
  outputDraft.value = text
}

function removeOutput(name: string): void {
  emit('update', { outputs: outputs.value.filter((item) => item !== name) })
}

async function confirmDelete(): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除节点「${props.node.title || props.node.id}」及其连线？`, '删除节点', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    emit('delete')
  } catch {
    // 用户取消
  }
}

/* ---------- 开始节点：启动参数 ---------- */

const PARAM_TYPES: { value: ParamType; label: string }[] = [
  { value: 'text', label: '文本' },
  { value: 'number', label: '数字' },
  { value: 'boolean', label: '布尔' },
  { value: 'datetime', label: '时间' },
]

/** Python 侧类型归一；后端给的就是这四种时会原样通过 */
function normalizeType(raw?: string): ParamType {
  const text = (raw ?? '').toLowerCase()
  if (!text) return 'text'
  if (text.includes('bool')) return 'boolean'
  if (text.includes('date') || text.includes('time')) return 'datetime'
  if (
    text.includes('int') ||
    text.includes('float') ||
    text.includes('number') ||
    text.includes('double') ||
    text.includes('decimal')
  ) {
    return 'number'
  }
  return 'text'
}

interface FunctionBinding {
  key: string
  project: string
  name: string
}

const inputs = computed<StartParam[]>(() => props.node.inputs ?? [])

/** 同一函数可能出现在多个节点，按「工程:函数」去重且保留首次出现顺序 */
const bindings = computed<FunctionBinding[]>(() => {
  const seen = new Set<string>()
  const list: FunctionBinding[] = []
  const diagnosis = props.doc?.diagnosis
  if (diagnosis?.project && diagnosis.function) {
    list.push({ key: `${diagnosis.project}:${diagnosis.function}`,
      project: diagnosis.project, name: diagnosis.function })
    seen.add(`${diagnosis.project}:${diagnosis.function}`)
    for (const node of props.doc?.nodes ?? []) {
      if (!node.method) continue
      const name = `${diagnosis.function}.${node.method}`
      const key = `${diagnosis.project}:${name}`
      if (!seen.has(key)) list.push({ key, project: diagnosis.project, name })
      seen.add(key)
    }
  }
  for (const node of props.doc?.nodes ?? []) {
    const binding = node.pipeline?.acquire ?? (node.pipeline ? undefined : node.binding)
    const project = binding?.project
    const name = binding?.function
    if (!project || !name) continue
    const key = `${project}:${name}`
    if (seen.has(key)) continue
    seen.add(key)
    list.push({ key, project, name })
  }
  return list
})

const bindingKey = computed(() => bindings.value.map((item) => item.key).join('|'))

const probe = ref<ProbeResult>({})
const probeError = ref<string | null>(null)
const probing = ref(false)
let probeSeq = 0

async function runProbe(): Promise<void> {
  if (!isStart.value || !props.flowId || !props.doc) return
  if (bindings.value.length === 0) {
    probe.value = {}
    probeError.value = null
    return
  }
  const seq = ++probeSeq
  probing.value = true
  try {
    const result = await probeFlow(props.flowId, props.doc)
    if (seq !== probeSeq) return
    probe.value = result
    probeError.value = null
  } catch (error) {
    if (seq !== probeSeq) return
    probe.value = {}
    probeError.value = errorText(error, '参数探测失败')
  } finally {
    if (seq === probeSeq) probing.value = false
  }
}

watch([isStart, bindingKey, () => props.flowId], () => void runProbe(), { immediate: true })

interface ParamRow {
  key: string
  name: string
  /** 不带工程名前缀，写进 StartParam.function */
  fnName: string
  type: ParamType
  required: boolean
  probeDefault?: unknown
  global?: StartParam
  own?: StartParam
}

interface FunctionGroup {
  key: string
  label: string
  fnName: string
  error?: string
  params: ParamRow[]
}

const groups = computed<FunctionGroup[]>(() =>
  bindings.value.map((binding) => {
    // 整工程探测失败时后端按工程名记一条 error，函数级按「工程:函数」记
    const entry = probe.value[binding.key] ?? probe.value[binding.project]
    const params = entry?.params ?? []
    const rows: ParamRow[] = params.map((param) => {
      const global = inputs.value.find(
        (item) => item.scope === 'global' && item.name === param.name,
      )
      const own = inputs.value.find(
        (item) =>
          item.scope === 'function' &&
          item.function === binding.name &&
          item.name === param.name,
      )
      return {
        key: `${binding.key}:${param.name}`,
        name: param.name,
        fnName: binding.name,
        type: normalizeType(own?.type ?? global?.type ?? param.type),
        required: param.required === true,
        probeDefault: param.default,
        global,
        own,
      }
    })
    return {
      key: binding.key,
      label: `${binding.project} / ${binding.name}`,
      fnName: binding.name,
      error: entry?.error,
      params: rows,
    }
  }),
)

const globals = computed(() => inputs.value.filter((item) => item.scope === 'global'))

function emitInputs(next: StartParam[]): void {
  emit('update', { inputs: next.length ? next : undefined })
}

function without(list: StartParam[], match: (item: StartParam) => boolean): StartParam[] {
  return list.filter((item) => !match(item))
}

function sameFunctionParam(item: StartParam, fnName: string, name: string): boolean {
  return item.scope === 'function' && item.function === fnName && item.name === name
}

function describeDefault(value: unknown): string {
  if (value === undefined || value === null || value === '') return '空'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function placeholderOf(row: ParamRow): string {
  if (row.global) return `全局默认：${describeDefault(row.global.default)}`
  if (row.probeDefault !== undefined) return `默认：${describeDefault(row.probeDefault)}`
  return '未设置'
}

/** 函数行有值时写函数级参数；清空则该行回到继承（保留类型覆盖） */
function setFunctionValue(row: ParamRow, value: unknown): void {
  const base = without(inputs.value, (item) => sameFunctionParam(item, row.fnName, row.name))
  const prev = row.own
  const keepType = prev?.type ? { type: prev.type } : {}
  if (value === undefined) {
    emitInputs(
      prev?.type
        ? [...base, { name: row.name, scope: 'function', function: row.fnName, ...keepType }]
        : base,
    )
    return
  }
  emitInputs([
    ...base,
    { name: row.name, scope: 'function', function: row.fnName, ...keepType, default: value },
  ])
}

function setFunctionType(row: ParamRow, type: ParamType): void {
  const next = inputs.value.slice()
  const index = next.findIndex((item) => sameFunctionParam(item, row.fnName, row.name))
  const entry: StartParam = {
    name: row.name,
    scope: 'function',
    function: row.fnName,
    type,
    ...(row.own?.default !== undefined ? { default: row.own.default } : {}),
  }
  if (index < 0) next.push(entry)
  else next[index] = entry
  emitInputs(next)
}

function clearOverride(row: ParamRow): void {
  setFunctionValue(row, undefined)
}

/** 提升为全局：同名参数进全局区，本函数行退回继承 */
function promote(row: ParamRow): void {
  if (row.global) return
  const base = without(inputs.value, (item) => sameFunctionParam(item, row.fnName, row.name))
  const value = row.own?.default !== undefined ? row.own.default : row.probeDefault
  emitInputs([
    ...base,
    {
      name: row.name,
      scope: 'global',
      type: row.type,
      ...(value !== undefined ? { default: value } : {}),
    },
  ])
}

function setGlobalType(name: string, type: ParamType): void {
  const next = inputs.value.map((item) =>
    item.scope === 'global' && item.name === name ? { ...item, type } : item,
  )
  emitInputs(next)
}

function setGlobalValue(name: string, value: unknown): void {
  const next = inputs.value.map((item) => {
    if (item.scope !== 'global' || item.name !== name) return item
    const copy: StartParam = { ...item }
    if (value === undefined) delete copy.default
    else copy.default = value
    return copy
  })
  emitInputs(next)
}

function demote(name: string): void {
  emitInputs(without(inputs.value, (item) => item.scope === 'global' && item.name === name))
}

function onGlobalType(name: string, value: unknown): void {
  setGlobalType(name, normalizeType(String(value)))
}

function onFunctionType(row: ParamRow, value: unknown): void {
  setFunctionType(row, normalizeType(String(value)))
}

function addGlobalInput(): void {
  const name = inputName.value.trim()
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
    inputError.value = '参数名请使用英文字母、数字和下划线，且不能以数字开头'
    return
  }
  if (inputs.value.some((item) => item.scope === 'global' && item.name === name)) {
    inputError.value = '该全局参数已存在'
    return
  }
  emitInputs([...inputs.value, { name, scope: 'global', type: inputType.value }])
  inputName.value = ''
  inputError.value = ''
}
</script>

<template>
  <div class="node-panel">
    <div class="kind-row">
      <span class="kind-label" :style="{ borderColor: meta.color, color: meta.color }">
        {{ meta.label }}
      </span>
      <span class="node-id">{{ node.id }}</span>
    </div>

    <label class="label">标题</label>
    <el-input
      :model-value="node.title"
      :disabled="readonly"
      :placeholder="isStart ? '例如：开始诊断' : '例如：检查母线电压'"
      @update:model-value="patchText('title', $event)"
    />

    <template v-if="isStart">
      <div class="section-title">全局参数</div>
      <div class="section-hint">所有函数都能读到；函数行同名参数默认继承这里的值</div>
      <div v-if="!globals.length" class="section-hint muted">
        还没有全局参数，可从获取函数参数行提升，或为四阶段排查添加诊断输入
      </div>
      <template v-if="allowsCustomInputs && !readonly">
        <label for="global-input-name" class="label">新增诊断输入</label>
        <el-input id="global-input-name" v-model="inputName" placeholder="例如 device_id" />
        <el-select v-model="inputType" aria-label="新增诊断输入类型">
          <el-option
            v-for="option in PARAM_TYPES" :key="option.value"
            :label="option.label" :value="option.value"
          />
        </el-select>
        <el-button @click="addGlobalInput">添加全局参数</el-button>
        <div v-if="inputError" class="section-hint error" role="alert">{{ inputError }}</div>
      </template>
      <div v-for="item in globals" :key="item.name" class="param-row">
        <div class="param-line">
          <span class="param-name" :title="item.name">{{ item.name }}</span>
          <el-select
            :model-value="item.type ?? 'text'"
            :disabled="readonly"
            size="small"
            class="type-select"
            @update:model-value="onGlobalType(item.name, $event)"
          >
            <el-option
              v-for="option in PARAM_TYPES"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <el-button v-if="!readonly" link size="small" @click="demote(item.name)">
            取消全局
          </el-button>
        </div>
        <ParamValueInput
          :model-value="item.default"
          :type="item.type ?? 'text'"
          :readonly="readonly"
          placeholder="全局默认值"
          @update:model-value="setGlobalValue(item.name, $event)"
        />
      </div>

      <div class="section-title">函数参数</div>
      <div class="section-hint" v-if="probing">正在读取函数签名…</div>
      <div v-if="probeError" class="section-hint error">{{ probeError }}</div>
      <div v-if="!bindings.length" class="section-hint muted">
        未配置获取函数时，可为人工取数直接添加全局诊断输入
      </div>

      <div v-for="group in groups" :key="group.key" class="fn-group">
        <div class="fn-label">{{ group.label }}</div>
        <div v-if="group.error" class="section-hint error">{{ group.error }}</div>
        <div v-else-if="!group.params.length" class="section-hint muted">该函数没有参数</div>
        <div
          v-for="row in group.params"
          :key="row.key"
          class="param-row"
          :class="{ inherit: Boolean(row.global) && row.own?.default === undefined }"
        >
          <div class="param-line">
            <span class="param-name" :title="row.name">{{ row.name }}</span>
            <el-tag v-if="row.required" size="small" type="danger" effect="plain">必填</el-tag>
            <el-select
              :model-value="row.type"
              :disabled="readonly"
              size="small"
              class="type-select"
              @update:model-value="onFunctionType(row, $event)"
            >
              <el-option
                v-for="option in PARAM_TYPES"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-button
              v-if="!readonly && !row.global"
              link
              size="small"
              type="primary"
              @click="promote(row)"
            >
              提升为全局
            </el-button>
            <el-button
              v-else-if="!readonly && row.own?.default !== undefined"
              link
              size="small"
              @click="clearOverride(row)"
            >
              恢复继承
            </el-button>
            <span v-else-if="row.global" class="inherit-tag">继承全局</span>
          </div>
          <ParamValueInput
            :model-value="row.own?.default"
            :type="row.type"
            :readonly="readonly"
            :placeholder="placeholderOf(row)"
            @update:model-value="setFunctionValue(row, $event)"
          />
        </div>
      </div>

      <div class="section-hint">
        参数由所选入口的签名推导，也可添加全局诊断输入。默认值随发布保存，执行时可再改。
      </div>
    </template>

    <template v-else>
      <label class="label">详细内容</label>
      <el-input
        :model-value="node.detail"
        :disabled="readonly"
        type="textarea"
        :rows="3"
        placeholder="这一节点要做什么、看什么"
        @update:model-value="patchText('detail', $event)"
      />

      <template v-if="node.kind === 'investigation'">
        <div class="section-title">排查说明</div>
        <label class="label">观察对象</label>
        <el-input
          :model-value="fields.observation"
          :disabled="readonly"
          placeholder="例如：母线电压"
          @update:model-value="patchField('observation', $event)"
        />
        <label class="label">取数方式</label>
        <el-input
          :model-value="fields.acquisition"
          :disabled="readonly"
          placeholder="例如：调用数据服务器读取 voltage"
          @update:model-value="patchField('acquisition', $event)"
        />
        <label class="label">判定标准</label>
        <el-input
          :model-value="fields.assessment"
          :disabled="readonly"
          placeholder="例如：低于 210V 判为异常"
          @update:model-value="patchField('assessment', $event)"
        />
      </template>

      <div class="section-title">执行方式</div>
      <div class="section-hint">{{ codeHint }}</div>
      <el-select
        v-if="node.kind === 'investigation' && !doc?.diagnosis" :model-value="connectionMode" :disabled="readonly"
        aria-label="排查接入方式" @change="changeConnectionMode"
      >
        <el-option label="能力配方（调用现成库）" value="recipe" />
        <el-option label="分别选择四阶段函数" value="functions" />
        <el-option label="原有函数或适配器类" value="legacy" />
      </el-select>
      <DiagnosisMethodEditor
        v-if="node.kind === 'investigation' && doc?.diagnosis"
        :diagnosis="doc.diagnosis" :value="node.method" :readonly="readonly"
        @update="emit('update', { method: $event, pipeline: undefined, binding: undefined })"
      />
      <LibraryRecipeEditor
        v-else-if="node.recipe || node.kind === 'capability'"
        :key="node.id"
        :value="node.recipe ?? EMPTY_RECIPE"
        :readonly="readonly"
        :flow-id="flowId"
        :doc="doc"
        :node-id="node.id"
        :allow-decision="node.kind === 'investigation'"
        @update="patchRecipe"
      />
      <FunctionPipelineEditor
        v-else-if="node.kind === 'dataPreparation'
          || (node.kind === 'investigation' && connectionMode === 'functions')"
        :key="node.id" :value="pipelineValue" :readonly="readonly"
        :preparation="node.kind === 'dataPreparation'" :doc="doc" :node-id="node.id"
        @update="patchPipeline"
      />
      <BindingEditor
        v-else-if="node.kind === 'investigation'"
        :binding="node.binding"
        :readonly="readonly"
        @update="patchBinding"
      />

      <template v-if="node.binding || node.pipeline || node.method">
        <div class="section-title">输出字段</div>
        <div class="tag-input">
          <el-tag
            v-for="item in outputs"
            :key="item"
            size="small"
            :closable="!readonly"
            @close="removeOutput(item)"
          >
            {{ item }}
          </el-tag>
          <el-input
            v-if="!readonly"
            :model-value="outputDraft"
            size="small"
            placeholder="输入后回车或逗号"
            class="tag-field"
            @update:model-value="onOutputInput"
            @keydown.enter.prevent="addOutputs(outputDraft)"
            @blur="addOutputs(outputDraft)"
          />
        </div>
        <div class="section-hint">该函数返回结构里的字段名，供下游节点绑定；判据是布尔值的函数可留空</div>
      </template>

    <template v-if="orderGroups.length">
      <div class="section-title">执行顺序</div>
      <div class="order-groups">
        <div v-for="group in orderGroups" :key="group.branch" class="order-group">
          <div class="order-group-head">{{ group.label }}</div>
          <div class="order-list">
            <div v-for="(item, index) in group.items" :key="item.edgeId" class="order-row">
              <span class="order-index">{{ index + 1 }}</span>
              <span class="order-title">{{ item.title }}</span>
              <el-button-group size="small">
                <el-button
                  :disabled="readonly || index === 0"
                  @click="emit('reorder', item.edgeId, -1)"
                >
                  ↑
                </el-button>
                <el-button
                  :disabled="readonly || index === group.items.length - 1"
                  @click="emit('reorder', item.edgeId, 1)"
                >
                  ↓
                </el-button>
              </el-button-group>
            </div>
          </div>
        </div>
      </div>
      <div class="section-hint">
        {{
          mode === 'serial'
            ? '串行模式按各组内的顺序依次尝试，失败自动换下一条'
            : '并行模式同时执行，顺序只影响同组内的队列排布'
        }}
      </div>
    </template>

      <div class="section-title">附件</div>
      <AttachmentField
        :attachments="node.attachments"
        :readonly="readonly"
        @update="patchAttachments"
      />
    </template>

    <el-button
      v-if="!readonly && !isStart"
      class="delete"
      type="danger"
      plain
      @click="confirmDelete"
    >
      删除节点
    </el-button>
  </div>
</template>

<style scoped>
.node-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kind-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.kind-label {
  font-size: 12px;
  border: 1px solid currentColor;
  border-radius: 4px;
  padding: 1px 6px;
}

.node-id {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-family: ui-monospace, Consolas, monospace;
}

.label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.section-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 2px;
  line-height: 1.6;
}

.section-hint.muted {
  color: var(--el-text-color-placeholder);
}

.section-hint.error {
  color: var(--el-color-danger);
}

.delete {
  margin-top: 16px;
  width: 100%;
}

.tag-input {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.tag-field {
  width: 100%;
}

.order-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.order-groups {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.order-group-head {
  margin-bottom: 3px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.order-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.order-index {
  flex: none;
  width: 18px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.order-title {
  flex: 1;
  overflow: hidden;
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.fn-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 10px;
}

.fn-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-regular);
  padding: 2px 6px;
  border-left: 2px solid var(--el-color-primary);
  background: var(--el-fill-color-light);
}

.param-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 6px;
  border-radius: 4px;
}

.param-row.inherit {
  background: var(--el-fill-color-lighter);
}

.param-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.param-name {
  flex: 1;
  overflow: hidden;
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.type-select {
  width: 78px;
  flex: none;
}

.inherit-tag {
  flex: none;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.param-row.inherit :deep(.el-input__wrapper) {
  background: var(--el-fill-color-blank);
  box-shadow: 0 0 0 1px var(--el-border-color-lighter) inset;
}
</style>
