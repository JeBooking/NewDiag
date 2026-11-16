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
 * 开始节点声明的一个启动参数，全局可用。
 */
export interface StartParam {
  name: string
  type?: ParamType
  /** 设计时默认值；执行时可改，改完固化进本次启动上下文 */
  default?: unknown
}

export type SlotView = 'image' | 'line' | 'metrics' | 'table' | 'text'

/** 槽位参数的来源只有四种：常量、启动输入、现场参数、上一槽返回值 */
export type SlotArgument =
  | { lit: unknown }
  | { source: 'input' | 'parameter'; name: string; path?: string; file?: boolean }
  | { source: 'previous' }
  | { items: SlotArgument[] }
  | { fields: Record<string, SlotArgument> }

/** 一个槽位填的一个接口调用；object 指向开始节点构造的对象名，空表示模块级函数 */
export interface SlotCall {
  object?: string
  project: string
  module?: string
  interface: string
  function: string
  arguments: Record<string, SlotArgument>
  /** 仅展示槽：决定平台怎么渲染 */
  view?: SlotView
  /** 仅判定槽：true 表示不调用方法，交人工判断 */
  human?: boolean
  /** 仅拉取槽：默认使用缓存；置 false 表示重取一遍 */
  useCache?: boolean
}

/** 节点模板上的槽位；哪些槽可用由节点类型决定 */
export interface NodeSlots {
  constructs?: SlotCall[]
  fetch?: SlotCall
  process?: SlotCall
  present?: SlotCall
  decide?: SlotCall
}

export interface DocNode {
  id: string
  kind: NodeKind
  title: string
  detail: string
  fields?: InvestigationFields
  attachments?: AttachmentRef[]
  slots?: NodeSlots
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
  nodes: DocNode[]
  edges: DocEdge[]
}

type BindingFields = 'runtimeInputs' | 'slots'

/** 持久化与导出格式；编辑器使用 FlowDoc 投影，运行配置保存在实例中。 */
export interface StoredFlow {
  format: 2
  flow: Omit<FlowDoc, 'nodes'> & {
    nodes: Omit<DocNode, BindingFields | 'position'>[]
  }
  bindings: {
    format: number
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
  slots?: NodeSlots
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

export type RunStatus = 'ok' | 'failed' | 'abandoned' | 'rollback'

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
