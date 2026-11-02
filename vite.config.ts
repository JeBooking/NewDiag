import { fileURLToPath, URL } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 改后端 Python 时 uvicorn 会自己重启，但浏览器还停在旧页面，
 * 重启窗口内的请求会打到半启动的服务上。等它起来再让前端整页刷新。
 */
function reloadOnBackendChange(): Plugin {
  const backendDir = fileURLToPath(new URL('./backend', import.meta.url)).replace(/\\/g, '/')
  return {
    name: 'reload-on-backend-change',
    apply: 'serve',
    configureServer(server) {
      server.watcher.on('change', (file) => {
        const path = file.replace(/\\/g, '/')
        // 只认后端代码；docs/、scripts/ 的改动不影响运行中的进程
        if (!path.startsWith(backendDir) || !path.endsWith('.py')) return
        server.config.logger.info(`[backend-reload] ${path}`)
        // uvicorn --reload 要重建 worker，太早刷新会撞在重启窗口上
        setTimeout(() => server.hot.send({ type: 'full-reload' }), 1200)
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
