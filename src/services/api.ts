import type { LintIssue } from '@/domain/types'

/** v1 固定本地用户，权限模型落地后由登录态替换 */
export const CURRENT_USER = 'local'

/** 浏览器控制台调试开关：开发态默认开，生产在控制台执行 localStorage.setItem('newdiag:debug','1') 后刷新 */
export const debugEnabled = (() => {
  try {
    return import.meta.env.DEV || localStorage.getItem('newdiag:debug') === '1'
  } catch {
    return import.meta.env.DEV
  }
})()

export function debugLog(...args: unknown[]) {
  if (debugEnabled) console.debug('[newdiag]', ...args)
}

function warnLog(...args: unknown[]) {
  console.warn('[newdiag]', ...args)
}

const errorMessageKeys = ['message', 'msg', 'detail'] as const

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown
  readonly issues: LintIssue[]

  constructor(status: number, message: string, detail?: unknown, issues: LintIssue[] = []) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.issues = issues
  }
}

function pickMessage(entry: unknown): string | null {
  if (typeof entry === 'string') return entry
  if (!entry || typeof entry !== 'object') return null
  const record = entry as Record<string, unknown>
  for (const key of errorMessageKeys) {
    const value = record[key]
    if (typeof value === 'string' && value) return value
  }
  return null
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

/** 后端 detail 可能是字符串、校验问题数组、或 FastAPI 的 422 错误数组 */
function toIssues(detail: unknown): LintIssue[] {
  if (!Array.isArray(detail)) {
    const message = pickMessage(detail)
    return message ? [{ id: 'api', level: 'error', message }] : []
  }
  return detail.map((entry, index) => {
    const record = entry && typeof entry === 'object' ? (entry as Record<string, unknown>) : null
    const nodeId = str(record?.nodeId)
    const edgeId = str(record?.edgeId)
    return {
      id: str(record?.id) ?? `api-${index}`,
      level: record?.level === 'warning' ? 'warning' : 'error',
      message: pickMessage(entry) ?? '未知错误',
      nodeId,
      edgeId,
    }
  })
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  const body = init?.body
  if (body !== undefined && body !== null && !(body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const method = init?.method ?? 'GET'
  const started = performance.now()
  let response: Response
  try {
    response = await fetch(`/api${path}`, { ...init, headers })
  } catch (error) {
    warnLog(`${method} ${path} 网络失败`, (error as Error).message)
    throw new ApiError(0, `无法连接后端服务：${(error as Error).message}`)
  }

  const cost = Math.round(performance.now() - started)
  const requestId = response.headers.get('X-Request-ID') ?? '-'

  if (!response.ok) {
    const payload = await readBody(response)
    const detail =
      payload && typeof payload === 'object' && !Array.isArray(payload) && 'detail' in payload
        ? (payload as { detail: unknown }).detail
        : payload
    const issues = toIssues(detail)
    const message = issues.map((issue) => issue.message).join('；') || `请求失败（HTTP ${response.status}）`
    warnLog(`${method} ${path} ${response.status} ${cost}ms req=${requestId}`, detail)
    throw new ApiError(response.status, message, detail, issues)
  }

  debugLog(`${method} ${path} ${response.status} ${cost}ms req=${requestId}`)
  if (response.status === 204) return undefined as T
  return (await readBody(response)) as T
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

/** 把未知异常转成可展示的文案，供各页面统一处理 */
export function errorText(error: unknown, fallback = '操作失败'): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return fallback
}
