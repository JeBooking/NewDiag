<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import type { DocNode, FlowMode, InvestigationFields } from '@/domain/types'
import { KIND_META } from '@/domain/kinds'
import AttachmentField from './AttachmentField.vue'
import BindingEditor from './BindingEditor.vue'

const props = defineProps<{
  node: DocNode
  successors?: { edgeId: string; title: string }[]
  mode?: FlowMode
  readonly?: boolean
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

const codeHint = computed(() => {
  if (props.node.kind !== 'investigation') return '该类型由人工完成，不执行代码'
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
      placeholder="例如：检查母线电压"
      @update:model-value="patchText('title', $event)"
    />

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

    <div class="section-title">参数绑定</div>
    <div class="section-hint">{{ codeHint }}</div>
    <BindingEditor
      v-if="node.kind === 'investigation'"
      :binding="node.binding"
      :readonly="readonly"
      @update="patchBinding"
    />

    <template v-if="node.binding">
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

    <template v-if="(successors?.length ?? 0) > 1">
      <div class="section-title">执行顺序</div>
      <div class="order-list">
        <div v-for="(item, index) in successors" :key="item.edgeId" class="order-row">
          <span class="order-index">{{ index + 1 }}</span>
          <span class="order-title">{{ item.title }}</span>
          <el-button-group size="small">
            <el-button :disabled="readonly || index === 0" @click="emit('reorder', item.edgeId, -1)">
              ↑
            </el-button>
            <el-button
              :disabled="readonly || index === (successors?.length ?? 0) - 1"
              @click="emit('reorder', item.edgeId, 1)"
            >
              ↓
            </el-button>
          </el-button-group>
        </div>
      </div>
      <div class="section-hint">
        {{
          mode === 'serial'
            ? '串行模式按此顺序依次尝试，失败自动换下一条'
            : '并行模式同时执行，顺序只影响队列排布'
        }}
      </div>
    </template>

    <div class="section-title">附件</div>
    <AttachmentField
      :attachments="node.attachments"
      :readonly="readonly"
      @update="patchAttachments"
    />

    <el-button v-if="!readonly" class="delete" type="danger" plain @click="confirmDelete">
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
</style>
