<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import type { FlowDoc, LibraryRecipe, RecipeShow, RecipeStep } from '@/domain/types'
import { listProjects, type Project } from '@/services/projects'
import { probeFlow } from '@/services/flows'

const props = defineProps<{
  value: LibraryRecipe
  readonly?: boolean
  flowId?: string
  doc?: FlowDoc
  nodeId: string
  /** 排查节点才需要是/否判定来源 */
  allowDecision?: boolean
}>()
const emit = defineEmits<{ (e: 'update', value: LibraryRecipe): void }>()

const SHOWS: { value: RecipeShow; label: string }[] = [
  { value: 'auto', label: '自动' },
  { value: 'text', label: '文本' },
  { value: 'table', label: '表格' },
  { value: 'line', label: '曲线' },
  { value: 'image', label: '图片' },
  { value: 'none', label: '不展示' },
]

const projects = ref<Project[]>([])
const probe = reactive<Record<string, { params: string[]; error?: string }>>({})
const probing = ref(false)
const probeError = ref('')

onMounted(async () => {
  try {
    projects.value = await listProjects()
  } catch {
    projects.value = []
  }
})

const steps = computed<RecipeStep[]>(() => props.value.steps ?? [])
const saved = computed(() => steps.value.map((step) => step.as ?? '').filter(Boolean))
const upstreamNodes = computed(() =>
  (props.doc?.nodes ?? []).filter((node) => node.id !== props.nodeId),
)

/** 参数用 JSON 编辑；保留草稿，避免每敲一个字符就往返一次文档 */
const drafts = reactive<Record<number, string>>({})
const errors = reactive<Record<number, string>>({})

function argsText(step: RecipeStep): string {
  const value = step.args ?? {}
  return Object.keys(value).length ? JSON.stringify(value, null, 2) : '{}'
}

function onArgsInput(index: number, raw: string): void {
  drafts[index] = raw
  if (!raw.trim()) {
    errors[index] = ''
    patchStep(index, { args: {} })
    return
  }
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      errors[index] = '参数必须是对象'
      return
    }
    errors[index] = ''
    patchStep(index, { args: parsed as Record<string, unknown> })
  } catch (error) {
    errors[index] = `JSON 解析失败：${(error as Error).message}`
  }
}

function patch(next: Partial<LibraryRecipe>): void {
  emit('update', { ...props.value, ...next })
}

function patchStep(index: number, next: Partial<RecipeStep>): void {
  const list = steps.value.map((step, at) => (at === index ? { ...step, ...next } : step))
  patch({ steps: list })
}

function addStep(): void {
  patch({ steps: [...steps.value, { call: '', show: 'auto' }] })
}

function removeStep(index: number): void {
  delete drafts[index]
  delete errors[index]
  patch({ steps: steps.value.filter((_, at) => at !== index) })
}

function moveStep(index: number, delta: number): void {
  const target = index + delta
  if (target < 0 || target >= steps.value.length) return
  const list = steps.value.slice()
  const [item] = list.splice(index, 1)
  list.splice(target, 0, item)
  patch({ steps: list })
}

function setProject(name: string): void {
  patch({ project: name })
}

/* ---------- 输出与判定 ---------- */

const outputs = computed(() => Object.entries(props.value.out ?? {}))

function setOutput(name: string, from: string, path: string): void {
  const next = { ...(props.value.out ?? {}) }
  if (!name.trim()) return
  next[name] = { from, ...(path ? { path } : {}) }
  patch({ out: next })
}

function renameOutput(oldName: string, name: string): void {
  if (!name.trim() || name === oldName) return
  const next: Record<string, { from: string; path?: string }> = {}
  for (const [key, item] of outputs.value) next[key === oldName ? name : key] = item
  patch({ out: next })
}

function removeOutput(name: string): void {
  const next = { ...(props.value.out ?? {}) }
  delete next[name]
  patch({ out: Object.keys(next).length ? next : undefined })
}

function addOutput(): void {
  const from = saved.value[0] ?? ''
  patch({ out: { ...(props.value.out ?? {}), [`field${outputs.value.length + 1}`]: { from } } })
}

function setResult(from: string, path: string): void {
  patch({ result: { from, ...(path ? { path } : {}) } })
}

function setReason(value: string): void {
  patch({ reason: value ? { template: value } : undefined })
}

const reasonText = computed(() => {
  const reason = props.value.reason
  if (typeof reason === 'string') return reason
  return reason?.template ?? ''
})

/* ---------- 探测 ---------- */

async function readSignatures(): Promise<void> {
  if (!props.flowId || !props.doc) {
    probeError.value = '保存流程后才能读取函数签名'
    return
  }
  probing.value = true
  probeError.value = ''
  try {
    const result = await probeFlow(props.flowId, props.doc)
    for (const key of Object.keys(probe)) delete probe[key]
    for (const [key, item] of Object.entries(result)) {
      probe[key] = {
        params: (item.params ?? []).map((param) =>
          `${param.name}${param.required ? '' : '?'}`,
        ),
        error: item.error,
      }
    }
    const missing = steps.value
      .filter((step) => !step.on)
      .map((step) => `${props.value.project}:${step.call}`)
      .filter((key) => !probe[key])
    if (missing.length) {
      probeError.value = `以下调用没有探测到签名：${missing.join('、')}。对象方法（步骤里填了「对象」）不在发布期校验。`
    }
  } catch (error) {
    probeError.value = (error as Error).message || '探测失败'
  } finally {
    probing.value = false
  }
}

function hintOf(step: RecipeStep): string {
  if (!step.call) return ''
  const entry = probe[`${props.value.project}:${step.call}`]
  if (!entry) return ''
  if (entry.error) return entry.error
  return entry.params.length ? `参数：${entry.params.join('、')}` : '该调用没有参数'
}
</script>

<template>
  <div class="recipe-editor">
    <div class="section-title">能力配方</div>
    <div class="section-hint">
      按顺序调用现成库的函数；对象只在本节点内传递，节点之间只传 JSON。
    </div>

    <label class="label">工程</label>
    <el-select
      :model-value="value.project" :disabled="readonly" placeholder="选择已登记的工程"
      filterable @update:model-value="setProject(String($event))"
    >
      <el-option v-for="item in projects" :key="item.name" :label="item.name" :value="item.name" />
    </el-select>

    <div class="step-head">
      <span>调用步骤</span>
      <el-button v-if="!readonly" link size="small" type="primary" @click="addStep">添加步骤</el-button>
    </div>

    <div v-for="(step, index) in steps" :key="index" class="step-card">
      <div class="step-line">
        <span class="step-index">{{ index + 1 }}</span>
        <el-input
          :model-value="step.call" :disabled="readonly" size="small" placeholder="Dataset.load"
          @update:model-value="patchStep(index, { call: String($event) })"
        />
        <el-button-group v-if="!readonly" size="small">
          <el-button :disabled="index === 0" @click="moveStep(index, -1)">↑</el-button>
          <el-button :disabled="index === steps.length - 1" @click="moveStep(index, 1)">↓</el-button>
          <el-button @click="removeStep(index)">删</el-button>
        </el-button-group>
      </div>
      <div v-if="hintOf(step)" class="section-hint" :class="{ error: probe[`${value.project}:${step.call}`]?.error }">
        {{ hintOf(step) }}
      </div>

      <div class="step-line">
        <span class="field-label">对象</span>
        <el-select
          :model-value="step.on ?? ''" :disabled="readonly" size="small" clearable
          placeholder="工程根对象"
          @update:model-value="patchStep(index, { on: $event ? String($event) : undefined })"
        >
          <el-option v-for="name in saved.filter((item) => item !== step.as)" :key="name" :label="name" :value="name" />
        </el-select>
        <span class="field-label">保存为</span>
        <el-input
          :model-value="step.as ?? ''" :disabled="readonly" size="small" placeholder="变量名"
          @update:model-value="patchStep(index, { as: String($event) || undefined })"
        />
      </div>

      <div class="step-line">
        <span class="field-label">展示</span>
        <el-select
          :model-value="step.show ?? 'auto'" :disabled="readonly" size="small"
          @update:model-value="patchStep(index, { show: $event as RecipeShow })"
        >
          <el-option v-for="item in SHOWS" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-input
          :model-value="step.label ?? ''" :disabled="readonly" size="small" placeholder="展示标题（可留空）"
          @update:model-value="patchStep(index, { label: String($event) || undefined })"
        />
      </div>

      <el-input
        :model-value="drafts[index] ?? argsText(step)" :disabled="readonly" type="textarea" :rows="3"
        class="args" placeholder='{"path": {"$run": "n1", "path": "output.data_file"}}'
        @update:model-value="onArgsInput(index, String($event))"
      />
      <div v-if="errors[index]" class="section-hint error">{{ errors[index] }}</div>
    </div>

    <el-button v-if="flowId" size="small" :loading="probing" @click="readSignatures">读取函数签名</el-button>
    <div v-if="probeError" class="section-hint error">{{ probeError }}</div>

    <div class="step-head">
      <span>对下游输出</span>
      <el-button v-if="!readonly" link size="small" type="primary" @click="addOutput">添加字段</el-button>
    </div>
    <div class="section-hint">只传 JSON。下游用「上游节点输出」引用这里的字段名。</div>
    <div v-for="[name, source] in outputs" :key="name" class="step-line">
      <el-input
        :model-value="name" :disabled="readonly" size="small" placeholder="字段名"
        @change="renameOutput(name, String($event))"
      />
      <el-select
        :model-value="source.from" :disabled="readonly" size="small" placeholder="来源"
        @update:model-value="setOutput(name, String($event), source.path ?? '')"
      >
        <el-option v-for="item in saved" :key="item" :label="item" :value="item" />
      </el-select>
      <el-input
        :model-value="source.path ?? ''" :disabled="readonly" size="small" placeholder="字段路径（可空）"
        @update:model-value="setOutput(name, source.from, String($event))"
      />
      <el-button v-if="!readonly" link size="small" @click="removeOutput(name)">删</el-button>
    </div>

    <template v-if="allowDecision">
      <div class="step-head"><span>判定</span></div>
      <div class="section-hint">
        指向 yes/no 字段时自动判断；留空则停下来让人看图判断。
      </div>
      <div class="step-line">
        <el-select
          :model-value="value.result?.from ?? ''" :disabled="readonly" size="small" clearable
          placeholder="人工判断"
          @update:model-value="setResult(String($event), value.result?.path ?? '')"
        >
          <el-option v-for="item in saved" :key="item" :label="item" :value="item" />
        </el-select>
        <el-input
          :model-value="value.result?.path ?? ''" :disabled="readonly" size="small" placeholder="判定字段，如 overall"
          @update:model-value="setResult(value.result?.from ?? '', String($event))"
        />
      </div>

      <label class="label">判断依据</label>
      <el-input
        :model-value="reasonText" :disabled="readonly" placeholder="实测 {summary.overall}，阈值 {threshold}"
        @update:model-value="setReason(String($event))"
      />
      <div class="section-hint">可用 {保存名.字段} 引用本节点内的值。</div>

      <label class="label">带上游展示</label>
      <el-select
        :model-value="value.evidence ?? ''" :disabled="readonly" size="small" clearable
        placeholder="不引用"
        @update:model-value="patch({ evidence: $event ? String($event) : undefined })"
      >
        <el-option
          v-for="node in upstreamNodes" :key="node.id"
          :label="`${node.id} ${node.title}`" :value="node.id"
        />
      </el-select>
      <div class="section-hint">判断前把该节点的图表一并显示，执行人不用去轨迹里翻。</div>
    </template>
  </div>
</template>

<style scoped>
.recipe-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
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

.section-hint.error {
  color: var(--el-color-danger);
}

.label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.step-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 600;
  margin-top: 10px;
  color: var(--el-text-color-regular);
}

.step-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
}

.step-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.step-index {
  flex: none;
  width: 18px;
  font-size: 12px;
  text-align: center;
  color: var(--el-text-color-secondary);
}

.field-label {
  flex: none;
  width: 44px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.args :deep(textarea) {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
}
</style>
