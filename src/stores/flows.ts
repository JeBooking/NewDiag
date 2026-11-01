import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { FlowDetail, FlowSummary } from '@/domain/types'
import { errorText } from '@/services/api'
import {
  createFlow,
  getFlow,
  listFlows,
  type CreateFlowPayload,
  type ListFlowsFilter,
} from '@/services/flows'

export const useFlowsStore = defineStore('flows', () => {
  const items = ref<FlowSummary[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  /** 最近一次列表请求的筛选条件，刷新时沿用 */
  const filter = ref<ListFlowsFilter>({})

  // 详情缓存只用于减少重复请求，编辑页仍以服务端检出状态为准
  const details = new Map<string, FlowDetail>()

  // 请求进行中又换了筛选条件时记下来，等当前请求结束再补一次，避免点击被丢掉
  let pending: ListFlowsFilter | null = null

  async function load(next: ListFlowsFilter = {}, force = false): Promise<void> {
    if (loading.value) {
      pending = { ...next }
      return
    }
    const changed = next.folderId !== filter.value.folderId || next.search !== filter.value.search
    if (!force && !changed && items.value.length > 0) return
    filter.value = { ...next }
    loading.value = true
    error.value = null
    try {
      items.value = await listFlows(filter.value)
    } catch (err) {
      error.value = errorText(err, '加载流程列表失败')
    } finally {
      loading.value = false
      const queued = pending
      pending = null
      if (queued) void load(queued, true)
    }
  }

  function refresh(): Promise<void> {
    return load(filter.value, true)
  }

  async function fetchDetail(id: string, force = false): Promise<FlowDetail> {
    if (!force) {
      const hit = details.get(id)
      if (hit) return hit
    }
    const detail = await getFlow(id)
    details.set(id, detail)
    return detail
  }

  function cacheDetail(detail: FlowDetail): void {
    details.set(detail.id, detail)
  }

  function invalidate(id?: string): void {
    if (id) details.delete(id)
    else details.clear()
  }

  async function create(payload: CreateFlowPayload): Promise<FlowDetail> {
    const created = await createFlow(payload)
    await refresh()
    return created
  }

  return { items, loading, error, filter, load, refresh, fetchDetail, cacheDetail, invalidate, create }
})
