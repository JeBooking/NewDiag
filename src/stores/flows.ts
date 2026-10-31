import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { FlowDetail, FlowSummary } from '@/domain/types'
import { errorText } from '@/services/api'
import {
  createFlow,
  getFlow,
  listFlows,
  type CreateFlowPayload,
} from '@/services/flows'

export const useFlowsStore = defineStore('flows', () => {
  const items = ref<FlowSummary[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  // 详情缓存只用于减少重复请求，编辑页仍以服务端检出状态为准
  const details = new Map<string, FlowDetail>()

  async function load(force = false): Promise<void> {
    if (loading.value) return
    if (!force && items.value.length > 0) return
    loading.value = true
    error.value = null
    try {
      items.value = await listFlows()
    } catch (err) {
      error.value = errorText(err, '加载流程列表失败')
    } finally {
      loading.value = false
    }
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
    await load(true)
    return created
  }

  return { items, loading, error, load, fetchDetail, cacheDetail, invalidate, create }
})
