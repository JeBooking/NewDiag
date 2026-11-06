export type FlowMode = 'parallel' | 'serial'

export type NodeKind =
  | 'start'
  | 'investigation'
  | 'dataPreparation'
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
  protocol?: 'investigation/v1' | 'diagnosis/v1'
  version?: string
  config?: Record<string, unknown>
}

export interface ManualField {
  name: string
  label?: string
  type?: 'text' | 'number' | 'boolean' | 'datetime' | 'json' | 'file'
  required?: boolean
  default?: unknown
  min?: number
  max?: number
  unit?: string
  description?: string
}

export type StageName = 'acquire' | 'parse' | 'present' | 'judge'

export type StageArgument =
  | { source: 'raw' | 'parsed' | 'decision' | 'inputs'; path?: string }
  | { lit: unknown }
  | { omit: true }

export interface StageFunction {
  project: string
  function: string
  arguments?: Record<string, StageArgument>
}

export interface FunctionPipeline {
  dataSource?: { node: string; path?: string }
  acquire?: StageFunction
  parse?: StageFunction
  present?: StageFunction
  judge?: StageFunction
  manualFields: ManualField[]
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

export type CompositionArgument =
  | { lit: unknown }
  | { source: 'input' | 'parameter' | 'value' | 'object' | 'resource'; name: string;
      path?: string; arguments?: Record<string, CompositionArgument> }
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
  presentation: { type: 'image' | 'text'; value: CompositionArgument }[]
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
  binding?: NodeBinding
  pipeline?: FunctionPipeline
  method?: string
  operation?: string
  operationArguments?: Record<string, CompositionArgument>
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
  diagnosis?: NodeBinding
  composition?: Composition
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

/**
 * 节点相对「诊断路径」的位置，与 NodeVisualStatus 正交：
 * 状态回答「这一步成没成」，它回答「这条路还算不算数」。
 */
export type NodeTrailState = 'current' | 'dropped' | 'unvisited'

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
  protocol?: 'investigation/v1'
  version?: string
  config?: Record<string, unknown>
}

export interface PlanStep {
  kind: NodeKind
  call?: PlanCall
  diagnosisMethod?: { name: string; args: Record<string, ArgExpr> }
  compositionOperation?: string
  operationArguments?: Record<string, CompositionArgument>
  pipeline?: FunctionPipeline & { args: Record<string, ArgExpr> }
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
  composition?: Composition
  diagnosis?: NodeBinding & {
    args: Record<string, ArgExpr>
    definition: Record<string, unknown>
  }
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
    type?: 'decision' | 'action' | 'after_success' | 'acquisition' | 'judgment'
    fields?: ManualField[]
    presentation?: unknown
    pipeline?: Record<string, unknown>
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
