import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { DocEdge, DocNode, FlowDetail, FlowDoc, FlowMode, LintIssue } from '@/domain/types'
import { CURRENT_USER, isApiError } from '@/services/api'
import {
  checkoutFlow,
  commitFlow,
  discardFlow,
  getFlow,
  publishFlow,
  putWorking,
  type PublishResponse,
} from '@/services/flows'

const AUTOSAVE_DELAY = 2000

/** 后端在没有 revision 时可能返回 {}，补齐成画布能消费的空文档 */
function coerceDoc(raw: unknown, name: string, mode: FlowMode): FlowDoc {
  const record = raw && typeof raw === 'object' ? (raw as Partial<FlowDoc>) : {}
  return {
    format: 1,
    name: typeof record.name === 'string' && record.name ? record.name : name,
    mode: record.mode === 'serial' ? 'serial' : record.mode === 'parallel' ? 'parallel' : mode,
    nodes: Array.isArray(record.nodes) ? record.nodes : [],
    edges: Array.isArray(record.edges) ? record.edges : [],
  }
}

/**
 * 旧文档没有开始节点时在内存里补一个：启动参数需要一个稳定落点。
 * 同时按迁移脚本的口径把它连到所有根节点，否则发布时会报「开始节点至少需要一条出线」。
 * 只改内存、标记 dirty，等用户保存时才落库。
 */
function ensureStartNode(source: FlowDoc): { doc: FlowDoc; added: boolean } {
  if (source.nodes.some((node) => node.kind === 'start')) {
    return { doc: source, added: false }
  }
  let startId = 'start'
  let seq = 1
  while (source.nodes.some((node) => node.id === startId)) {
    startId = `start${seq}`
    seq += 1
  }
  const xs = source.nodes.map((node) => node.position.x)
  const start: DocNode = {
    id: startId,
    kind: 'start',
    title: '开始',
    detail: '',
    // 固定在左上角节点面板下方，避免自动补出来的入口被浮层压住
    position: {
      x: xs.length ? Math.max(60, Math.min(...xs) - 260) : 60,
      y: 220,
    },
  }

  const incoming = new Set(source.edges.map((edge) => edge.to))
  const roots = source.nodes
    .filter((node) => !incoming.has(node.id))
    .map((node) => node.id)
    .sort()
  const usedEdgeIds = new Set(source.edges.map((edge) => edge.id))
  const edges: DocEdge[] = roots.map((to, index) => {
    let edgeId = `e-${startId}-${index + 1}`
    let suffix = 1
    while (usedEdgeIds.has(edgeId)) {
      edgeId = `e-${startId}-${index + 1}-${suffix}`
      suffix += 1
    }
    usedEdgeIds.add(edgeId)
    return { id: edgeId, from: startId, to, order: index + 1 }
  })

  return {
    doc: { ...source, nodes: [start, ...source.nodes], edges: [...source.edges, ...edges] },
    added: true,
  }
}

export const useEditorStore = defineStore('editor', () => {
  const flow = ref<FlowDetail | null>(null)
  const doc = ref<FlowDoc | null>(null)
  const readonly = ref(false)
  /** 是否已拿到编辑锁；进页面不算，首次改动才 checkout */
  const hasLock = ref(false)
  const dirty = ref(false)
  const loading = ref(false)
  const saving = ref(false)
  const publishing = ref(false)
  const selectedNodeId = ref<string | null>(null)
  const issues = ref<LintIssue[]>([])
  const saveIssues = ref<LintIssue[]>([])
  const lastSavedAt = ref<string | null>(null)

  let autosaveTimer: ReturnType<typeof setTimeout> | null = null
  /** 进行中的检出请求；连续改动共用同一次 checkout */
  let lockPromise: Promise<void> | null = null

  const editable = computed(() => !readonly.value && Boolean(doc.value))
  const selectedNode = computed(
    () => doc.value?.nodes.find((node) => node.id === selectedNodeId.value) ?? null,
  )

  function clearAutosave(): void {
    if (autosaveTimer) clearTimeout(autosaveTimer)
    autosaveTimer = null
  }

  function reset(): void {
    clearAutosave()
    flow.value = null
    doc.value = null
    readonly.value = false
    hasLock.value = false
    lockPromise = null
    dirty.value = false
    saving.value = false
    publishing.value = false
    selectedNodeId.value = null
    issues.value = []
    saveIssues.value = []
    lastSavedAt.value = null
  }

  async function open(id: string): Promise<void> {
    reset()
    loading.value = true
    try {
      const detail = await getFlow(id)
      // 进页面只读文档、不占锁：别人正在编辑才转只读，自己遗留的草稿视为已持有
      const mine = detail.editingBy === CURRENT_USER
      readonly.value = Boolean(detail.editingBy) && !mine
      hasLock.value = mine && Boolean(detail.workingRevisionId)
      flow.value = detail
      const prepared = ensureStartNode(coerceDoc(detail.doc, detail.name, detail.mode))
      doc.value = prepared.doc
      if (prepared.added && !readonly.value) dirty.value = true
    } finally {
      loading.value = false
    }
  }

  /** 首次改动才拿锁：失败转只读，避免页面在无锁状态下继续写 */
  async function ensureLock(): Promise<void> {
    if (!flow.value) throw new Error('流程尚未加载')
    if (hasLock.value) return
    if (readonly.value) throw new Error('当前流程只读')
    const id = flow.value.id
    if (lockPromise) return lockPromise
    lockPromise = (async () => {
      try {
        const detail = await checkoutFlow(id)
        if (flow.value?.id !== id) return
        flow.value = detail
        hasLock.value = true
      } catch (error) {
        if (flow.value?.id === id) {
          readonly.value = true
          try {
            flow.value = await getFlow(id)
          } catch {
            // 读不到占用者也保持只读，横幅退化成「其他用户」
          }
        }
        throw error
      } finally {
        lockPromise = null
      }
    })()
    return lockPromise
  }

  function applyDoc(next: FlowDoc): void {
    if (!doc.value) return
    // 画布的 Delete 键也能删节点，开始节点是入口，丢了就补回来
    const start = doc.value.nodes.find((node) => node.kind === 'start')
    let resolved = next
    if (start && !next.nodes.some((node) => node.kind === 'start')) {
      resolved = {
        ...next,
        nodes: [...next.nodes, start],
        edges: next.edges.filter((edge) => edge.from !== start.id && edge.to !== start.id),
      }
    }
    doc.value = resolved
    if (readonly.value) return
    dirty.value = true
    // 改动即抢锁，抢不到会转只读；autosave 复用同一个 Promise
    void ensureLock().catch(() => {})
    scheduleAutosave()
  }

  async function autosave(): Promise<void> {
    if (!flow.value || !doc.value || readonly.value || saving.value) return
    if (!dirty.value) return
    try {
      await ensureLock()
      await putWorking(flow.value.id, doc.value)
      lastSavedAt.value = new Date().toISOString()
    } catch {
      // 暂存失败不打断编辑，提交时会再次校验并提示
    }
  }

  function scheduleAutosave(): void {
    clearAutosave()
    autosaveTimer = setTimeout(() => {
      autosaveTimer = null
      void autosave()
    }, AUTOSAVE_DELAY)
  }

  async function save(): Promise<void> {
    if (!flow.value || !doc.value) return
    clearAutosave()
    saving.value = true
    issues.value = []
    try {
      await ensureLock()
      const id = flow.value.id
      const result = await commitFlow(id, doc.value)
      if (flow.value?.id !== id) return
      // 提交即释放锁，不再自动重检：下次改动时再 checkout，列表页也不该显示编辑中
      flow.value = result
      hasLock.value = false
      dirty.value = false
      saveIssues.value = result.issues ?? []
      lastSavedAt.value = new Date().toISOString()
      if (result.doc) doc.value = coerceDoc(result.doc, result.name, result.mode)
    } catch (error) {
      if (isApiError(error) && error.issues.length > 0) issues.value = error.issues
      throw error
    } finally {
      saving.value = false
    }
  }

  async function publish(note?: string): Promise<PublishResponse> {
    if (!flow.value) throw new Error('流程尚未加载')
    publishing.value = true
    issues.value = []
    try {
      const result = await publishFlow(flow.value.id, note ? { note } : {})
      saveIssues.value = []
      return result
    } catch (error) {
      if (isApiError(error) && error.issues.length > 0) issues.value = error.issues
      throw error
    } finally {
      publishing.value = false
    }
  }

  async function discard(): Promise<void> {
    if (!flow.value) return
    const id = flow.value.id
    clearAutosave()
    // 检出可能还在路上，先等它落地再决定要不要调后端
    if (lockPromise) await lockPromise.catch(() => undefined)
    if (hasLock.value) await discardFlow(id)
    reset()
  }

  /** 名称/模式的修改走 PATCH，成功后同步本地，避免整页重开丢掉选中态 */
  function patchFlowMeta(patch: Partial<FlowDetail>): void {
    if (!flow.value) return
    flow.value = { ...flow.value, ...patch }
    if (patch.name && doc.value) doc.value = { ...doc.value, name: patch.name }
    if (patch.mode && doc.value) doc.value = { ...doc.value, mode: patch.mode }
  }

  function clearIssues(): void {
    issues.value = []
  }

  function clearSaveIssues(): void {
    saveIssues.value = []
  }

  function close(): void {
    clearAutosave()
  }

  return {
    flow,
    doc,
    readonly,
    dirty,
    loading,
    saving,
    publishing,
    selectedNodeId,
    selectedNode,
    editable,
    issues,
    saveIssues,
    lastSavedAt,
    open,
    applyDoc,
    save,
    publish,
    discard,
    patchFlowMeta,
    scheduleAutosave,
    clearIssues,
    clearSaveIssues,
    reset,
    close,
  }
})
