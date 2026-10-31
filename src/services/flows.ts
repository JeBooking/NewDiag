import type {
  FlowDetail,
  FlowDoc,
  FlowMode,
  FlowSummary,
  InstanceSummary,
  LintIssue,
  ReleaseDetail,
  ReleaseSummary,
} from '@/domain/types'
import { request } from './api'

export type { ReleaseDetail }

export interface CreateFlowPayload {
  name: string
  description?: string
  mode?: FlowMode
}

export interface UpdateFlowPayload {
  name?: string
  description?: string
  mode?: FlowMode
}

export interface CommitResponse {
  revisionId: string
  updatedAt?: string
  /** 轻校验放过的 warning，阻断级问题走 422 */
  issues?: LintIssue[]
}

export interface PublishPayload {
  note?: string
}

export interface PublishResponse {
  id: string
  versionNo: number
}

export interface StartInstancePayload {
  releaseId: string
  input?: Record<string, unknown>
}

export function listFlows(): Promise<FlowSummary[]> {
  return request<FlowSummary[]>('/flows')
}

export function createFlow(payload: CreateFlowPayload): Promise<FlowDetail> {
  return request<FlowDetail>('/flows', { method: 'POST', body: JSON.stringify(payload) })
}

export function getFlow(id: string): Promise<FlowDetail> {
  return request<FlowDetail>(`/flows/${encodeURIComponent(id)}`)
}

export function updateFlow(id: string, payload: UpdateFlowPayload): Promise<FlowDetail> {
  return request<FlowDetail>(`/flows/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function checkoutFlow(id: string): Promise<FlowDetail> {
  return request<FlowDetail>(`/flows/${encodeURIComponent(id)}/checkout`, { method: 'POST' })
}

export function putWorking(id: string, doc: FlowDoc): Promise<unknown> {
  return request<unknown>(`/flows/${encodeURIComponent(id)}/working`, {
    method: 'PUT',
    body: JSON.stringify({ doc }),
  })
}

export function commitFlow(id: string, doc: FlowDoc): Promise<CommitResponse> {
  return request<CommitResponse>(`/flows/${encodeURIComponent(id)}/commit`, {
    method: 'POST',
    body: JSON.stringify({ doc }),
  })
}

export function discardFlow(id: string): Promise<unknown> {
  return request<unknown>(`/flows/${encodeURIComponent(id)}/discard`, { method: 'POST' })
}

export function listReleases(id: string): Promise<ReleaseSummary[]> {
  return request<ReleaseSummary[]>(`/flows/${encodeURIComponent(id)}/releases`)
}

export function publishFlow(id: string, payload: PublishPayload = {}): Promise<PublishResponse> {
  return request<PublishResponse>(`/flows/${encodeURIComponent(id)}/releases`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getRelease(releaseId: string): Promise<ReleaseDetail> {
  return request<ReleaseDetail>(`/releases/${encodeURIComponent(releaseId)}`)
}

export function startInstance(flowId: string, payload: StartInstancePayload): Promise<InstanceSummary> {
  return request<InstanceSummary>(`/flows/${encodeURIComponent(flowId)}/instances`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function importFlow(doc: FlowDoc): Promise<FlowDetail> {
  return request<FlowDetail>('/flows/import', {
    method: 'POST',
    body: JSON.stringify({ doc }),
  })
}
