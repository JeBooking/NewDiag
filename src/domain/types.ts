export type FlowMode = 'parallel' | 'serial'

export type NodeKind =
  | 'start'
  | 'investigation'
  | 'investigationAction'
  | 'conclusion'
  | 'remediation'
  | 'problemSolved'
  | 'escalation'
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

export interface ManualField {
  name: string
  label?: string
  type?: ParamType
  required?: boolean
  default?: unknown
  min?: number
  max?: number
  unit?: string
  description?: string
}

/** 开始节点参数的输入控件类型，由函数签名推导，作者可覆盖 */
export type ParamType = 'text' | 'number' | 'boolean' | 'datetime' | 'file' | 'array' | 'object' | 'json'

/**
 * 开始节点声明的一个启动参数；组合配置下都是全局输入。
 */
export interface StartParam {
  name: string
  type?: ParamType
  /** 设计时默认值；执行时可改，改完固化进本次启动上下文 */
  default?: unknown
}

export type CompositionArgument =
  | { lit: unknown }
  | { source: 'input' | 'parameter' | 'value' | 'object' | 'resource' | 'node'; name: string;
      path?: string; file?: boolean; arguments?: Record<string, CompositionArgument> }
  | { system: 'directory' | 'file' }
  | { items: CompositionArgument[] }
  | { fields: Record<string, CompositionArgument> }

export interface CompositionCall {
  id: string
  project: string
  module?: string
  function: string
  interface?: string
  target?: CompositionArgument
  arguments: Record<string, CompositionArgument>
  output?: 'value' | 'object' | 'file' | 'image'
}

export interface CompositionResource {
  id: string
  trigger: 'startup' | 'lazy'
  defaults?: Record<string, unknown>
  calls: CompositionCall[]
  result: CompositionArgument
}

export interface CompositionOperation {
  id: string
  parameters?: string[]
  calls: CompositionCall[]
  presentation: { type: 'image' | 'text' | 'line' | 'metrics' | 'table'; value: CompositionArgument }[]
  decision?: { result: CompositionArgument; reason: CompositionArgument; normalReason?: string }
}

export interface Composition {
  objects: CompositionCall[]
  resources: CompositionResource[]
  operations: CompositionOperation[]
}

export interface DocNode {
  id: string
  kind: NodeKind
  title: string
  detail: string
  fields?: InvestigationFields
  attachments?: AttachmentRef[]
  operation?: string
  operationArguments?: Record<string, CompositionArgument>
  runtimeInputs?: ManualField[]
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
  composition?: Composition
  nodes: DocNode[]
  edges: DocEdge[]
}

type BindingFields = 'runtimeInputs' | 'operation' | 'operationArguments'

/** 持久化与导出格式；编辑器使用 FlowDoc 投影，运行配置保存在实例中。 */
export interface StoredFlow {
  format: 2
  flow: Omit<FlowDoc, 'nodes' | 'composition'> & {
    nodes: Omit<DocNode, BindingFields | 'position'>[]
  }
  bindings: {
    format: 1
    composition?: Composition
    nodes: Record<string, Pick<DocNode, BindingFields>>
  }
  presentation: {
    format: number
    nodes?: Record<string, { position: DocNode['position'] }>
  }
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

/**
 * 节点相对「诊断路径」的位置，与 NodeVisualStatus 正交：
 * 状态回答「这一步成没成」，它回答「这条路还算不算数」。
 */
export type NodeTrailState = 'current' | 'dropped' | 'unvisited'

/** 边是否被实际走过；dropped 表示走过但随回退作废 */
export type EdgeTrail = 'taken' | 'dropped'

export interface PlanStep {
  kind: NodeKind
  compositionOperation?: string
  operationInputs?: ManualField[]
  operationArguments?: Record<string, CompositionArgument>
  human?: boolean
  text?: string
  next?: Record<string, string[]>
  terminal?: boolean
}

export interface Plan {
  format: 1
  flowId: string
  release: number
  entry: string
  steps: Record<string, PlanStep>
  signature: Record<string, string>
  composition?: Composition
}

export type InstanceStatus =
  | 'running'
  | 'waiting'
  | 'paused'
  | 'success'
  | 'failed'
  | 'escalated'
  | 'terminated'

export type TokenStatus = 'queued' | 'running' | 'waiting' | 'done' | 'failed' | 'abandoned'

export interface Token {
  id: string
  nodeId: string
  status: TokenStatus
  branch?: Branch
  order?: number
  generation: number
  parentTokenId?: string | null
  visitedAt?: string | null
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
  tokenId?: string | null
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
    type?: 'decision' | 'action' | 'after_success' | 'judgment' | 'parameters'
    fields?: ManualField[]
    presentation?: unknown
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
  data?: Record<string, unknown>
}

export interface FlowSummary {
  id: string
  name: string
  description: string
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
  runtime?: { mode: FlowMode; maxParallel: number; engine?: string }
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
