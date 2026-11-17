<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import type {
  DocNode,
  FlowDoc,
  InvestigationFields,
  ManualField,
  NodeSlots,
  ParamType,
  SlotCall,
  StartParam,
} from '@/domain/types'
import { KIND_META } from '@/domain/kinds'
import AttachmentField from './AttachmentField.vue'
import ParamValueInput from './ParamValueInput.vue'
import SlotCallEditor from '@/components/panels/SlotCallEditor.vue'
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

/** 开始节点声明的对象名与启动参数，是其余槽位的可选值来源 */
const startNode = computed(() => props.doc?.nodes?.find((item) => item.kind === 'start'))
const objectNames = computed(() =>
  (startNode.value?.slots?.constructs ?? []).map((item) => item.object).filter((name): name is string => Boolean(name)))
const startInputs = computed(() => (startNode.value?.inputs ?? []).map((item) => item.name))
const parameters = computed(() => (props.node.runtimeInputs ?? []).map((item) => item.name))
const opened = ref(['basic', 'inputs', 'slots', 'order', 'attachments'])
const slots = computed<NodeSlots>(() => props.node.slots ?? {})
const hasFetch = computed(() => Boolean(slots.value.fetch))
const hasProcess = computed(() => Boolean(slots.value.process))
const constructs = computed<SlotCall[]>(() => (isStart.value ? slots.value.constructs ?? [] : []))

function patchSlots(patch: Partial<NodeSlots>): void {
  emit('update', { slots: { ...slots.value, ...patch } })
}

/** 槽位里出现「现场填写」时自动登记成节点现场参数，避免两处各填一遍 */
function syncParameters(next: NodeSlots): void {
  const names = new Set<string>()
  for (const call of [next.fetch, next.process, next.present, next.decide]) {
    for (const value of Object.values(call?.arguments ?? {})) collect(value, names)
  }
  const existing = props.node.runtimeInputs ?? []
  const merged: ManualField[] = [...existing.filter((item) => names.has(item.name))]
  for (const name of names) {
    if (!merged.some((item) => item.name === name)) merged.push({ name, type: 'json', required: true })
  }
  emit('update', { slots: next, runtimeInputs: merged.length ? merged : undefined })
}

function collect(value: unknown, names: Set<string>): void {
  if (!value || typeof value !== 'object') return
  const argument = value as Record<string, unknown>
  if (argument.source === 'parameter' && typeof argument.name === 'string' && argument.name) {
    names.add(argument.name)
  }
  for (const item of (argument.items as unknown[]) ?? []) collect(item, names)
  for (const item of Object.values((argument.fields as Record<string, unknown>) ?? {})) collect(item, names)
}

function updateSlot(name: keyof NodeSlots, value: SlotCall | undefined): void {
  const next = { ...slots.value, [name]: value }
  if (!value) delete next[name]
  syncParameters(next)
}

function setConstruct(index: number, value: SlotCall | undefined): void {
  const list = [...constructs.value]
  if (value) list[index] = value
  else list.splice(index, 1)
  syncParameters({ ...slots.value, constructs: list })
}

function addConstruct(): void {
  syncParameters({ ...slots.value, constructs: [...constructs.value, {
    object: `app${constructs.value.length + 1}`, project: '', interface: '', function: '', arguments: {},
  }] })
}

/** 只有同组内多于一条出边时才需要排序；不同分支之间互不影响 */
const orderGroups = computed(() =>
  (props.successorGroups ?? []).filter((group) => group.items.length > 1),
)

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

    <el-collapse v-model="opened" class="sections">
      <el-collapse-item name="basic" title="基本信息">
        <div class="section-body">
          <label class="label">标题</label>
          <el-input
            :model-value="node.title"
            :disabled="readonly"
            :placeholder="isStart ? '例如：开始诊断' : '例如：检查母线电压'"
            @update:model-value="patchText('title', $event)"
          />

          <template v-if="!isStart">
            <label class="label">详细内容</label>
            <el-input
              :model-value="node.detail"
              :disabled="readonly"
              type="textarea"
              :rows="3"
              placeholder="这一节点要做什么、看什么"
              @update:model-value="patchText('detail', $event)"
            />
          </template>

          <template v-if="node.kind === 'investigation'">
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
        </div>
      </el-collapse-item>

      <el-collapse-item v-if="isStart" name="inputs" title="启动参数">
        <div class="section-body">
          <div class="section-hint">发布时冻结成执行页表单；槽位里的参数来源选「启动输入」即可引用</div>
          <div v-if="!inputs.length" class="section-hint muted">还没有启动参数</div>
          <template v-if="!readonly">
            <label for="input-name" class="label">新增启动参数</label>
            <el-input id="input-name" v-model="inputName" placeholder="例如 device_id"
              @keyup.enter="addInput" />
            <el-select v-model="inputType" aria-label="新增启动参数类型">
              <el-option
                v-for="option in PARAM_TYPES" :key="option.value"
                :label="option.label" :value="option.value"
              />
            </el-select>
            <el-button @click="addInput">添加启动参数</el-button>
            <div class="section-hint muted">填了名字要点「添加启动参数」（或回车）才会出现在上面的列表里</div>
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
        </div>
      </el-collapse-item>

      <el-collapse-item name="slots" :title="isStart ? '接入口径与取数' : '接入口径与数据'">
        <div class="section-body">
          <div class="section-hint">
            {{ isStart
              ? '每个槽位填一个外接工程的公开方法；构造出的对象在后续节点按名字引用。'
              : '固定链：拉取 → 处理 → 展示 → 判定，只在节点内传值；要数据就自己取。' }}
          </div>

          <template v-if="isStart">
            <SlotCallEditor v-for="(call, index) in constructs" :key="`c${index}`"
              :title="`构造 ${index + 1}`" hint="开始节点实例化外接工程的对象，其余槽位按对象名引用"
              :value="call" construct :inputs="startInputs" :parameters="[]"
              :readonly="readonly" @update="setConstruct(index, $event)" />
            <el-button v-if="!readonly" size="small" @click="addConstruct">添加构造对象</el-button>
            <div v-if="!constructs.length" class="section-hint muted">
              可不填；不构造对象时，后面的槽位只能选模块级函数
            </div>

            <SlotCallEditor title="拉取数据" hint="可选；取到的数据由方法自己存进对象，后续节点从对象里取" allow-cache
              :value="slots.fetch" :object-names="objectNames" :inputs="startInputs" :parameters="parameters"
              :readonly="readonly" @update="updateSlot('fetch', $event)" />
            <SlotCallEditor title="处理数据" hint="可选；上一槽的返回值用「上一槽结果」接收"
              :value="slots.process" :object-names="objectNames" :inputs="startInputs" :parameters="parameters"
              :allow-previous="hasFetch" :readonly="readonly" @update="updateSlot('process', $event)" />
          </template>

          <template v-else-if="node.kind === 'investigation'">
            <SlotCallEditor title="拉取数据" hint="可选；取到的数据由方法自己存进对象，后续节点从对象里取" allow-cache
              :value="slots.fetch" :object-names="objectNames" :inputs="startInputs" :parameters="parameters"
              :readonly="readonly" @update="updateSlot('fetch', $event)" />
            <SlotCallEditor title="处理数据" hint="可选；接收本节点拉取的结果"
              :value="slots.process" :object-names="objectNames" :inputs="startInputs" :parameters="parameters"
              :allow-previous="hasFetch" :readonly="readonly" @update="updateSlot('process', $event)" />
            <SlotCallEditor title="展示数据" hint="必填；用本节点拉取或处理的结果来画" required show-view
              :value="slots.present" :object-names="objectNames" :inputs="startInputs" :parameters="parameters"
              :allow-previous="hasFetch || hasProcess" :readonly="readonly" @update="updateSlot('present', $event)" />
            <SlotCallEditor title="判定结果" hint="必填；可用本节点拉取、处理的结果或展示结果来判" required allow-human
              :value="slots.decide" :object-names="objectNames" :inputs="startInputs" :parameters="parameters"
              :allow-previous="hasFetch || hasProcess" :allow-presentation="Boolean(slots.present)"
              :readonly="readonly" @update="updateSlot('decide', $event)" />

            <div v-if="node.runtimeInputs?.length" class="section-hint">
              现场参数：{{ node.runtimeInputs.map((item) => `${item.name}（${item.type}）`).join('、') }}
            </div>
          </template>

          <div v-else class="section-hint muted">这类节点不接代码，完成情况由人工确认。</div>
        </div>
      </el-collapse-item>

      <el-collapse-item v-if="!isStart && orderGroups.length" name="order" title="执行顺序">
        <div class="section-body">
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
        </div>
      </el-collapse-item>

      <el-collapse-item name="attachments" title="附件">
        <div class="section-body">
          <AttachmentField
            :attachments="node.attachments"
            :readonly="readonly"
            @update="patchAttachments"
          />
        </div>
      </el-collapse-item>
    </el-collapse>

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
.sections {
  border-top: 0;
  border-bottom: 0;
}

/* 分组用发丝线分隔，不再用整条色块标题栏：标题只占文字宽度，视觉重量交给留白 */
.sections :deep(.el-collapse-item) {
  border: 0;
  border-top: 1px solid var(--el-border-color-lighter);
}

.sections :deep(.el-collapse-item:first-child) {
  border-top: 0;
}

.sections :deep(.el-collapse-item__header) {
  height: 34px;
  padding: 0 4px;
  gap: 8px;
  border: 0;
  background: transparent;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  color: var(--el-text-color-primary);
}

.sections :deep(.el-collapse-item__header:hover) {
  border-radius: 4px;
  background: var(--el-fill-color-light);
}

/* 2px 短竖条代替整条色带，既标明分组起点，又不抢正文注意力 */
.sections :deep(.el-collapse-item__header)::before {
  content: '';
  width: 2px;
  height: 12px;
  border-radius: 1px;
  background: var(--el-color-primary);
}

.sections :deep(.el-collapse-item__arrow) {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-tertiary);
}

/* 折叠体高度不要锁死：选完接口会新增参数，锁了高度就会被裁掉一截 */
.sections :deep(.el-collapse-item__wrap) {
  height: auto !important;
  overflow: visible;
  border-bottom: 0;
}

.sections :deep(.el-collapse-item__content) {
  padding: 4px 4px 14px;
}

.section-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

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
