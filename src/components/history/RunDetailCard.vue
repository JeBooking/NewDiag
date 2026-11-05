<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { KIND_META } from '@/domain/kinds'
import type { DocNode, ReviewDecision, ReviewStatus, ReviewTask, RunRecord, RunStatus } from '@/domain/types'
import AttachmentList from '@/components/panels/AttachmentList.vue'
import DataPresentation from '@/components/panels/DataPresentation.vue'

const props = defineProps<{
  node: DocNode
  runs: RunRecord[]
  /** 人工节点没有 run，执行情况只能靠待办记录 */
  reviews: ReviewTask[]
}>()
const emit = defineEmits<{ (event: 'close'): void }>()

const STATUS_TEXT: Record<RunStatus, string> = {
  ok: '成功',
  failed: '失败',
  abandoned: '已放弃',
}

const STATUS_TYPE: Record<RunStatus, 'success' | 'danger' | 'info'> = {
  ok: 'success',
  failed: 'danger',
  abandoned: 'info',
}

const REVIEW_STATUS_TEXT: Record<ReviewStatus, string> = {
  pending: '等待处理',
  done: '已处理',
  cancelled: '已取消',
}

const REVIEW_STATUS_TYPE: Record<ReviewStatus, 'warning' | 'success' | 'info'> = {
  pending: 'warning',
  done: 'success',
  cancelled: 'info',
}

const DECISION_TEXT: Record<string, string> = {
  yes: '是',
  no: '否',
  done: '完成',
  rollback: '回退',
  continue: '继续',
}

/** 设计节点时填的三段说明，就是这次执行所依据的参考数据 */
const REFERENCE_ROWS = [
  { key: 'observation', label: '观察' },
  { key: 'acquisition', label: '获取' },
  { key: 'assessment', label: '评估' },
] as const

type Entry =
  | { kind: 'run'; id: string; at: string; run: RunRecord }
  | { kind: 'review'; id: string; at: string; review: ReviewTask }

const meta = computed(() => KIND_META[props.node.kind])
const activeId = ref<string | null>(null)

/** 机器执行和人工处理揉成一条时间线，节点上只露一个入口 */
const entries = computed<Entry[]>(() => {
  const list: Entry[] = [
    ...props.runs.map((run) => ({
      kind: 'run' as const,
      id: `run:${run.id}`,
      at: run.endedAt ?? run.startedAt,
      run,
    })),
    ...props.reviews.map((review) => ({
      kind: 'review' as const,
      id: `review:${review.id}`,
      at: review.createdAt,
      review,
    })),
  ]
  return list.sort((a, b) => b.at.localeCompare(a.at))
})

/** 默认看最新一条 */
const current = computed(
  () => entries.value.find((entry) => entry.id === activeId.value) ?? entries.value[0] ?? null,
)

const currentRun = computed(() => (current.value?.kind === 'run' ? current.value.run : null))
const currentReview = computed(() =>
  current.value?.kind === 'review' ? current.value.review : null,
)
const presentation = computed(() => {
  const evidence = currentRun.value?.evidence
  return evidence && typeof evidence === 'object' && 'presentation' in evidence
    ? evidence.presentation : currentReview.value?.request.presentation
})

watch(
  () => [props.runs, props.reviews],
  () => {
    // 刷新后选中项可能已不存在（回退换了代），退回最新一条
    if (activeId.value && !entries.value.some((entry) => entry.id === activeId.value)) {
      activeId.value = null
    }
  },
)

const referenceRows = computed(() => {
  const fields = props.node.fields
  return REFERENCE_ROWS.map((row) => ({
    label: row.label,
    value: (fields?.[row.key] ?? '').trim(),
  })).filter((row) => row.value)
})

const attachments = computed(() => props.node.attachments ?? [])
const hasReference = computed(
  () => referenceRows.value.length > 0 || attachments.value.length > 0,
)

const verdict = computed(() => {
  const evidence = currentRun.value?.evidence
  if (!evidence || typeof evidence !== 'object') return ''
  const branch = (evidence as { branch?: unknown }).branch
  if (branch === 'yes') return '是'
  if (branch === 'no') return '否'
  if ('prepared' in evidence) return '数据已准备，可供下游共享'
  return pretty(evidence)
})

const headTag = computed(() => {
  const run = currentRun.value
  if (run) return { text: STATUS_TEXT[run.status], type: STATUS_TYPE[run.status] }
  const review = currentReview.value
  if (review) {
    return { text: REVIEW_STATUS_TEXT[review.status], type: REVIEW_STATUS_TYPE[review.status] }
  }
  return null
})

const headMeta = computed<string[]>(() => {
  const run = currentRun.value
  if (run) {
    return [
      `第 ${run.generation} 代 · 尝试 ${run.attempt}`,
      `${formatTime(run.startedAt)} → ${run.endedAt ? formatTime(run.endedAt) : '进行中'}`,
      `耗时 ${duration(run)}`,
    ]
  }
  const review = currentReview.value
  if (!review) return []
  const rows = [reviewKindText(review), `发起 ${formatTime(review.createdAt)}`]
  if (review.decidedAt) rows.push(`处理 ${formatTime(review.decidedAt)}`)
  if (review.decidedBy) rows.push(`处理人 ${review.decidedBy}`)
  return rows
})

function reviewKindText(review: ReviewTask): string {
  if (review.request.type === 'parameters') return '现场参数'
  if (review.request.type === 'acquisition') return '提供数据'
  if (review.request.type === 'after_success') return '串行决策'
  return review.kind === 'decision' ? '人工判断' : '人工处置'
}

function decisionText(decision?: ReviewDecision): string {
  if (!decision) return ''
  const label = DECISION_TEXT[decision.result] ?? decision.result
  return decision.target ? `${label} → ${decision.target}` : label
}

function entryLabel(entry: Entry): string {
  if (entry.kind === 'run') return `第${entry.run.generation}代·${entry.run.attempt}次`
  return `${entry.review.kind === 'decision' ? '判断' : '处置'}·${shortTime(entry.at)}`
}

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

const shortTime = (value: string): string =>
  new Date(value).toLocaleTimeString('zh-CN', { hour12: false })

function duration(run: RunRecord): string {
  if (!run.endedAt) return '进行中'
  const ms = new Date(run.endedAt).getTime() - new Date(run.startedAt).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  // 调度器按秒级时间戳落库，快调用取整后就是 0，写 0ms 看着像坏了
  if (ms < 1000) return '<1s'
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const minutes = Math.floor(ms / 60000)
  const seconds = Math.round((ms % 60000) / 1000)
  return `${minutes}m ${seconds}s`
}

function pretty(value: unknown): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
</script>

<template>
  <div class="run-card" :style="{ '--kind-color': meta.color }">
    <span class="stripe" />

    <div class="head">
      <div class="head-text">
        <div class="head-row">
          <span class="kind">{{ meta.label }}</span>
          <span class="title">{{ node.title || '未命名节点' }}</span>
          <el-tag v-if="headTag" size="small" :type="headTag.type" effect="plain">
            {{ headTag.text }}
          </el-tag>
        </div>
        <div v-if="headMeta.length" class="head-meta">
          <span v-for="item in headMeta" :key="item">{{ item }}</span>
        </div>
      </div>
      <button type="button" class="close" aria-label="关闭" @click="emit('close')">×</button>
    </div>

    <div v-if="entries.length > 1" class="switcher">
      <button
        v-for="entry in entries"
        :key="entry.id"
        type="button"
        class="switch-item"
        :class="{ active: entry.id === current?.id }"
        @click="activeId = entry.id"
      >
        <span class="dot" :class="entry.kind === 'run' ? entry.run.status : entry.review.status" />
        {{ entryLabel(entry) }}
      </button>
    </div>

    <div v-if="currentRun" class="body">
      <div v-if="currentRun.error" class="block">
        <div class="label is-error">错误</div>
        <pre class="error-text">{{ currentRun.error }}</pre>
      </div>

      <div v-if="currentRun.input !== undefined" class="block">
        <div class="label">输入</div>
        <pre>{{ pretty(currentRun.input) }}</pre>
      </div>

      <div v-if="hasReference" class="block">
        <div class="label">参考数据</div>
        <div class="ref">
          <div v-for="row in referenceRows" :key="row.label" class="ref-row">
            <span class="ref-label">{{ row.label }}</span>
            <span class="ref-text">{{ row.value }}</span>
          </div>
          <AttachmentList v-if="attachments.length" :attachments="attachments" dense />
        </div>
      </div>

      <div v-if="currentRun.output !== undefined" class="block">
        <div class="label">输出</div>
        <pre>{{ pretty(currentRun.output) }}</pre>
      </div>
      <DataPresentation :value="presentation" />

      <div v-if="verdict" class="block">
        <div class="label">判定</div>
        <div class="verdict">{{ verdict }}</div>
      </div>
    </div>

    <div v-else-if="currentReview" class="body">
      <DataPresentation :value="presentation" />
      <div class="block">
        <div class="label">待办</div>
        <div class="request">
          <div class="request-title">{{ currentReview.request.title || node.title }}</div>
          <div v-if="currentReview.request.detail" class="request-text">
            {{ currentReview.request.detail }}
          </div>
          <div v-if="currentReview.request.instructions" class="request-hint">
            {{ currentReview.request.instructions }}
          </div>
          <AttachmentList
            v-if="currentReview.request.attachments?.length"
            :attachments="currentReview.request.attachments"
            dense
          />
        </div>
      </div>

      <div class="block">
        <div class="label">处理结果</div>
        <div v-if="currentReview.decision" class="decision">
          <span class="decision-result">{{ decisionText(currentReview.decision) }}</span>
          <span v-if="currentReview.decision.reason" class="decision-reason">
            {{ currentReview.decision.reason }}
          </span>
        </div>
        <div v-else class="decision is-pending">等待人工处理</div>
        <pre v-if="currentReview.decision?.data">{{ pretty(currentReview.decision.data) }}</pre>
      </div>

      <div v-if="hasReference" class="block">
        <div class="label">参考数据</div>
        <div class="ref">
          <div v-for="row in referenceRows" :key="row.label" class="ref-row">
            <span class="ref-label">{{ row.label }}</span>
            <span class="ref-text">{{ row.value }}</span>
          </div>
          <AttachmentList v-if="attachments.length" :attachments="attachments" dense />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.run-card {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 560px;
  max-width: 100%;
  max-height: 100%;
  padding-left: 4px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
  box-shadow: 0 12px 32px rgb(15 23 42 / 18%);
  overflow: hidden;
}

.stripe {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  width: 4px;
  background: var(--kind-color);
}

.head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 12px 10px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.head-text {
  flex: 1;
  min-width: 0;
}

.head-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.kind {
  padding: 0 5px;
  border-radius: 3px;
  font-size: 11px;
  color: var(--kind-color);
  background: color-mix(in srgb, var(--kind-color) 12%, transparent);
}

.title {
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.head-meta {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.close {
  flex: none;
  padding: 0 4px;
  border: none;
  background: none;
  font-size: 18px;
  line-height: 1;
  color: var(--el-text-color-secondary);
  cursor: pointer;
}

.close:hover {
  color: var(--el-color-primary);
}

.switcher {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 8px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.switch-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  background: none;
  font: inherit;
  font-size: 11px;
  color: var(--el-text-color-regular);
  cursor: pointer;
}

.switch-item:hover {
  border-color: var(--el-color-primary-light-5);
}

.switch-item.active {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-text-color-placeholder);
}

.dot.ok,
.dot.done {
  background: var(--el-color-success);
}

.dot.failed {
  background: var(--el-color-danger);
}

.dot.pending {
  background: var(--el-color-warning);
}

.body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 16px 16px;
  overflow: auto;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.label.is-error {
  color: var(--el-color-danger);
}

pre {
  margin: 0;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.error-text {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.ref {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
}

.ref-row {
  display: flex;
  gap: 8px;
  font-size: 11px;
  line-height: 1.6;
}

.ref-label {
  flex: none;
  width: 28px;
  color: var(--el-text-color-secondary);
}

.ref-text {
  color: var(--el-text-color-primary);
  word-break: break-word;
}

.verdict {
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  background: var(--el-fill-color-light);
}

.request {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
}

.request-title {
  font-size: 12px;
  font-weight: 600;
}

.request-text {
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}

.request-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.decision {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
}

.decision-result {
  font-size: 12px;
  font-weight: 600;
}

.decision-reason {
  font-size: 11px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.decision.is-pending {
  font-size: 12px;
  color: var(--el-color-warning);
}
</style>
