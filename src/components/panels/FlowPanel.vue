<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { FlowClass, FlowDetail, FlowMode, NodeBinding } from '@/domain/types'
import BindingEditor from '@/components/panels/BindingEditor.vue'
import FlowClassEditor from '@/components/panels/FlowClassEditor.vue'

const props = defineProps<{
  flow: FlowDetail
  snapshotFields?: string[]
  readonly?: boolean
  publishing?: boolean
  diagnosis?: NodeBinding
  flowClass?: FlowClass
}>()
const emit = defineEmits<{
  (e: 'update-meta', patch: { name?: string; mode?: FlowMode }): void
  (e: 'update-snapshot-fields', value: string[]): void
  (e: 'publish'): void
  (e: 'update-diagnosis', value: NodeBinding | undefined): void
  (e: 'update-flow-class', value: FlowClass | undefined): void
}>()

const draft = ref('')
const fields = computed(() => props.snapshotFields ?? [])

const name = ref(props.flow.name)
const nameDirty = computed(() => name.value.trim() !== props.flow.name && name.value.trim().length > 0)

watch(
  () => props.flow.name,
  (value) => {
    name.value = value
  },
)

const latestRelease = computed(() => {
  const list = props.flow.releases ?? []
  return list.reduce<(typeof list)[number] | null>(
    (latest, item) => (!latest || item.versionNo > latest.versionNo ? item : latest),
    null,
  )
})

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

function commitName(): void {
  if (!nameDirty.value) return
  emit('update-meta', { name: name.value.trim() })
}

function changeMode(value: unknown): void {
  const mode = value === 'serial' ? 'serial' : 'parallel'
  if (mode !== props.flow.mode) emit('update-meta', { mode })
}

function addFields(raw: string): void {
  const items = raw
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
  if (items.length === 0) {
    draft.value = ''
    return
  }
  const next = [...fields.value]
  for (const item of items) {
    if (!next.includes(item)) next.push(item)
  }
  if (next.length !== fields.value.length) emit('update-snapshot-fields', next)
  draft.value = ''
}

function onDraftInput(value: unknown): void {
  const text = typeof value === 'string' ? value : String(value ?? '')
  if (/[,，]$/.test(text)) {
    addFields(text)
    return
  }
  draft.value = text
}

function removeField(name: string): void {
  emit(
    'update-snapshot-fields',
    fields.value.filter((item) => item !== name),
  )
}
</script>

<template>
  <div class="flow-panel">
    <div class="title">流程属性</div>

    <label class="label">名称</label>
    <el-input
      v-model="name"
      :disabled="readonly"
      placeholder="流程名称"
      @blur="commitName"
      @keyup.enter="commitName"
    />

    <label class="label">执行模式</label>
    <el-radio-group
      :model-value="flow.mode"
      :disabled="readonly"
      @update:model-value="changeMode"
    >
      <el-radio-button value="parallel">并行</el-radio-button>
      <el-radio-button value="serial">串行</el-radio-button>
    </el-radio-group>
    <div class="section-hint">
      并行：同组后继同时推进；串行：按出线顺序逐条尝试，遇到问题解决节点后由人决定结束或继续
    </div>

    <div class="section-title">流程类</div>
    <FlowClassEditor
      :value="flowClass" :readonly="readonly"
      @update="emit('update-flow-class', $event)"
    />
    <div class="section-hint">把流程声明成一个 Python 类：导入工程、构造一次、方法由排查节点调用。与下面的「诊断实例」二选一。</div>

    <div class="section-title">诊断实例</div>
    <BindingEditor
      :binding="diagnosis" :readonly="readonly" diagnosis-only
      @update="emit('update-diagnosis', $event)"
    />
    <div class="section-hint">可选。选择后，排查节点直接调用该诊断类的方法，获取和解析由实例统一管理。启动参数在开始节点配置。</div>

    <div class="section-title">快照字段（旧版兼容）</div>
    <div class="tag-input">
      <el-tag
        v-for="field in fields"
        :key="field"
        size="small"
        :closable="!readonly"
        @close="removeField(field)"
      >
        {{ field }}
      </el-tag>
      <el-input
        v-if="!readonly"
        :model-value="draft"
        size="small"
        placeholder="输入后回车或逗号"
        class="tag-field"
        @update:model-value="onDraftInput"
        @keydown.enter.prevent="addFields(draft)"
        @blur="addFields(draft)"
      />
    </div>
    <div v-if="readonly && !fields.length" class="section-hint">未设置</div>
    <div class="section-hint">启动时把这些字段作为诊断快照，节点按名字自动取用</div>

    <div class="section-title">版本</div>
    <div class="kv">
      <span class="key">发布版本数</span>
      <span class="value">{{ flow.releases?.length ?? 0 }}</span>
    </div>
    <div class="kv">
      <span class="key">最近发布</span>
      <span class="value">
        <template v-if="latestRelease">
          v{{ latestRelease.versionNo }} · {{ formatTime(latestRelease.publishedAt) }}
        </template>
        <template v-else>尚未发布</template>
      </span>
    </div>
    <div class="kv">
      <span class="key">最近更新</span>
      <span class="value">{{ formatTime(flow.updatedAt) }}</span>
    </div>

    <el-button
      class="publish"
      type="primary"
      :disabled="readonly"
      :loading="publishing"
      @click="emit('publish')"
    >
      发布新版本
    </el-button>
    <div class="section-hint">发布前会按校验规则编译为步骤表，版本不可修改</div>
  </div>
</template>

<style scoped>
.flow-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.title {
  font-size: 13px;
  font-weight: 600;
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
  line-height: 1.6;
}

.kv {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
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

.key {
  color: var(--el-text-color-secondary);
  flex: none;
}

.value {
  text-align: right;
  color: var(--el-text-color-regular);
}

.publish {
  margin-top: 16px;
  width: 100%;
}
</style>
