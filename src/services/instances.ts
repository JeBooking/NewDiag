import type { InstanceDetail, InstanceStatus, InstanceSummary, ReviewDecision } from '@/domain/types'
import { request } from '@/services/api'

export interface InstanceFilter {
  flowId?: string
  status?: InstanceStatus
}

export interface RollbackPayload {
  targetNodeId: string
  targetTokenId?: string
  mode?: 'branch' | 'retry'
  branch?: string
  reason?: string
}

function buildQuery(filter: InstanceFilter): string {
  const params = new URLSearchParams()
  if (filter.flowId) params.set('flowId', filter.flowId)
  if (filter.status) params.set('status', filter.status)
  const query = params.toString()
  return query ? `?${query}` : ''
}

export function listInstances(filter: InstanceFilter = {}): Promise<InstanceSummary[]> {
  return request<InstanceSummary[]>(`/instances${buildQuery(filter)}`)
}

export function getInstance(id: string): Promise<InstanceDetail> {
  return request<InstanceDetail>(`/instances/${encodeURIComponent(id)}`)
}

export function instanceCodeExportUrl(id: string): string {
  return `/api/instances/${encodeURIComponent(id)}/code-export`
}

export function terminateInstance(id: string): Promise<InstanceDetail> {
  return request<InstanceDetail>(`/instances/${encodeURIComponent(id)}/terminate`, {
    method: 'POST',
  })
}

export function submitReview(
  instanceId: string,
  reviewId: string,
  decision: ReviewDecision,
): Promise<InstanceDetail> {
  return request<InstanceDetail>(
    `/instances/${encodeURIComponent(instanceId)}/reviews/${encodeURIComponent(reviewId)}`,
    { method: 'POST', body: JSON.stringify(decision) },
  )
}

export function rollbackInstance(instanceId: string, payload: RollbackPayload): Promise<InstanceDetail> {
  return request<InstanceDetail>(`/instances/${encodeURIComponent(instanceId)}/rollback`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
