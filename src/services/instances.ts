import type {
  InstanceDetail,
  InstanceStatus,
  InstanceSummary,
  ReviewDecision,
  ReviewTask,
  RunRecord,
} from '@/domain/types'
import { request } from '@/services/api'

/** v1.19：与串行／并行并列的第三种模式，手动模式下平台不跑任何槽位 */
export type InstanceMode = 'serial' | 'parallel' | 'manual'
export type EngineKind = 'python/v1' | 'manual/v1'

export interface InstanceRuntime {
  mode: InstanceMode
  maxParallel?: number
  engine?: EngineKind
}

/** 手动模式下人工选择产生的 run 没有业务调用，source 标 manual */
export type RunSource = 'engine' | 'manual'

/** 分类节点里目标排查节点标题为空时的统一文案，与后端一致 */
export const UNNAMED_INVESTIGATION = '未命名排查'

export interface InstanceFilter {
  flowId?: string
  status?: InstanceStatus
}

export interface RollbackPayload {
  targetNodeId: string
  targetTokenId?: string
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

/**
 * 后端 runtime 有两种形状：自动 `{engine:"python/v1",mode:"serial"|"parallel",maxParallel}`
 * 与手动 `{engine:"manual/v1",mode:"manual"}`；老数据可能没有 engine。统一归一化后再判定。
 */
export function instanceRuntime(instance: { runtime?: unknown } | null | undefined): InstanceRuntime {
  const raw = instance?.runtime
  if (!raw || typeof raw !== 'object') return { mode: 'serial', maxParallel: 4 }
  const record = raw as Record<string, unknown>
  const mode: InstanceMode =
    record.mode === 'parallel' || record.mode === 'manual' ? record.mode : 'serial'
  return {
    mode,
    maxParallel: typeof record.maxParallel === 'number' ? record.maxParallel : 4,
    engine:
      record.engine === 'manual/v1'
        ? 'manual/v1'
        : record.engine === 'python/v1'
          ? 'python/v1'
          : undefined,
  }
}

export function isManualInstance(instance: { runtime?: unknown } | null | undefined): boolean {
  return instanceRuntime(instance).mode === 'manual'
}

export function runSource(run: RunRecord): RunSource {
  return (run as RunRecord & { source?: string }).source === 'manual' ? 'manual' : 'engine'
}

/** 手动 run 的 output：分类是 `{choice}`，排查是 `{result, reason}` */
export interface RunOutputView {
  choice?: string
  result?: string
  reason?: string
}

export function runOutput(run: RunRecord): RunOutputView {
  const raw = run.output
  if (!raw || typeof raw !== 'object') return {}
  const record = raw as Record<string, unknown>
  return {
    choice: typeof record.choice === 'string' ? record.choice : undefined,
    result: typeof record.result === 'string' ? record.result : undefined,
    reason: typeof record.reason === 'string' ? record.reason : undefined,
  }
}

export interface CategoryOption {
  /** 目标排查节点 id（后端字段名是 node，不是 nodeId） */
  node: string
  /** 可能为空，展示时回落到「未命名排查」 */
  title?: string
}

export interface ManualFieldsSnapshot {
  observation?: string
  acquisition?: string
  assessment?: string
}

export interface ManualRequestView {
  type?: string
  nodeKind?: string
  title?: string
  detail?: string
  options?: CategoryOption[]
  /** 手动排查待办的三项字段是对象，不是现场参数用的数组 */
  fields?: ManualFieldsSnapshot
}

export function manualRequest(review: ReviewTask): ManualRequestView {
  return (review.request ?? {}) as unknown as ManualRequestView
}

/** 待办类型：domain 的类型定义还停在 v1.18，这里读原始值以支持 category */
export function reviewType(review: ReviewTask): string {
  return manualRequest(review).type ?? ''
}

export function categoryOptions(review: ReviewTask): CategoryOption[] {
  const options = manualRequest(review).options
  if (!Array.isArray(options)) return []
  return options
    .filter((item): item is CategoryOption => Boolean(item) && typeof item.node === 'string')
    .map((item) => ({ node: item.node, title: typeof item.title === 'string' ? item.title : '' }))
}

/** 排查待办的三项字段；自动模式的判定待办没有 fields，返回 null */
export function investigationSnapshot(review: ReviewTask): ManualFieldsSnapshot | null {
  const request = manualRequest(review)
  if (request.type !== 'decision' || request.nodeKind !== 'investigation') return null
  const fields = request.fields
  // 自动模式的人工判定也有 type=decision，但 fields 是数组（现场参数）或不存在；
  // 手动模式的 fields 是对象且至少填了一项
  if (!fields || typeof fields !== 'object' || Array.isArray(fields)) return null
  const snapshot = {
    observation: fields.observation ?? '',
    acquisition: fields.acquisition ?? '',
    assessment: fields.assessment ?? '',
  }
  if (!snapshot.observation && !snapshot.acquisition && !snapshot.assessment) return null
  return snapshot
}

export interface SwitchToAutoPayload {
  mode?: 'serial' | 'parallel'
  maxParallel?: number
}

/** 手动 → 自动（单向）；非手动模式后端返回 409 */
export function switchToAuto(
  instanceId: string,
  payload: SwitchToAutoPayload = {},
): Promise<InstanceDetail> {
  return request<InstanceDetail>(`/instances/${encodeURIComponent(instanceId)}/switch-to-auto`, {
    method: 'POST',
    body: JSON.stringify({ mode: payload.mode, maxParallel: payload.maxParallel ?? 4 }),
  })
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
