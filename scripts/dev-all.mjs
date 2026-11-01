// 开发态一键启动：后端 uvicorn(8000) + 前端 Vite(5173)。
// 为什么自己写而不引 concurrently：项目约定不加没被要求的依赖。
// 为什么必须捆绑后端：Vite 把 /api 代理到 8000，后端没起时每个请求都会变成 500。

import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const projectRoot = join(here, '..')
const backendDir = join(projectRoot, 'backend')
const isWin = process.platform === 'win32'

const python = isWin
  ? join(backendDir, '.venv', 'Scripts', 'python.exe')
  : join(backendDir, '.venv', 'bin', 'python')
const viteBin = join(projectRoot, 'node_modules', 'vite', 'bin', 'vite.js')

function die(message) {
  console.error(`[dev] ${message}`)
  process.exit(1)
}

if (!existsSync(python)) {
  die(
    `找不到后端解释器 ${python}\n` +
      '      先建虚拟环境：cd backend && python -m venv .venv && ' +
      '.venv/Scripts/python -m pip install -r requirements.txt',
  )
}
if (!existsSync(viteBin)) {
  die(`找不到 Vite（${viteBin}）\n      先装前端依赖：pnpm install`)
}

const children = []
let stopping = false

function killTree(child) {
  if (!child.pid || child.killed) return
  if (isWin) {
    // uvicorn --reload 会派生 worker，只杀父进程会留下占着 8000 的孤儿
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' })
  } else {
    try {
      process.kill(-child.pid, 'SIGTERM')
    } catch {
      child.kill('SIGTERM')
    }
  }
}

function stopAll(code) {
  if (stopping) return
  stopping = true
  for (const { child } of children) killTree(child)
  process.exit(code)
}

function start(name, command, args, options) {
  const child = spawn(command, args, {
    stdio: 'inherit',
    // 非 Windows 需要独立进程组，退出时才能连同子进程一起杀掉
    detached: !isWin,
    ...options,
  })
  children.push({ name, child })

  child.on('error', (error) => {
    console.error(`[dev] ${name} 启动失败：${error.message}`)
    stopAll(1)
  })
  child.on('exit', (code) => {
    if (stopping) return
    if (name === '后端' && code !== 0) {
      console.error('[dev] 后端未能启动：检查 8000 端口是否被占用（例如离线包 start.cmd 仍在运行）')
    }
    console.error(`[dev] ${name} 已退出（code=${code ?? 'unknown'}），停止其余进程`)
    stopAll(code ?? 1)
  })
  return child
}

process.on('SIGINT', () => stopAll(0))
process.on('SIGTERM', () => stopAll(0))

console.log('[dev] 后端 http://127.0.0.1:8000   前端 http://127.0.0.1:5173   Ctrl+C 停止')

start('后端', python, ['-m', 'uvicorn', 'app.main:app', '--reload', '--port', '8000'], {
  cwd: backendDir,
})
start('前端', process.execPath, [viteBin], { cwd: projectRoot })
