export type FlowMode = 'parallel' | 'serial'

export type NodeKind =
  | 'start'
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

/** 开始节点参数的输入控件类型，由函数签名推导，作者可覆盖 */
export type ParamType = 'text' | 'number' | 'boolean' | 'datetime'

/**
 * 开始节点声明的一个启动参数。
 * - scope=global：全局参数，所有函数可用
 * - scope=function：只对该函数可见，覆盖同名全局参数
 */
export interface StartParam {
  name: string
  scope: 'global' | 'function'
  /** scope=function 时必填，函数名（不带工程名前缀） */
  function?: string
  type?: ParamType
  /** 设计时默认值；执行时可改，改完固化进本次启动上下文 */
  default?: unknown
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
  /** 仅 start 节点：本次诊断的启动参数声明 */
  inputs?: StartParam[]
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
  /** 旧版全局启动字段；新文档改用 start 节点的 inputs，读取时兼容 */
  snapshotFields?: string[]
}

/** 流程目录，树形；parentId 为空表示根目录 */
export interface Folder {
  id: string
  name: string
  parentId: string | null
  createdAt: string
}

export interface FolderNode extends Folder {
  children: FolderNode[]
  flowCount?: number
}

export interface LintIssue {
  id: string
  level: 'error' | 'warning'
  message: string
  nodeId?: string
  edgeId?: string
}

/**
 * pending 现在只由 abandoned 映射而来，语义是「回退作废」，与 queued（排队等待执行）区分开：
 * 两者在画布上一个该压暗、一个该保持可读，共用一种状态会导致排队节点看不出「即将执行」。
 */
export type NodeVisualStatus = 'pending' | 'queued' | 'running' | 'waiting' | 'success' | 'failed'

/** 边是否被实际走过；dropped 表示走过但随回退作废 */
export type EdgeTrail = 'taken' | 'dropped'

export type ArgExpr =
  | { lit: unknown }
  /** optional=true 表示取不到值时省略该参数，交给函数自身默认值 */
  | { snapshot: string; optional?: boolean }
  | { run: string; path: string; optional?: boolean }

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
  /** 所属目录，空表示未分类 */
  folderId?: string | null
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

/** 发布时冻结的启动表单字段，执行页只读它，不读当前代码 */
export interface ReleaseInput {
  name: string
  scope: 'global' | 'function'
  function?: string
  type: ParamType
  required: boolean
  default?: unknown
}

export interface ReleaseDetail extends ReleaseSummary {
  flowId: string
  revisionId: string
  plan: Plan
  signature: Record<string, string>
  inputs: ReleaseInput[]
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
