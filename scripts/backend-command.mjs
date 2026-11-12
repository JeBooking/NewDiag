import { join } from 'node:path'

export function backendArgs(backendDir, port = 8000) {
  // 执行产物包含 Python 源码；监控整个 backend 会把生成代码当成平台更新。
  return [
    '-m', 'uvicorn', 'app.main:app', '--reload',
    '--reload-dir', join(backendDir, 'app'),
    '--port', String(port),
  ]
}
