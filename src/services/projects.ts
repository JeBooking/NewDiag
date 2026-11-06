import { ApiError, request } from './api'

export interface Project {
  id: string
  name: string
  path: string
  module: string
  python: string
  factory: string
}

export interface CreateProjectPayload {
  name: string
  path: string
  module: string
  python: string
  factory: string
}

/** 不含 name：工程名是流程 binding 的引用键，登记后不可改 */
export interface UpdateProjectPayload {
  path?: string
  module?: string
  python?: string
  factory?: string
}

export interface ProjectFunctionParam {
  name: string
  annotation?: string | null
  default?: string | null
  required?: boolean
}

export interface ProjectFunction {
  name: string
  doc?: string | null
  kind?: string
  params: ProjectFunctionParam[]
  returns?: string | null
  protocol?: 'investigation/v1' | 'diagnosis/v1'
  methods?: { name: string; params: ProjectFunctionParam[] }[]
  version?: string
  configParams?: { name: string; required: boolean; default?: unknown }[]
  automaticAcquisition?: boolean
  automaticJudgment?: boolean
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

/** 参数默认值可能是数字/布尔/None，统一转成可展示文本 */
function defaultText(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function normalizeProject(raw: unknown): Project | null {
  const item = record(raw)
  if (!item) return null
  const id = text(item.id)
  if (!id) return null
  return {
    id,
    name: text(item.name) ?? id,
    path: text(item.path) ?? '',
    module: text(item.module) ?? '',
    python: text(item.python) ?? '',
    factory: text(item.factory) ?? '',
  }
}

function normalizeParam(raw: unknown, index: number): ProjectFunctionParam | null {
  const item = record(raw)
  if (!item) return null
  return {
    name: text(item.name) ?? `arg${index + 1}`,
    annotation: text(item.annotation) ?? text(item.type) ?? null,
    default: defaultText(item.default),
    required: item.required !== false,
  }
}

function normalizeFunction(raw: unknown): ProjectFunction | null {
  const item = record(raw)
  if (!item) return null
  const name = text(item.name)
  if (!name) return null
  const params = Array.isArray(item.params)
    ? item.params.map(normalizeParam).filter((param): param is ProjectFunctionParam => Boolean(param))
    : []
  return {
    name,
    doc: text(item.doc) ?? text(item.description) ?? null,
    kind: text(item.kind),
    params,
    returns: text(item.returns) ?? null,
    protocol: item.protocol === 'investigation/v1' || item.protocol === 'diagnosis/v1'
      ? item.protocol : undefined,
    methods: Array.isArray(item.methods) ? item.methods.flatMap((raw) => {
      const method = record(raw)
      return method && typeof method.name === 'string'
        ? [{ name: method.name, params: Array.isArray(method.params)
          ? method.params.map(normalizeParam).filter((p): p is ProjectFunctionParam => Boolean(p)) : [] }]
        : []
    }) : [],
    version: text(item.version),
    configParams: Array.isArray(item.configParams)
      ? item.configParams.flatMap((raw) => {
        const param = record(raw)
        return param && typeof param.name === 'string'
          ? [{ name: param.name, required: param.required === true, default: param.default }]
          : []
      })
      : [],
    automaticAcquisition: item.automaticAcquisition === true,
    automaticJudgment: item.automaticJudgment === true,
  }
}

export async function listProjects(): Promise<Project[]> {
  const data = await request<unknown>('/projects')
  const raw = Array.isArray(data) ? data : []
  return raw.map(normalizeProject).filter((item): item is Project => Boolean(item))
}

export async function fetchProjectCatalog(id: string, module: string): Promise<ProjectFunction[]> {
  const query = new URLSearchParams({ catalog: 'true', module })
  const raw = await request<unknown>(`/projects/${encodeURIComponent(id)}/functions?${query}`)
  return Array.isArray(raw)
    ? raw.map(normalizeFunction).filter((item): item is ProjectFunction => Boolean(item)) : []
}

export async function createProject(payload: CreateProjectPayload): Promise<Project> {
  const data = await request<unknown>('/projects', { method: 'POST', body: JSON.stringify(payload) })
  const project = normalizeProject(data)
  if (!project) throw new ApiError(500, '新建工程返回的数据不完整')
  return project
}

export async function updateProject(id: string, payload: UpdateProjectPayload): Promise<Project> {
  const data = await request<unknown>(`/projects/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
  const project = normalizeProject(data)
  if (!project) throw new ApiError(500, '编辑工程返回的数据不完整')
  return project
}

export async function deleteProject(id: string): Promise<void> {
  await request<void>(`/projects/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export interface ProjectFunctions {
  functions: ProjectFunction[]
  error?: string | null
}

function normalizeFunctions(raw: unknown): ProjectFunction[] {
  if (!Array.isArray(raw)) return []
  return raw.map(normalizeFunction).filter((item): item is ProjectFunction => Boolean(item))
}

export async function refreshProject(id: string): Promise<ProjectFunctions> {
  const data = await request<unknown>(`/projects/${encodeURIComponent(id)}/refresh`, {
    method: 'POST',
  })
  const item = record(data)
  const error = typeof item?.error === 'string' && item.error ? item.error : null
  return { functions: normalizeFunctions(item?.functions), error }
}

export async function fetchProjectFunctions(projectId: string): Promise<ProjectFunction[]> {
  const data = await request<unknown>(`/projects/${encodeURIComponent(projectId)}/functions`)
  const list = Array.isArray(data) ? data : record(data)?.functions
  return normalizeFunctions(list)
}
