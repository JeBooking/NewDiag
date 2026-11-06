import { request } from 'node:http'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 改后端 Python 时 uvicorn 会自己重启，但浏览器还停在旧页面。
 * 重启窗口内的请求会打到半启动的服务上，所以刷新必须等后端真的能应答；
 * 固定延迟（原先 1.2s）要么太早撞上重建 worker，要么白等。
 */
function waitBackendReady(timeoutMs = 10_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  const attempt = (): Promise<boolean> =>
    new Promise<boolean>((resolve) => {
      const req = request(
        { host: '127.0.0.1', port: 8000, path: '/api/health', timeout: 800 },
        (res) => {
          res.resume()
          resolve(res.statusCode === 200)
        },
      )
      req.on('timeout', () => {
        req.destroy()
        resolve(false)
      })
      req.on('error', () => resolve(false))
      req.end()
    }).then((ok) => {
      if (ok) return true
      if (Date.now() >= deadline) return false
      return new Promise<void>((resolve) => setTimeout(resolve, 300)).then(attempt)
    })
  return attempt()
}

function reloadOnBackendChange(): Plugin {
  const backendDir = fileURLToPath(new URL('./backend', import.meta.url)).replace(/\\/g, '/')
  return {
    name: 'reload-on-backend-change',
    apply: 'serve',
    configureServer(server) {
      let pending = false
      server.watcher.on('change', (file) => {
        const path = file.replace(/\\/g, '/')
        // 只认后端代码；docs/、scripts/ 的改动不影响运行中的进程
        if (!path.startsWith(backendDir) || !path.endsWith('.py')) return
        server.config.logger.info(`[backend-reload] ${path}`)
        // 连按保存会连发 change，合并成一次等待 + 一次刷新
        if (pending) return
        pending = true
        void waitBackendReady()
          .then((ready) => {
            if (!ready) {
              server.config.logger.warn('[backend-reload] 后端 10 秒内未就绪，仍然刷新页面')
            }
            server.hot.send({ type: 'full-reload' })
          })
          .finally(() => {
            pending = false
          })
      })
    },
  }
}

export default defineConfig({
  plugins: [vue(), reloadOnBackendChange()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
