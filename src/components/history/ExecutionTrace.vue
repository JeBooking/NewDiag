<script setup lang="ts">
import { computed } from 'vue'
import type { FlowDoc, ReviewTask, RunRecord, Token } from '@/domain/types'
import { runOutput, runSource } from '@/services/instances'

const props = defineProps<{
  tokens: Token[]
  runs: RunRecord[]
  reviews: ReviewTask[]
  doc?: FlowDoc | null
}>()

type TagType = 'primary' | 'success' | 'warning' | 'danger' | 'info'

interface Entry {
  id: string
  at: string
  title: string
  tag: string
  type: TagType
  meta: string
  note?: string
  noteType?: 'danger' | 'warning'
}

function title(nodeId: string): string {
  return props.doc?.nodes.find((node) => node.id === nodeId)?.title || nodeId
}

function time(value?: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

/** 时间戳格式不统一（有微秒+00:00，也有 Z），按字符串比会错序，一律换成毫秒 */
function stamp(value?: string | null): number {
  const parsed = value ? new Date(value).getTime() : 0
  return Number.isNaN(parsed) ? 0 : parsed
}

const BRANCH_TEXT: Record<string, string> = { yes: '是', no: '否', default: '默认' }

const tokenById = computed(() => {
  const map = new Map<string, Token>()
  for (const token of props.tokens) map.set(token.id, token)
  return map
})

function runEntry(run: RunRecord): Entry {
  const node = title(run.nodeId)
  const meta = `第 ${run.generation} 代 · 尝试 ${run.attempt}`
  const error = String(run.error ?? '')

  // 手动模式下的人工选择：没有业务调用，只记录当时选了什么
  if (runSource(run) === 'manual') {
    const manualMeta = `第 ${run.generation} 代 · 人工`
    if (run.status !== 'ok') {
      return {
        id: run.id, at: run.startedAt, title: node, tag: '人工·未走通', type: 'danger',
        meta: manualMeta, note: error || '所选分支没有后继', noteType: 'warning',
      }
    }
    const output = runOutput(run)
    let note: string | undefined
    if (output.choice) {
      note = `选择「${title(output.choice)}」`
    } else if (output.result === 'yes' || output.result === 'no') {
      note = `判定：${output.result === 'yes' ? '是' : '否'}${
        output.reason ? ` · 理由：${output.reason}` : ''}`
    }
    const evidence = (run.evidence ?? {}) as { detail?: unknown }
    if (!note && typeof evidence.detail === 'string' && evidence.detail) note = evidence.detail
    return { id: run.id, at: run.startedAt, title: node, tag: '人工', type: 'info', meta: manualMeta, note }
  }

  if (run.status === 'rollback') {
    const input = (run.input ?? {}) as { reason?: unknown; branch?: unknown }
    const branch = typeof input.branch === 'string' ? BRANCH_TEXT[input.branch] ?? input.branch : ''
    const reason = typeof input.reason === 'string' && input.reason ? input.reason : '未填原因'
    return {
      id: run.id, at: run.startedAt, title: `${node} · 回退`, tag: '回退', type: 'warning', meta,
      note: `复用这次到访${branch ? `，按「${branch}」继续` : ''} · 原因：${reason}`,
      noteType: 'warning',
    }
  }

  if (run.status === 'ok') {
    const evidence = (run.evidence ?? {}) as { reason?: unknown }
    return {
      id: run.id, at: run.startedAt, title: node, tag: '完成', type: 'success', meta,
      note: typeof evidence.reason === 'string' && evidence.reason ? evidence.reason : undefined,
    }
  }

  if (error.startsWith('route/')) {
    return {
      id: run.id, at: run.startedAt, title: node, tag: '排查失败', type: 'danger', meta,
      note: '所选分支没有后继，这条思路不成立', noteType: 'warning',
    }
  }
  if (error.startsWith('execution/')) {
    const tail = error.split('\n').filter(Boolean).at(-1) ?? error
    return { id: run.id, at: run.startedAt, title: node, tag: '执行报错', type: 'danger', meta, note: tail, noteType: 'danger' }
  }
  return { id: run.id, at: run.startedAt, title: node, tag: '失败', type: 'danger', meta, note: error || undefined, noteType: 'danger' }
}

const REVIEW_TAG: Record<string, { tag: string; type: TagType }> = {
  decision: { tag: '人工判断', type: 'primary' },
  action: { tag: '处置', type: 'success' },
  parameters: { tag: '现场参数', type: 'info' },
  after_success: { tag: '串行决策', type: 'warning' },
  judgment: { tag: '人工判断', type: 'primary' },
  failure: { tag: '执行报错', type: 'danger' },
}

const RESULT_TEXT: Record<string, string> = {
  yes: '是', no: '否', done: '完成', continue: '继续', stop: '结束', rollback: '回退',
}

/** 手动模式下每次人工选择都同时写了 run 和待办，轨迹只留 run，避免同一动作出现两条 */
const manualRunKeys = computed(() => {
  const keys = new Set<string>()
  for (const run of props.runs) {
    if (runSource(run) === 'manual') keys.add(`${run.tokenId}:${run.nodeId}`)
  }
  return keys
})

function reviewEntry(review: ReviewTask): Entry | null {
  if (review.status !== 'done' || !review.decision) return null
  if (manualRunKeys.value.has(`${review.tokenId ?? ''}:${review.nodeId}`)) return null
  const failure = review.request.type === 'failure'
  const meta = `第 ${tokenById.value.get(review.tokenId ?? '')?.generation ?? 1} 代`
  const label = REVIEW_TAG[review.request.type ?? review.kind] ?? { tag: '人工操作', type: 'info' as TagType }
  const result = RESULT_TEXT[review.decision.result] ?? review.decision.result
  return {
    id: review.id,
    at: review.decidedAt ?? review.createdAt,
    title: failure ? `${title(review.nodeId)} · 结束流程` : `${title(review.nodeId)} · ${label.tag}：${result}`,
    tag: label.tag,
    type: label.type,
    meta,
    note: review.decision.reason || undefined,
    noteType: failure ? 'danger' : undefined,
  }
}

const items = computed<Entry[]>(() => {
  const entries: Entry[] = props.runs.map(runEntry)
  for (const review of props.reviews) {
    const entry = reviewEntry(review)
    if (entry) entries.push(entry)
  }
  // 手动模式一次点击可能在同一秒内推进多个节点，秒级时间戳相同，
  // 这时保持接口返回的先后顺序，别用 id 打散成随机次序
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => stamp(a.entry.at) - stamp(b.entry.at) || a.index - b.index)
    .map((item) => item.entry)
})
</script>

<template>
  <div class="trace">
    <el-empty v-if="!items.length" description="还没有节点动作" :image-size="60" />
    <el-timeline v-else>
      <el-timeline-item
        v-for="item in items"
        :key="item.id"
        :timestamp="time(item.at)"
        placement="top"
        :type="item.type === 'success' ? 'success' : item.type === 'danger' ? 'danger' : item.type === 'warning' ? 'warning' : 'primary'"
      >
        <div class="row">
          <span class="name">{{ item.title }}</span>
          <el-tag size="small" :type="item.type" effect="plain">{{ item.tag }}</el-tag>
          <span class="meta">{{ item.meta }}</span>
        </div>
        <div v-if="item.note" class="note" :class="item.noteType">{{ item.note }}</div>
      </el-timeline-item>
    </el-timeline>
  </div>
</template>

<style scoped>
.trace {
  padding: 4px 0;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.name {
  font-size: 12px;
  font-weight: 500;
}

.meta {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.note {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
}

.note.warning {
  color: var(--el-color-warning);
}

.note.danger {
  color: var(--el-color-danger);
}
</style>
