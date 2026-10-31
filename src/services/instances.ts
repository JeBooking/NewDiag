import type { InstanceDetail, InstanceStatus, InstanceSummary, ReviewDecision } from '@/domain/types'
import { request } from './api'

export interface InstanceFilter {
  flowId?: string
  status?: InstanceStatus
}

export interface RollbackPayload {
  targetNodeId: string
  branch: string
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
