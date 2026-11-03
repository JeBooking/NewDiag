import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  EdgeTrail,
  FlowDoc,
  InstanceDetail,
  InstanceSummary,
  NodeVisualStatus,
  ReviewDecision,
  RunRecord,
  Token,
  TokenStatus,
} from '@/domain/types'
import { errorText } from '@/services/api'
import { getRelease, getFlow } from '@/services/flows'
import {
  getInstance,
  listInstances,
  rollbackInstance,
  submitReview,
  terminateInstance,
  type InstanceFilter,
  type RollbackPayload,
} from '@/services/instances'
import { subscribeInstance } from '@/services/sse'

/** 同一节点有多个 token（回退产生新代）时，活跃状态优先展示 */
const TOKEN_PRIORITY: Record<TokenStatus, number> = {
  running: 5,
  waiting: 4,
  queued: 3,
  failed: 2,
  done: 1,
  abandoned: 0,
}

const TOKEN_VISUAL: Record<TokenStatus, NodeVisualStatus> = {
  running: 'running',
  waiting: 'waiting',
  // 排队和执行中必须分开，否则「即将执行」的节点和「没进流程」的节点长得一样
  queued: 'queued',
  done: 'success',
  failed: 'failed',
  abandoned: 'pending',
}

/** 发布快照可能缺失或为空对象，只有带 nodes 数组的才算可用文档 */
function usableDoc(raw: unknown): FlowDoc | null {
  if (!raw || typeof raw !== 'object') return null
  const record = raw as Partial<FlowDoc>
  if (!Array.isArray(record.nodes) || !Array.isArray(record.edges)) return null
  return record as FlowDoc
}

export const useInstancesStore = defineStore('instances', () => {
  const list = ref<InstanceSummary[]>([])
  const listLoading = ref(false)
  const listError = ref<string | null>(null)

  const detail = ref<InstanceDetail | null>(null)
  const doc = ref<FlowDoc | null>(null)
  /** 画布 doc 的来源：发布快照优先，在线版本只是兜底 */
  const docSource = ref<'release' | 'flow' | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  let unsubscribe: (() => void) | null = null
  let docReleaseId: string | null = null
  let refreshing = false
  let refreshAgain = false

  function applyDetail(next: InstanceDetail): void {
    if (detail.value?.id !== next.id) return
    // 终止不可恢复，忽略终止前发出的请求晚到的旧状态。
    if (detail.value.status === 'terminated' && next.status !== 'terminated') return
    detail.value = next
  }

  const pendingReviews = computed(() =>
    (detail.value?.reviews ?? [])
      .filter((review) => review.status === 'pending')
      .slice()
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt)),
  )

  const nodeStatus = computed<Record<string, NodeVisualStatus>>(() => {
    const best: Record<string, { priority: number; generation: number; status: TokenStatus }> = {}
    for (const token of detail.value?.tokens ?? []) {
      const priority = TOKEN_PRIORITY[token.status]
      const current = best[token.nodeId]
      const better =
        !current ||
        priority > current.priority ||
        (priority === current.priority && token.generation > current.generation)
      if (better) best[token.nodeId] = { priority, generation: token.generation, status: token.status }
    }
    const result: Record<string, NodeVisualStatus> = {}
    for (const [nodeId, entry] of Object.entries(best)) {
      result[nodeId] = TOKEN_VISUAL[entry.status]
    }
    return result
  })

  const activeTokenCount = computed(
    () =>
      (detail.value?.tokens ?? []).filter(
        (token) => token.status === 'running' || token.status === 'waiting' || token.status === 'queued',
      ).length,
  )

  /**
   * 走过的边：靠 token 的父子关系还原，parent_token_id 由调度器在投递后继时写入。
   * 不能按「两端节点都执行过」推断分支路径，同一个排查节点的 yes/no 两条边会被同时点亮。
   */
  const walkedEdges = computed<Record<string, EdgeTrail>>(() => {
    const result: Record<string, EdgeTrail> = {}
    const tokens = detail.value?.tokens ?? []
    const edges = doc.value?.edges ?? []
    if (!tokens.length || !edges.length) return result
    const byId = new Map(tokens.map((token) => [token.id, token]))
    for (const token of tokens) {
      const parent = token.parentTokenId ? byId.get(token.parentTokenId) : undefined
      if (!parent) continue
      const edge = edges.find(
        (item) =>
          item.from === parent.nodeId &&
          item.to === token.nodeId &&
          (item.branch ?? null) === (token.branch ?? null),
      )
      if (!edge) continue
      if (result[edge.id] === 'taken') continue
      result[edge.id] = token.status === 'abandoned' ? 'dropped' : 'taken'
    }
    return result
  })

  function runSequence(token: Token): RunRecord[] {
    return (detail.value?.runs ?? [])
      .filter((run) => run.tokenId === token.id)
      .slice()
      .sort((a, b) => a.startedAt.localeCompare(b.startedAt))
  }

  async function loadList(filter: InstanceFilter = {}): Promise<void> {
    listLoading.value = true
    listError.value = null
    try {
      list.value = await listInstances(filter)
    } catch (err) {
      listError.value = errorText(err, '加载执行记录失败')
    } finally {
      listLoading.value = false
    }
  }

  async function ensureDoc(): Promise<void> {
    const current = detail.value
    if (!current) return
    if (doc.value && docReleaseId === current.releaseId) return
    docReleaseId = current.releaseId
    try {
      const release = await getRelease(current.releaseId)
      const snapshot = usableDoc(release.doc)
      if (snapshot) {
        doc.value = snapshot
        docSource.value = 'release'
        return
      }
    } catch {
      // 版本详情不可用时退回在线版本，只为让画布有图可看
    }
    try {
      const flow = await getFlow(current.flowId)
      doc.value = usableDoc(flow.doc)
      docSource.value = doc.value ? 'flow' : null
    } catch {
      doc.value = null
      docSource.value = null
    }
  }

  async function refresh(id?: string): Promise<void> {
    const target = id ?? detail.value?.id
    if (!target) return
    if (refreshing) {
      refreshAgain = true
      return
    }
    refreshing = true
    try {
      applyDetail(await getInstance(target))
      error.value = null
      await ensureDoc()
    } catch (err) {
      error.value = errorText(err, '加载实例详情失败')
    } finally {
      refreshing = false
      if (refreshAgain) {
        refreshAgain = false
        void refresh(target)
      }
    }
  }

  function connect(id: string): void {
    unsubscribe?.()
    unsubscribe = subscribeInstance(id, () => {
      void refresh(id)
    })
  }

  async function open(id: string): Promise<void> {
    loading.value = true
    error.value = null
    detail.value = null
    doc.value = null
    docSource.value = null
    docReleaseId = null
    try {
      detail.value = await getInstance(id)
      await ensureDoc()
      connect(id)
    } finally {
      loading.value = false
    }
  }

  async function decide(reviewId: string, decision: ReviewDecision): Promise<void> {
    if (!detail.value) return
    applyDetail(await submitReview(detail.value.id, reviewId, decision))
  }

  async function rollback(payload: RollbackPayload): Promise<void> {
    if (!detail.value) return
    applyDetail(await rollbackInstance(detail.value.id, payload))
  }

  async function terminate(): Promise<void> {
    if (!detail.value) return
    applyDetail(await terminateInstance(detail.value.id))
    error.value = null
  }

  function close(): void {
    unsubscribe?.()
    unsubscribe = null
    detail.value = null
    doc.value = null
    docSource.value = null
    docReleaseId = null
    error.value = null
  }

  return {
    list,
    listLoading,
    listError,
    detail,
    doc,
    docSource,
    loading,
    error,
    pendingReviews,
    nodeStatus,
    activeTokenCount,
    walkedEdges,
    runSequence,
    loadList,
    open,
    refresh,
    decide,
    rollback,
    terminate,
    close,
  }
})
