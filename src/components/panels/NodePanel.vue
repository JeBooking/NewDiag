<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import type {
  DocNode,
  FlowDoc,
  InvestigationFields,
  ManualField,
  ParamType,
  StartParam,
  CompositionArgument,
} from '@/domain/types'
import { KIND_META } from '@/domain/kinds'
import AttachmentField from './AttachmentField.vue'
import ParamValueInput from './ParamValueInput.vue'
import CompositionArgumentEditor from '@/components/panels/CompositionArgumentEditor.vue'
import { PARAM_TYPES, parameterType as normalizeType } from '@/domain/parameters'

const props = defineProps<{
  node: DocNode
  successorGroups?: {
    branch: string
    label: string
    items: { edgeId: string; title: string }[]
  }[]
  readonly?: boolean
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
const isStart = computed(() => props.node.kind === 'start')
const operationParameters = computed(() => props.doc?.composition?.operations
  .find((item) => item.id === props.node.operation)?.parameters ?? [])

function changeOperation(value: unknown): void {
  emit('update', {
    operation: typeof value === 'string' && value ? value : undefined,
    operationArguments: {},
    runtimeInputs: undefined,
  })
}

function setOperationSource(name: string, manual: unknown): void {
  const argumentsMap = { ...props.node.operationArguments }
  const fields = (props.node.runtimeInputs ?? []).filter((item) => item.name !== name)
  if (manual) {
    delete argumentsMap[name]
    fields.push({ name, type: 'json', required: true })
  } else argumentsMap[name] = { lit: null }
  emit('update', { operationArguments: argumentsMap, runtimeInputs: fields })
}
function setOperationType(name: string, type: ParamType): void {
  const fields = (props.node.runtimeInputs ?? []).filter((item) => item.name !== name)
  emit('update', { runtimeInputs: [...fields, { name, type, required: true }] })
}
function setOperationArgument(name: string, value: CompositionArgument): void {
  emit('update', { operationArguments: { ...props.node.operationArguments, [name]: value } })
}

/** 只有同组内多于一条出边时才需要排序；不同分支之间互不影响 */
const orderGroups = computed(() =>
  (props.successorGroups ?? []).filter((group) => group.items.length > 1),
)

const codeHint = computed(() => {
  if (props.doc?.composition) return '复用流程中配置的排查操作，共享启动资源与按需缓存'
  return '未配置组合实例：执行到此节点时由人工判断'
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

function patchAttachments(value: DocNode['attachments']): void {
  emit('update', { attachments: value })
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

const inputs = computed<StartParam[]>(() => props.node.inputs ?? [])
const inputName = ref('')
const inputType = ref<ParamType>('text')
const inputError = ref('')

function emitInputs(next: StartParam[]): void {
  emit('update', { inputs: next.length ? next : undefined })
}

function setInputType(name: string, type: ParamType): void {
  emitInputs(inputs.value.map((item) => (item.name === name ? { ...item, type } : item)))
}

function setInputValue(name: string, value: unknown): void {
  emitInputs(inputs.value.map((item) => {
    if (item.name !== name) return item
    const copy: StartParam = { ...item }
    if (value === undefined) delete copy.default
    else copy.default = value
    return copy
  }))
}

function removeInput(name: string): void {
  emitInputs(inputs.value.filter((item) => item.name !== name))
}

function addInput(): void {
  const name = inputName.value.trim()
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
    inputError.value = '参数名请使用英文字母、数字和下划线，且不能以数字开头'
    return
  }
  if (inputs.value.some((item) => item.name === name)) {
    inputError.value = '该启动参数已存在'
    return
  }
  emitInputs([...inputs.value, { name, type: inputType.value }])
  inputName.value = ''
  inputError.value = ''
}

function onInputType(name: string, value: unknown): void {
  setInputType(name, normalizeType(String(value)))
}

/* ---------- 现场参数 ---------- */

function patchRuntimeInputs(value: ManualField[]): void {
  emit('update', { runtimeInputs: value.length ? value : undefined })
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
      <div class="section-title">启动参数</div>
      <div class="section-hint">发布时冻结成执行页表单；组合配置里的参数来源选「启动输入」即可引用</div>
      <div v-if="!inputs.length" class="section-hint muted">还没有启动参数</div>
      <template v-if="!readonly">
        <label for="input-name" class="label">新增启动参数</label>
        <el-input id="input-name" v-model="inputName" placeholder="例如 device_id" />
        <el-select v-model="inputType" aria-label="新增启动参数类型">
          <el-option
            v-for="option in PARAM_TYPES" :key="option.value"
            :label="option.label" :value="option.value"
          />
        </el-select>
        <el-button @click="addInput">添加启动参数</el-button>
        <div v-if="inputError" class="section-hint error" role="alert">{{ inputError }}</div>
      </template>
      <div v-for="item in inputs" :key="item.name" class="param-row">
        <div class="param-line">
          <span class="param-name" :title="item.name">{{ item.name }}</span>
          <el-select
            :model-value="item.type ?? 'text'"
            :disabled="readonly"
            size="small"
            class="type-select"
            @update:model-value="onInputType(item.name, $event)"
          >
            <el-option
              v-for="option in PARAM_TYPES"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <el-button v-if="!readonly" link size="small" @click="removeInput(item.name)">
            删除
          </el-button>
        </div>
        <ParamValueInput
          :model-value="item.default"
          :type="item.type ?? 'text'"
          :readonly="readonly"
          placeholder="默认值"
          @update:model-value="setInputValue(item.name, $event)"
        />
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

      <div class="section-title">排查操作</div>
      <div class="section-hint">{{ codeHint }}</div>
      <template v-if="node.kind === 'investigation' && doc?.composition">
        <el-select :model-value="node.operation" :disabled="readonly" clearable
          aria-label="排查操作" placeholder="选择操作；留空由人工判断" @change="changeOperation">
          <el-option v-for="operation in doc.composition.operations" :key="operation.id"
            :label="operation.id" :value="operation.id" />
        </el-select>
        <div v-for="name in operationParameters" :key="name">
          <label>{{ name }}</label>
          <el-checkbox :model-value="!(name in (node.operationArguments ?? {}))" :disabled="readonly"
            @change="setOperationSource(name, $event)">执行到此节点时填写</el-checkbox>
          <el-select v-if="!(name in (node.operationArguments ?? {}))"
            :model-value="node.runtimeInputs?.find((field) => field.name === name)?.type ?? 'json'"
            :disabled="readonly" :aria-label="`${name} 输入类型`" @change="setOperationType(name, $event)">
            <el-option v-for="option in PARAM_TYPES" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <CompositionArgumentEditor v-else :key="`${node.id}:${name}`"
            :value="node.operationArguments?.[name]" :composition="doc.composition"
            :readonly="readonly" @update="setOperationArgument(name, $event)" />
        </div>
        <div v-if="node.runtimeInputs?.length" class="section-hint">
          现场参数：{{ node.runtimeInputs.map((item) => item.name).join('、') }}
        </div>
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
        <div class="section-hint">同组内按顺序执行；运行模式与并行上限在启动时选择</div>
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

.param-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 6px;
  border-radius: 4px;
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
</style>
