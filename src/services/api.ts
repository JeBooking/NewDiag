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

function parseBody(text: string): unknown {
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

async function readBody(response: Response): Promise<unknown> {
  return parseBody(await response.text())
}

/** 把后端返回体统一转成 ApiError：detail 可能是字符串、问题数组或 FastAPI 的 422 数组 */
function toApiError(status: number, payload: unknown): ApiError {
  const detail =
    payload && typeof payload === 'object' && !Array.isArray(payload) && 'detail' in payload
      ? (payload as { detail: unknown }).detail
      : payload
  const issues = toIssues(detail)
  const message = issues.map((issue) => issue.message).join('；') || `请求失败（HTTP ${status}）`
  return new ApiError(status, message, detail, issues)
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
    const error = toApiError(response.status, await readBody(response))
    warnLog(`${method} ${path} ${response.status} ${cost}ms req=${requestId}`, error.detail)
    throw error
  }

  debugLog(`${method} ${path} ${response.status} ${cost}ms req=${requestId}`)
  if (response.status === 204) return undefined as T
  return (await readBody(response)) as T
}

/**
 * 文件上传走 XHR：fetch 拿不到上传进度，取消也只能靠 AbortSignal 中断整个请求。
 * 与 request 共用错误归一化和日志，区别只在于能上报 upload.onprogress。
 */
export function uploadForm<T>(
  path: string,
  form: FormData,
  options: { signal?: AbortSignal; onProgress?: (progress: { loaded: number; total: number }) => void } = {},
): Promise<T> {
  const method = 'POST'
  return new Promise<T>((resolve, reject) => {
    if (options.signal?.aborted) {
      reject(new DOMException('上传已取消', 'AbortError'))
      return
    }

    const xhr = new XMLHttpRequest()
    const started = performance.now()
    let settled = false

    function onAbort(): void {
      xhr.abort()
    }

    const settle = (run: () => void): void => {
      if (settled) return
      settled = true
      options.signal?.removeEventListener('abort', onAbort)
      run()
    }

    xhr.open(method, `/api${path}`)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) options.onProgress?.({ loaded: event.loaded, total: event.total })
    }
    // 字节发完不等于完成，服务端还要落盘和校验
    xhr.upload.onload = () => options.onProgress?.({ loaded: 1, total: 1 })

    xhr.onload = () => settle(() => {
      const cost = Math.round(performance.now() - started)
      const requestId = xhr.getResponseHeader('X-Request-ID') ?? '-'
      const payload = parseBody(xhr.responseText)
      if (xhr.status < 200 || xhr.status >= 300) {
        const error = toApiError(xhr.status, payload)
        warnLog(`${method} ${path} ${xhr.status} ${cost}ms req=${requestId}`, error.detail)
        reject(error)
        return
      }
      debugLog(`${method} ${path} ${xhr.status} ${cost}ms req=${requestId}`)
      resolve((xhr.status === 204 ? null : payload) as T)
    })
    xhr.onerror = () => settle(() => reject(new ApiError(0, '无法连接后端服务：网络错误')))
    xhr.ontimeout = () => settle(() => reject(new ApiError(0, '上传超时')))
    xhr.onabort = () => settle(() => reject(new DOMException('上传已取消', 'AbortError')))

    options.signal?.addEventListener('abort', onAbort, { once: true })
    xhr.send(form)
  })
}

/** 用户主动取消时不该再弹「上传失败」，调用方据此静默收尾 */
export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
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
