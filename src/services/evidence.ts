/** 运行证据里四阶段执行痕迹的只读视图；后端 evidence 是 any，形状不固定，统一在这里收敛 */

export interface PipelineStage {
  id: string
  /** 后端原始阶段名，acquire / parse / present / judge，也可能是适配器的 dumpRaw 之类 */
  key: string
  label: string
  project?: string
  function?: string
  arguments: Record<string, unknown>
  durationMs?: number
  error?: string
}

const STAGE_LABELS: Record<string, string> = {
  acquire: '获取',
  parse: '解析',
  present: '展示',
  judge: '判断',
  manualAcquisition: '人工采集',
  manualJudgment: '人工判断',
  loadParsed: '读取解析',
  dumpRaw: '存原始',
  dumpParsed: '存解析',
}

const MAX_VALUE_CHARS = 32
const MAX_ARGUMENTS = 2

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function briefValue(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'string') {
    return value.length > MAX_VALUE_CHARS ? `${value.slice(0, MAX_VALUE_CHARS)}…` : value
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (isRecord(value) && value.omitted === true) {
    const bytes = typeof value.bytes === 'number' ? value.bytes : null
    return bytes === null ? '已省略' : `已省略 ${bytes} 字节`
  }
  try {
    const text = JSON.stringify(value)
    if (!text) return String(value)
    return text.length > MAX_VALUE_CHARS ? `${text.slice(0, MAX_VALUE_CHARS)}…` : text
  } catch {
    return String(value)
  }
}

/** 参数量可能很多，只留前两项，其余折成计数，避免把证据区撑满 */
export function summarizeArguments(args: Record<string, unknown>): string {
  const entries = Object.entries(args)
  if (!entries.length) return ''
  const parts = entries
    .slice(0, MAX_ARGUMENTS)
    .map(([name, value]) => `${name}=${briefValue(value)}`)
  if (entries.length > MAX_ARGUMENTS) parts.push(`+${entries.length - MAX_ARGUMENTS} 项`)
  return parts.join(' · ')
}

export function stageTarget(stage: PipelineStage): string {
  if (stage.project && stage.function) return `${stage.project}.${stage.function}`
  return stage.function || '—'
}

export function formatStageDuration(ms?: number): string {
  if (ms === undefined) return '—'
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms}ms`
}

/**
 * 只认 evidence.pipeline.stages：适配器单函数绑定、旧记录、人工节点都没有这个结构，
 * 返回空数组让调用方决定要不要整块隐藏，而不是渲染空壳。
 */
export function readPipelineStages(evidence: unknown): PipelineStage[] {
  if (!isRecord(evidence)) return []
  const pipeline = evidence.pipeline
  if (!isRecord(pipeline) || !Array.isArray(pipeline.stages)) return []
  const stages: PipelineStage[] = []
  pipeline.stages.forEach((raw, index) => {
    if (!isRecord(raw)) return
    const key = typeof raw.stage === 'string' ? raw.stage : ''
    if (!key) return
    stages.push({
      id: `${index}:${key}`,
      key,
      label: STAGE_LABELS[key] ?? key,
      project: typeof raw.project === 'string' ? raw.project : undefined,
      function: typeof raw.function === 'string' ? raw.function : undefined,
      arguments: isRecord(raw.arguments) ? raw.arguments : {},
      durationMs: typeof raw.durationMs === 'number' ? raw.durationMs : undefined,
      error: typeof raw.error === 'string' ? raw.error : undefined,
    })
  })
  return stages
}
