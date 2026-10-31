export type FlowMode = 'parallel' | 'serial'

export type NodeKind =
  | 'investigation'
  | 'investigationAction'
  | 'conclusion'
  | 'remediation'
  | 'problemSolved'
  | 'escalation'
  | 'deepDiagnosis'
  | 'jump'

export type Branch = 'yes' | 'no'

export interface AttachmentRef {
  hash: string
  name: string
}

export interface InvestigationFields {
  observation: string
  acquisition: string
  assessment: string
}

export interface NodeBinding {
  project: string
  function: string
}

/** 参数来源：字段名（自动匹配）、快照字段、上游节点输出、常量 */
export type ParamSource =
  | string
  | { lit: unknown }
  | { snapshot: string }
  | { run: string; path: string }

export interface DocNode {
  id: string
  kind: NodeKind
  title: string
  detail: string
  fields?: InvestigationFields
  attachments?: AttachmentRef[]
  binding?: NodeBinding
  /** 该节点返回结构里的字段名，供下游绑定 */
  outputs?: string[]
  /** 参数名 → 来源；不填则按签名自动匹配 */
  params?: Record<string, ParamSource>
  position: { x: number; y: number }
}

export interface DocEdge {
  id: string
  from: string
  to: string
  branch?: Branch
  order?: number
}

export interface FlowDoc {
  format: 1
  name: string
  mode: FlowMode
  nodes: DocNode[]
  edges: DocEdge[]
  /** 启动输入里可被节点引用的字段名；发布时用于参数绑定 */
  snapshotFields?: string[]
}

export interface LintIssue {
  id: string
  level: 'error' | 'warning'
  message: string
  nodeId?: string
  edgeId?: string
}

export type NodeVisualStatus = 'pending' | 'running' | 'waiting' | 'success' | 'failed'

export type ArgExpr =
  | { lit: unknown }
  | { snapshot: string }
  | { run: string; path: string }

export interface PlanCall {
  project: string
  function: string
  args: Record<string, ArgExpr>
}

export interface PlanStep {
  kind: NodeKind
  call?: PlanCall
  human?: boolean
  text?: string
  next?: Record<string, string[]>
  terminal?: boolean
}

export interface Plan {
  format: 1
  flowId: string
  release: number
  mode: FlowMode
  entry: string
  steps: Record<string, PlanStep>
  signature: Record<string, string>
}

export type InstanceStatus = 'running' | 'waiting' | 'paused' | 'success' | 'failed' | 'escalated'

export type TokenStatus = 'queued' | 'running' | 'waiting' | 'done' | 'failed' | 'abandoned'

export interface Token {
  id: string
  nodeId: string
  status: TokenStatus
  branch?: Branch
  order?: number
  generation: number
  parentTokenId?: string | null
}

export type RunStatus = 'ok' | 'failed' | 'abandoned'

export interface RunRecord {
  id: string
  nodeId: string
  tokenId: string
  generation: number
  attempt: number
  status: RunStatus
  input?: unknown
  output?: unknown
  evidence?: unknown
  error?: string
  startedAt: string
  endedAt?: string
}

export type ReviewKind = 'decision' | 'action'
export type ReviewStatus = 'pending' | 'done' | 'cancelled'

export interface ReviewTask {
  id: string
  nodeId: string
  kind: ReviewKind
  status: ReviewStatus
  request: {
    title: string
    detail?: string
    evidence?: unknown
    attachments?: AttachmentRef[]
    instructions?: string
    nodeKind?: NodeKind
    type?: 'decision' | 'action' | 'after_success'
  }
  createdAt: string
  decidedAt?: string
  decidedBy?: string
  decision?: ReviewDecision
}

export interface ReviewDecision {
  result: 'yes' | 'no' | 'done' | 'rollback' | 'continue'
  target?: string
  reason?: string
}

export interface FlowSummary {
  id: string
  name: string
  description: string
  mode: FlowMode
  updatedAt: string
  editingBy?: string | null
  releaseCount: number
  lastRunStatus?: InstanceStatus | null
  lastPublishedAt?: string | null
}

export interface ReleaseSummary {
  id: string
  versionNo: number
  publishedAt: string
  publishedBy: string
  note?: string
}

export interface ReleaseDetail extends ReleaseSummary {
  flowId: string
  revisionId: string
  plan: Plan
  signature: Record<string, string>
  doc: FlowDoc
}

export interface FlowDetail extends FlowSummary {
  doc: FlowDoc
  currentRevisionId: string
  workingRevisionId?: string | null
  releases: ReleaseSummary[]
}

export interface InstanceSummary {
  id: string
  flowId: string
  flowName?: string
  releaseId: string
  releaseNo?: number
  status: InstanceStatus
  startedAt: string
  endedAt?: string
  startedBy: string
}

export interface InstanceDetail extends InstanceSummary {
  tokens: Token[]
  runs: RunRecord[]
  reviews: ReviewTask[]
  snapshot?: {
    id: string
    seq: number
    payload: unknown
    source?: string
    capturedAt: string
  }
}
