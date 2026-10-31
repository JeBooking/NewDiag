import { debugLog } from './api'

export interface InstanceEvent {
  type: string
  data: unknown
}

/** 断线重连退避；超出表长后维持最后一档，避免请求风暴 */
const BACKOFF_MS = [1000, 2000, 5000]
const EVENT_TYPES = ['status', 'run', 'review', 'update'] as const

export function subscribeInstance(
  instanceId: string,
  onEvent: (event: InstanceEvent) => void,
): () => void {
  let source: EventSource | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let closed = false
  let attempt = 0

  const handler = (type: string) => (event: MessageEvent<string>) => {
    attempt = 0
    let data: unknown = event.data
    if (event.data) {
      try {
        data = JSON.parse(event.data)
      } catch {
        data = event.data
      }
    }
    onEvent({ type, data })
  }

  const connect = () => {
    if (closed) return
    debugLog(`SSE 连接实例 ${instanceId}（第 ${attempt + 1} 次）`)
    source = new EventSource(`/api/instances/${encodeURIComponent(instanceId)}/events`)
    source.onopen = () => debugLog(`SSE 已连上实例 ${instanceId}`)
    source.onmessage = handler('message')
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, handler(type) as EventListener)
    }
    source.onerror = () => {
      source?.close()
      source = null
      if (closed) return
      const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]
      attempt += 1
      debugLog(`SSE 断开实例 ${instanceId}，${delay}ms 后重连`)
      timer = setTimeout(connect, delay)
    }
  }

  connect()

  return () => {
    closed = true
    if (timer) clearTimeout(timer)
    timer = null
    source?.close()
    source = null
  }
}
