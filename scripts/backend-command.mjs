export function backendArgs(_backendDir, port = 8000) {
  // 重启由开发启动器统一管理，避免 Windows 控制台信号传播及长连接阻塞退出。
  return [
    '-m', 'uvicorn', 'app.main:app',
    '--port', String(port),
  ]
}
