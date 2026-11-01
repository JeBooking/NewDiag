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
  /** null 表示移出目录（未分类） */
  folderId?: string | null
}

export interface ListFlowsFilter {
  /** 目录 id；传 root 表示未分类，不传表示全部 */
  folderId?: string
  search?: string
}

/** 提交成功返回完整流程详情，前端据此刷新流程元信息，无需再请求一次 */
export interface CommitResponse extends FlowDetail {
  revisionId: string | null
  /** 保存只提示、不阻断的问题 */
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

export function listFlows(filter: ListFlowsFilter = {}): Promise<FlowSummary[]> {
  const params = new URLSearchParams()
  if (filter.folderId) params.set('folderId', filter.folderId)
  const search = filter.search?.trim()
  if (search) params.set('search', search)
  const query = params.toString()
  return request<FlowSummary[]>(`/flows${query ? `?${query}` : ''}`)
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

/** 函数签名推导出的启动参数，type 是 Python 侧的类型名，由属性面板归一成 ParamType */
export interface ProbeParam {
  name: string
  type?: string
  required?: boolean
  default?: unknown
}

export interface ProbeFunction {
  params: ProbeParam[]
  error?: string
}

/** key 为「工程:函数」 */
export type ProbeResult = Record<string, ProbeFunction>

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function normalizeProbeParam(raw: unknown, index: number): ProbeParam | null {
  if (typeof raw === 'string' && raw) return { name: raw, required: true }
  if (!isRecord(raw)) return null
  const name = typeof raw.name === 'string' && raw.name ? raw.name : `arg${index + 1}`
  return {
    name,
    type: typeof raw.type === 'string' ? raw.type : undefined,
    required: raw.required === true,
    default: raw.default,
  }
}

/** 单个函数既可能返回参数数组，也可能返回 {error}，统一成一种形状 */
function normalizeProbeEntry(raw: unknown): ProbeFunction {
  if (Array.isArray(raw)) {
    return {
      params: raw.map(normalizeProbeParam).filter((item): item is ProbeParam => Boolean(item)),
    }
  }
  if (isRecord(raw) && typeof raw.error === 'string' && raw.error) {
    return { params: [], error: raw.error }
  }
  if (isRecord(raw) && Array.isArray(raw.params)) {
    return normalizeProbeEntry(raw.params)
  }
  return { params: [] }
}

export async function probeFlow(id: string, doc: FlowDoc): Promise<ProbeResult> {
  const data = await request<unknown>(`/flows/${encodeURIComponent(id)}/probe`, {
    method: 'POST',
    body: JSON.stringify({ doc }),
  })
  if (!isRecord(data)) return {}
  const result: ProbeResult = {}
  for (const [key, value] of Object.entries(data)) {
    result[key] = normalizeProbeEntry(value)
  }
  return result
}
