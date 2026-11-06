// 开发态一键启动：后端 uvicorn(8000) + 前端 Vite(5173)。
// 为什么自己写而不引 concurrently：项目约定不加没被要求的依赖。
// 为什么必须捆绑后端：Vite 把 /api 代理到 8000，后端没起时每个请求都会变成 500。
// 为什么还要健康探测：uvicorn --reload 是「reloader 父进程 + worker 子进程」两层，
// worker 崩了父进程不会退出。只听子进程 exit，会把「后端已死」误判成「还在跑」。

import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { request } from 'node:http'
import { Socket } from 'node:net'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const projectRoot = join(here, '..')
const backendDir = join(projectRoot, 'backend')
const isWin = process.platform === 'win32'

const BACKEND_PORT = 8000
const WEB_PORT = 5173

// 探测参数：连续失败 3 次约 6 秒才判定失联，避免冷启动和 reload 窗口误判
const PROBE_INTERVAL = 2000
const PROBE_TIMEOUT = 1500
const FAIL_THRESHOLD = 3
const STARTUP_GRACE = 30_000
// 一分钟内最多自动重启 3 次；代码本身坏了就停下来，不要变成重启风暴
const MAX_RESTARTS = 3
const RESTART_WINDOW = 60_000
const RESTART_DELAY = 600

const python = isWin
  ? join(backendDir, '.venv', 'Scripts', 'python.exe')
  : join(backendDir, '.venv', 'bin', 'python')
const viteBin = join(projectRoot, 'node_modules', 'vite', 'bin', 'vite.js')

const children = []
const expectedExit = new WeakSet()
let stopping = false
let backend = null
let startedAt = 0
let backendReady = false
let startupWarned = false
let failures = 0
let restarts = 0
let lastRestartAt = 0
let probing = false
let giveUp = false
let monitor = null

function die(message) {
  console.error(`[dev] ${message}`)
  process.exit(1)
}

function killTree(child) {
  if (!child || !child.pid || child.killed) return
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
  if (monitor) clearInterval(monitor)
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
    if (stopping || expectedExit.has(child)) return
    if (name === '后端' && code !== 0) {
      console.error('[dev] 后端未能启动：向上翻终端里的 Traceback，常见原因是 import 报错')
    }
    console.error(`[dev] ${name} 已退出（code=${code ?? 'unknown'}），停止其余进程`)
    stopAll(code ?? 1)
  })
  return child
}

function startBackend() {
  startedAt = Date.now()
  backend = start(
    '后端',
    python,
    ['-m', 'uvicorn', 'app.main:app', '--reload', '--port', String(BACKEND_PORT)],
    { cwd: backendDir },
  )
  return backend
}

function portInUse(port) {
  return new Promise((resolve) => {
    const socket = new Socket()
    const done = (result) => {
      socket.destroy()
      resolve(result)
    }
    socket.setTimeout(500)
    socket.once('connect', () => done(true))
    socket.once('timeout', () => done(false))
    socket.once('error', () => done(false))
    socket.connect(port, '127.0.0.1')
  })
}

/** 直接问后端能不能应答，而不是问端口在不在 */
function probeHealth() {
  return new Promise((resolve) => {
    const req = request(
      {
        host: '127.0.0.1',
        port: BACKEND_PORT,
        path: '/api/health',
        method: 'GET',
        timeout: PROBE_TIMEOUT,
      },
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
  })
}

function restartBackend() {
  const now = Date.now()
  if (now - lastRestartAt > RESTART_WINDOW) restarts = 0
  if (restarts >= MAX_RESTARTS) {
    giveUp = true
    console.error(
      `[dev] 后端一分钟内重启 ${MAX_RESTARTS} 次仍不可用，已停止自动重启。\n` +
        '      查后端日志：backend/logs/app.log；查端口占用：netstat -ano | findstr :8000',
    )
    return
  }

  restarts += 1
  lastRestartAt = now
  failures = 0
  backendReady = false
  startupWarned = false

  console.warn(`[dev] 后端无响应，重启后端（第 ${restarts} 次）…`)
  const dying = backend
  if (dying) expectedExit.add(dying)
  killTree(dying)
  setTimeout(() => {
    if (stopping) return
    startBackend()
  }, RESTART_DELAY)
}

async function tick() {
  if (stopping || probing) return
  probing = true
  const ok = await probeHealth()
  probing = false
  if (stopping) return

  if (ok) {
    failures = 0
    giveUp = false
    if (!backendReady) {
      const cost = ((Date.now() - startedAt) / 1000).toFixed(1)
      console.log(restarts > 0 ? `[dev] 后端已恢复（${cost}s）` : `[dev] 后端已就绪（${cost}s）`)
      backendReady = true
    }
    return
  }

  // 启动期还没就绪属于正常，只要在宽限期内就继续等
  if (!backendReady) {
    if (!startupWarned && Date.now() - startedAt > STARTUP_GRACE) {
      startupWarned = true
      console.error(
        '[dev] 后端 30 秒内没有响应，检查终端里 uvicorn 的 Traceback；' +
          '端口被占的话先执行 netstat -ano | findstr :8000',
      )
    }
    return
  }

  if (giveUp) return

  failures += 1
  console.warn(`[dev] /api/health 无响应（${failures}/${FAIL_THRESHOLD}）`)
  if (failures >= FAIL_THRESHOLD) restartBackend()
}

function startMonitor() {
  monitor = setInterval(() => {
    void tick()
  }, PROBE_INTERVAL)
}

process.on('SIGINT', () => stopAll(0))
process.on('SIGTERM', () => stopAll(0))

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

if (await portInUse(BACKEND_PORT)) {
  die(
    `${BACKEND_PORT} 已被占用，后端会绑定失败。\n` +
      '      查占用进程：netstat -ano | findstr :8000\n' +
      '      常见来源：上一条没退干净的 uvicorn、deploy/ 里解压出来的离线包 start.cmd',
  )
}
if (await portInUse(WEB_PORT)) {
  console.warn(`[dev] ${WEB_PORT} 已被占用，Vite 会自动换到下一个空闲端口，以它打印的地址为准`)
}

console.log('[dev] 后端 http://127.0.0.1:8000   前端 http://127.0.0.1:5173   Ctrl+C 停止')

startBackend()
start('前端', process.execPath, [viteBin], { cwd: projectRoot })
startMonitor()
