export type FlowMode = 'parallel' | 'serial'

export type NodeKind =
  | 'start'
  | 'investigation'
  | 'dataPreparation'
  | 'capability'
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

/** 能力配方里一个步骤的展示方式 */
export type RecipeShow = 'auto' | 'text' | 'table' | 'line' | 'image' | 'none'

/**
 * 能力配方的一个调用步骤。
 * args 里以 $ 开头的单键对象是表达式：
 *   {$snapshot: 启动参数名, cast?} {$run: 上游节点, path} {$var: 本节点已保存对象, path?}
 *   {$prev: path?} {$file: 文件名}
 * 其余结构原样作为字面量传入。
 */
export interface RecipeStep {
  /** 点分调用名，如 Dataset.load、plot_window.save_plot */
  call: string
  /** 在该步骤保存的对象上调用；省略表示从工程根（模块或工厂实例）解析 */
  on?: string
  args?: Record<string, unknown>
  /** 把返回值保存为本节点内的对象名，供后续步骤用 $var 引用 */
  as?: string
  show?: RecipeShow
  /** 展示块的标题 */
  label?: string
}

/** 输出字段或判定字段的来源：本节点内某个步骤保存的对象 + 字段路径 */
export interface RecipeRef {
  from: string
  path?: string
}

export interface RecipeReason {
  template?: string
  from?: string
  path?: string
}

/**
 * 能力配方：一个节点内按顺序调用现成库，节点之间只传 JSON。
 * 对象（数据集、绘图对象、判断引擎）只在本节点的进程内存在，因此不需要业务侧适配代码。
 */
export interface LibraryRecipe {
  protocol?: 'library/v1'
  project: string
  steps: RecipeStep[]
  /** 对外暴露的 JSON 字段，下游可用 {run: 节点, path: output.字段} 引用 */
  out?: Record<string, RecipeRef>
  /** 判定来源；只有排查节点需要，取值必须是 yes/no */
  result?: RecipeRef
  reason?: string | RecipeReason
  /** 把某个上游节点的展示（如图表）一并带进本次待办 */
  evidence?: string
}

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
  pipeline?: FunctionPipeline
  /** 能力配方；与 binding / pipeline / method 互斥 */
  recipe?: LibraryRecipe
  method?: string
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
  pipeline?: FunctionPipeline & { args: Record<string, ArgExpr> }
  recipe?: LibraryRecipe
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
