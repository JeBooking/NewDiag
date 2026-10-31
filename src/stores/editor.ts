import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { FlowDetail, FlowDoc, FlowMode, LintIssue } from '@/domain/types'
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

export const useEditorStore = defineStore('editor', () => {
  const flow = ref<FlowDetail | null>(null)
  const doc = ref<FlowDoc | null>(null)
  const readonly = ref(false)
  const dirty = ref(false)
  const loading = ref(false)
  const saving = ref(false)
  const publishing = ref(false)
  const selectedNodeId = ref<string | null>(null)
  const issues = ref<LintIssue[]>([])
  const saveWarnings = ref<LintIssue[]>([])
  const lastSavedAt = ref<string | null>(null)

  let autosaveTimer: ReturnType<typeof setTimeout> | null = null

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
    dirty.value = false
    saving.value = false
    publishing.value = false
    selectedNodeId.value = null
    issues.value = []
    saveWarnings.value = []
    lastSavedAt.value = null
  }

  async function open(id: string): Promise<void> {
    reset()
    loading.value = true
    try {
      let detail = await getFlow(id)
      if (!detail.editingBy) {
        // 抢占失败说明别人刚拿走锁，退回只读而不是报错阻断页面
        try {
          detail = await checkoutFlow(id)
        } catch {
          readonly.value = true
          try {
            detail = await getFlow(id)
          } catch {
            // 读取占用者失败就沿用旧数据，横幅会退化成「其他用户」
          }
        }
      } else {
        readonly.value = detail.editingBy !== CURRENT_USER
      }
      flow.value = detail
      doc.value = coerceDoc(detail.doc, detail.name, detail.mode)
    } finally {
      loading.value = false
    }
  }

  function applyDoc(next: FlowDoc): void {
    if (!doc.value) return
    doc.value = next
    if (readonly.value) return
    dirty.value = true
    scheduleAutosave()
  }

  async function autosave(): Promise<void> {
    if (!flow.value || !doc.value || readonly.value || saving.value) return
    if (!dirty.value) return
    try {
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
      const result = await commitFlow(flow.value.id, doc.value)
      saveWarnings.value = (result.issues ?? []).filter((issue) => issue.level === 'warning')
      dirty.value = false
      lastSavedAt.value = new Date().toISOString()
      // 提交会释放编辑锁，立即重新检出以便继续修改
      try {
        const detail = await checkoutFlow(flow.value.id)
        flow.value = detail
        readonly.value = false
        if (detail.doc) doc.value = coerceDoc(detail.doc, detail.name, detail.mode)
      } catch {
        readonly.value = true
      }
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
      saveWarnings.value = []
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
    await discardFlow(id)
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

  function clearSaveWarnings(): void {
    saveWarnings.value = []
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
    saveWarnings,
    lastSavedAt,
    open,
    applyDoc,
    save,
    publish,
    discard,
    patchFlowMeta,
    scheduleAutosave,
    clearIssues,
    clearSaveWarnings,
    reset,
    close,
  }
})
