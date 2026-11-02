<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  MarkerType,
  Panel,
  PanOnScrollMode,
  VueFlow,
  useVueFlow,
  type Connection,
  type Edge,
  type GraphNode,
  type Node,
  type NodeMouseEvent,
} from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { ElMessage } from 'element-plus'
import { KIND_META, V1_KINDS } from '@/domain/kinds'
import { canConnect, isSingleSuccessor, needsBranch } from '@/domain/rules'
import type {
  Branch,
  DocEdge,
  DocNode,
  EdgeTrail,
  FlowDoc,
  LintIssue,
  NodeKind,
  NodeVisualStatus,
} from '@/domain/types'
import { NODE_DRAG_TYPE, nodeTypes, type NodeCardData, type NodeIssue } from '@/components/nodes'
import { edgeTypes, type EdgeBranchData } from '@/components/edges'
import NodePalette from './NodePalette.vue'

const NODE_WIDTH = 220
const NODE_HEIGHT = 96
const EDGE_COLOR = { yes: '#16a34a', no: '#dc2626', none: '#94a3b8', faded: '#cbd5e1' } as const

/** 老流程（doc 里没有 start）在投影层补的占位开始节点，不回写 doc */
const START_GHOST_ID = '__start__'
const START_POSITION = { x: 60, y: 220 }
/** 端口悬停多久后弹出候选面板 */
const TIP_DELAY = 150
const TIP_WIDTH = 176
const TIP_MAX_HEIGHT = 240

/** 可与给定节点相连的节点类型，已过连接矩阵与类型规则 */
function candidateKinds(sourceId: string): NodeKind[] {
  const source = nodeMeta.get(sourceId)
  if (!source) return []
  const meta = KIND_META[source.kind]
  if (!meta || meta.branch === 'none') return []
  // 结论只允许一个后继，已有出线就不再给候选
  if (isSingleSuccessor(source.kind)) {
    for (const edge of edgeMeta.values()) if (edge.from === sourceId) return []
  }
  return (Object.keys(KIND_META) as NodeKind[]).filter(
    (kind) => !KIND_META[kind].entry && kind !== 'start' && canConnect(source.kind, kind),
  )
}

interface HoverTip {
  sourceId: string
  /** 悬停的是哪个出口；单出口节点为 null */
  branch: Branch | null
  left: number
  top: number
  /** 悬停 handle 的中心点，建节点时换算成画布坐标 */
  clientX: number
  clientY: number
  kinds: NodeKind[]
}

const props = withDefaults(
  defineProps<{
    doc: FlowDoc
    readonly?: boolean
    issues?: LintIssue[]
    status?: Record<string, NodeVisualStatus>
    selectedNodeId?: string | null
    /** 每个节点名下的运行记录条数；执行页传入后节点上才出现执行入口 */
    runCounts?: Record<string, number>
    /** 每个节点名下的人工待办数；人工节点没有 run，只能靠它给入口 */
    reviewCounts?: Record<string, number>
    /** 本次执行实际走过的边；传了才在画布上区分「走过」与「没走」 */
    trails?: Record<string, EdgeTrail>
  }>(),
  {
    readonly: false,
    issues: () => [],
    status: () => ({}),
    selectedNodeId: null,
    runCounts: () => ({}),
    reviewCounts: () => ({}),
    trails: () => ({}),
  },
)

const emit = defineEmits<{
  (event: 'update:doc', doc: FlowDoc): void
  (event: 'select-node', id: string | null): void
  (event: 'inspect-node', id: string): void
}>()

// 父组件也要 project/坐标换算，必须与 <VueFlow> 共用同一个 store
const flowId = `canvas-${Math.random().toString(36).slice(2, 8)}`
const {
  project,
  dimensions,
  viewport,
  setCenter,
  connectionStartHandle,
  fitView,
  zoomIn,
  zoomOut,
  zoomTo,
} = useVueFlow(flowId)

const wrapper = ref<HTMLElement | null>(null)
const nodes = ref<Node[]>([])
const edges = ref<Edge[]>([])

/** doc 里没有开始节点时的占位投影；只存在渲染层，不参与 commit */
const ghostStart = ref<DocNode | null>(null)
const tip = ref<HoverTip | null>(null)
let hoverTimer: ReturnType<typeof setTimeout> | null = null
let closeTimer: ReturnType<typeof setTimeout> | null = null

/** 业务字段的唯一副本；画布上的 nodes/edges 只是渲染投影 */
const nodeMeta = new Map<string, DocNode>()
const edgeMeta = new Map<string, DocEdge>()

/** 上一次与外部对齐的 doc 指纹，用于打断 更新 → watch → 再更新 的回环 */
let lastJson = ''
let idSeq = 0
let cascade = 0

function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  const record = value as Record<string, unknown>
  const keys = Object.keys(record)
    .filter((key) => record[key] !== undefined)
    .sort()
  return `{${keys.map((key) => `${JSON.stringify(key)}:${canonical(record[key])}`).join(',')}}`
}

function issueIndex(issues: LintIssue[]) {
  const byNode = new Map<string, NodeIssue[]>()
  const byEdge = new Map<string, LintIssue>()
  for (const issue of issues) {
    if (issue.nodeId) {
      const list = byNode.get(issue.nodeId) ?? []
      list.push({ level: issue.level, message: issue.message })
      byNode.set(issue.nodeId, list)
    } else if (issue.edgeId) {
      const prev = byEdge.get(issue.edgeId)
      if (!prev || (prev.level === 'warning' && issue.level === 'error')) {
        byEdge.set(issue.edgeId, issue)
      }
    }
  }
  for (const list of byNode.values()) {
    list.sort((a, b) => (a.level === b.level ? 0 : a.level === 'error' ? -1 : 1))
  }
  return { byNode, byEdge }
}

/** 入口 = 入度为 0 且可达节点最多的那个；避免刚被断开的节点抢走入口身份 */
function resolveEntry(): string | null {
  const indegree = new Map<string, number>()
  const successors = new Map<string, string[]>()
  for (const id of nodeMeta.keys()) {
    indegree.set(id, 0)
    successors.set(id, [])
  }
  for (const edge of edgeMeta.values()) {
    if (!indegree.has(edge.to) || !successors.has(edge.from)) continue
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1)
    successors.get(edge.from)!.push(edge.to)
  }

  const roots = [...nodeMeta.keys()].filter((id) => indegree.get(id) === 0)
  if (roots.length <= 1) return roots[0] ?? null

  let best = roots[0]
  let bestCount = -1
  for (const id of roots) {
    const seen = new Set<string>([id])
    const stack = [...(successors.get(id) ?? [])]
    let count = 0
    while (stack.length) {
      const next = stack.pop()!
      if (seen.has(next)) continue
      seen.add(next)
      count += 1
      stack.push(...(successors.get(next) ?? []))
    }
    if (count > bestCount) {
      best = id
      bestCount = count
    }
  }
  return best
}

function projectAll(preservePositions: boolean) {
  const entry = resolveEntry()
  const live = new Map<string, { x: number; y: number }>()
  if (preservePositions) {
    for (const node of nodes.value) live.set(node.id, { x: node.position.x, y: node.position.y })
  }
  const { byNode, byEdge } = issueIndex(props.issues)

  // 选中节点时，只高亮与它直接相连的节点和边；方向由边的 from/to 决定
  const selected = props.selectedNodeId
  const upstream = new Set<string>()
  const downstream = new Set<string>()
  const flowingEdges = new Set<string>()
  if (selected) {
    for (const meta of edgeMeta.values()) {
      if (meta.to === selected) {
        if (meta.from !== selected) upstream.add(meta.from)
        flowingEdges.add(meta.id)
      }
      if (meta.from === selected) {
        if (meta.to !== selected) downstream.add(meta.to)
        flowingEdges.add(meta.id)
      }
    }
  }

  const nextNodes: Node[] = []
  // 只有执行页会传 status；编辑页没数据时不能压暗，否则整张图都是灰的
  const hasRun = Object.keys(props.status).length > 0
  const pushNode = (
    meta: DocNode,
    extra: { ghost?: boolean } = {},
  ) => {
    const isStart = meta.kind === 'start'
    const status = props.status[meta.id] ?? null
    const data: NodeCardData = {
      kind: meta.kind,
      title: meta.title ?? '',
      detail: meta.detail ?? '',
      hasBinding: Boolean(meta.binding?.project && meta.binding?.function),
      hasTarget: !isStart && meta.id !== entry,
      paramCount: isStart ? (meta.inputs?.length ?? 0) : undefined,
      issues: byNode.get(meta.id) ?? [],
      flow:
        meta.id === selected
          ? null
          : upstream.has(meta.id)
            ? 'upstream'
            : downstream.has(meta.id)
              ? 'downstream'
              : null,
      status,
      selected: props.selectedNodeId === meta.id,
      dimmed: hasRun && (status === null || status === 'pending'),
      runCount: props.runCounts[meta.id] ?? 0,
      reviewCount: props.reviewCounts[meta.id] ?? 0,
    }
    nextNodes.push({
      id: meta.id,
      type: 'diag',
      position: live.has(meta.id) ? { ...live.get(meta.id)! } : { ...meta.position },
      data,
      draggable: !props.readonly && !extra.ghost,
      connectable: !props.readonly && !extra.ghost,
      // 开始节点是流程入口，任何操作都不允许删掉它
      deletable: !props.readonly && !isStart && !extra.ghost,
    })
  }

  for (const meta of nodeMeta.values()) pushNode(meta)
  if (ghostStart.value) pushNode(ghostStart.value, { ghost: true })
  nodes.value = nextNodes

  const nextEdges: Edge[] = []
  for (const meta of edgeMeta.values()) {
    const sourceKind = nodeMeta.get(meta.from)?.kind
    const issue = byEdge.get(meta.id)
    const branch = meta.branch ?? null
    const trail = props.trails[meta.id] ?? null
    const faded = hasRun && !trail
    const data: EdgeBranchData = {
      branch,
      issueLevel: issue?.level ?? null,
      issueMessage: issue?.message ?? '',
      flowing: flowingEdges.has(meta.id),
      trail,
      faded,
    }
    nextEdges.push({
      id: meta.id,
      type: 'branch',
      source: meta.from,
      target: meta.to,
      sourceHandle: sourceKind && needsBranch(sourceKind) ? (branch === 'no' ? 'no' : 'yes') : undefined,
      targetHandle: 'in',
      deletable: !props.readonly,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        // 箭头是独立 marker，不跟着 path 的 opacity 变淡，得单独给灰
        color: faded ? EDGE_COLOR.faded : branch ? EDGE_COLOR[branch] : EDGE_COLOR.none,
        width: 16,
        height: 16,
      },
      data,
    })
  }
  edges.value = nextEdges
}

function syncMetas(doc: FlowDoc) {
  nodeMeta.clear()
  edgeMeta.clear()
  for (const node of doc.nodes ?? []) {
    nodeMeta.set(node.id, { ...node, position: { ...node.position } })
  }
  for (const edge of doc.edges ?? []) {
    edgeMeta.set(edge.id, { ...edge })
  }
  // 老流程 / 空文档没有开始节点时补一个占位；已存在就不再补
  const hasStart = [...nodeMeta.values()].some((node) => node.kind === 'start')
  ghostStart.value = hasStart
    ? null
    : {
        id: START_GHOST_ID,
        kind: 'start',
        title: KIND_META.start.label,
        detail: '',
        inputs: [],
        position: { ...START_POSITION },
      }
}

watch(
  () => props.doc,
  (doc) => {
    const json = canonical(doc)
    if (json === lastJson) return
    hideTip()
    syncMetas(doc)
    lastJson = json
    projectAll(false)
  },
  { deep: true, immediate: true },
)

// 渲染投影（问题标记、执行状态、选中）变化时只重排投影，位置取自画布当前值
watch(
  () => [
    props.issues,
    props.status,
    props.selectedNodeId,
    props.readonly,
    props.runCounts,
    props.reviewCounts,
    props.trails,
  ],
  () => projectAll(true),
  { deep: true },
)

/** 视口外的节点移进来；已在视口内时保持不动，避免点谁跳谁 */
function centerOn(id: string, force = false) {
  // 只取取景需要的两个字段，也避开 Array.find 对 GraphNode 深层泛型的推导
  let node: { position: { x: number; y: number }; dimensions?: { width?: number; height?: number } } | undefined
  for (const item of nodes.value) {
    if (item.id === id) {
      node = item as unknown as typeof node
      break
    }
  }
  if (!node) return
  const { zoom } = viewport.value
  const width = node.dimensions?.width || NODE_WIDTH
  const height = node.dimensions?.height || NODE_HEIGHT
  const left = node.position.x * zoom + viewport.value.x
  const top = node.position.y * zoom + viewport.value.y
  const view = dimensions.value
  const margin = 32
  const visible =
    left >= margin &&
    top >= margin &&
    left + width * zoom <= view.width - margin &&
    top + height * zoom <= view.height - margin
  if (visible && !force) return
  void setCenter(node.position.x + width / 2, node.position.y + height / 2, { zoom, duration: 300 })
}

// 外部选中（如执行页点待办定位）只在节点跑到视口外时才移过去
watch(
  () => props.selectedNodeId,
  (id) => {
    if (!id) return
    centerOn(id)
  },
)

/** 执行中的节点：并行时可能不止一个，运行中优先于待处理 */
const activeIds = computed(() => {
  const running: string[] = []
  const waiting: string[] = []
  for (const [id, value] of Object.entries(props.status)) {
    if (value === 'running') running.push(id)
    else if (value === 'waiting') waiting.push(id)
  }
  return running.length > 0 ? running : waiting
})

const activeLabel = computed(() => {
  if (!activeIds.value.length) return ''
  const prefix = props.status[activeIds.value[0]] === 'running' ? '运行中' : '待处理'
  return activeIds.value.length > 1 ? `${prefix} ${activeIds.value.length}` : prefix
})

function focusActive() {
  const id = activeIds.value[0]
  if (id) centerOn(id, true)
}

function commit() {
  const docNodes: DocNode[] = []
  for (const node of nodes.value) {
    const meta = nodeMeta.get(node.id)
    if (!meta) continue
    docNodes.push({
      ...meta,
      position: { x: Math.round(node.position.x), y: Math.round(node.position.y) },
    })
  }
  const docEdges: DocEdge[] = []
  for (const edge of edges.value) {
    const meta = edgeMeta.get(edge.id)
    if (!meta) continue
    docEdges.push({ ...meta, from: edge.source, to: edge.target })
  }

  // 顶层字段（name/mode/snapshotFields 及将来新增的）原样带过去，画布只管 nodes/edges
  const next: FlowDoc = {
    ...props.doc,
    format: 1,
    nodes: docNodes,
    edges: docEdges,
  }
  // 没填快照字段时不要凭空生出这个键
  if (next.snapshotFields === undefined) delete next.snapshotFields
  const json = canonical(next)
  if (json === lastJson) return
  lastJson = json
  emit('update:doc', next)
}

// Vue Flow 的删除键会同时改 nodes/edges，这里按 id 差集回收业务字段。
// 开始节点被删时只恢复投影、不改 doc（占位节点同样不在 nodeMeta 里）
watch(nodes, (list) => {
  const alive = new Set(list.map((n) => n.id))
  let removed = false
  let blockedStart = false
  for (const [id, meta] of [...nodeMeta]) {
    if (alive.has(id)) continue
    if (meta.kind === 'start') {
      blockedStart = true
      continue
    }
    nodeMeta.delete(id)
    removed = true
  }
  if (blockedStart) {
    ElMessage.warning('开始节点是流程入口，不能删除')
    hideTip()
    projectAll(true)
  }
  if (!removed) return
  for (const id of [...edgeMeta.keys()]) {
    const edge = edgeMeta.get(id)
    if (edge && (!alive.has(edge.from) || !alive.has(edge.to))) edgeMeta.delete(id)
  }
  projectAll(true)
  commit()
})

watch(edges, (list) => {
  const alive = new Set(list.map((e) => e.id))
  let removed = false
  for (const id of [...edgeMeta.keys()]) {
    if (alive.has(id)) continue
    edgeMeta.delete(id)
    removed = true
  }
  if (!removed) return
  projectAll(true)
  commit()
})

function nextId(prefix: string) {
  let id = ''
  do {
    idSeq += 1
    id = `${prefix}${Date.now().toString(36)}${idSeq}`
  } while (nodeMeta.has(id) || edgeMeta.has(id))
  return id
}

/** 默认标题按类型递增，避免一加节点就满屏「标题缺失」 */
function defaultTitle(kind: NodeKind) {
  let count = 0
  for (const meta of nodeMeta.values()) if (meta.kind === kind) count += 1
  return `${KIND_META[kind].label} ${count + 1}`
}

function addNode(kind: NodeKind, position: { x: number; y: number }) {
  if (props.readonly) return
  const node: DocNode = { id: nextId('n'), kind, title: defaultTitle(kind), detail: '', position }
  nodeMeta.set(node.id, node)
  projectAll(true)
  emit('select-node', node.id)
  commit()
}

function centerPosition() {
  const size = dimensions.value
  const center = project({ x: size.width / 2, y: size.height / 2 })
  const step = (cascade % 6) * 26
  cascade += 1
  return {
    x: Math.round(center.x - NODE_WIDTH / 2 + step),
    y: Math.round(center.y - NODE_HEIGHT / 2 + step),
  }
}

function resolveBranch(connection: Connection, source: DocNode): Branch {
  if (connection.sourceHandle === 'yes' || connection.sourceHandle === 'no') {
    return connection.sourceHandle
  }
  const hasYes = [...edgeMeta.values()].some((e) => e.from === source.id && e.branch === 'yes')
  return hasYes ? 'no' : 'yes'
}

/** 分支组内自增序号；只有排查节点才分组 */
function nextOrder(sourceId: string, branch: Branch | undefined): number | undefined {
  if (!branch) return undefined
  let max = 0
  for (const edge of edgeMeta.values()) {
    if (edge.from !== sourceId || edge.branch !== branch) continue
    max = Math.max(max, edge.order ?? 0)
  }
  return max + 1
}

function onConnect(connection: Connection) {
  if (props.readonly) return
  // 开始节点是唯一入口，任何连线都不能指回来
  if (connection.target === START_GHOST_ID || nodeMeta.get(connection.target)?.kind === 'start') {
    ElMessage.warning('任何连线都不能指向开始节点')
    return
  }
  const source = nodeMeta.get(connection.source)
  const target = nodeMeta.get(connection.target)
  if (!source || !target) return

  if (!canConnect(source.kind, target.kind)) {
    ElMessage.warning(
      `「${KIND_META[source.kind].label}」不能连到「${KIND_META[target.kind].label}」`,
    )
    return
  }
  if (KIND_META[source.kind].branch === 'none') {
    ElMessage.warning(`「${KIND_META[source.kind].label}」是终止节点，不能再出线`)
    return
  }

  // start 只有一条 default 出边，不写 branch；排查按拖拽的出口分组
  const branch = needsBranch(source.kind) ? resolveBranch(connection, source) : undefined
  const duplicated = [...edgeMeta.values()].some(
    (e) => e.from === connection.source && e.to === connection.target && (e.branch ?? null) === (branch ?? null),
  )
  if (duplicated) {
    ElMessage.info('这条连线已经存在')
    return
  }

  const order = nextOrder(connection.source, branch)
  const edge: DocEdge = {
    id: nextId('e'),
    from: connection.source,
    to: connection.target,
    ...(branch ? { branch } : {}),
    ...(order ? { order } : {}),
  }
  edgeMeta.set(edge.id, edge)
  projectAll(true)
  commit()
}

/* ---------- 端口悬停候选面板 ---------- */

function clearHoverTimer() {
  if (hoverTimer) clearTimeout(hoverTimer)
  hoverTimer = null
}

function clearCloseTimer() {
  if (closeTimer) clearTimeout(closeTimer)
  closeTimer = null
}

function hideTip() {
  clearHoverTimer()
  clearCloseTimer()
  tip.value = null
}

/** 鼠标离开 handle 后留一点时间，允许移进浮层继续点击 */
function scheduleClose() {
  clearCloseTimer()
  closeTimer = setTimeout(() => {
    closeTimer = null
    tip.value = null
  }, 160)
}

const handleBranch = (id: string | null): Branch | null => (id === 'yes' || id === 'no' ? id : null)

function openTip(sourceId: string, branch: Branch | null, handle: HTMLElement) {
  const rect = wrapper.value?.getBoundingClientRect()
  if (!rect) return
  const handleRect = handle.getBoundingClientRect()
  // 浮层挂在画布容器上（绝对定位），不跟着画布缩放
  tip.value = {
    sourceId,
    branch,
    left: Math.min(Math.max(handleRect.right - rect.left + 12, 8), Math.max(8, rect.width - TIP_WIDTH - 8)),
    top: Math.min(
      Math.max(handleRect.top - rect.top - 8, 8),
      Math.max(8, rect.height - TIP_MAX_HEIGHT - 8),
    ),
    clientX: handleRect.left + handleRect.width / 2,
    clientY: handleRect.top + handleRect.height / 2,
    kinds: candidateKinds(sourceId),
  }
}

/** 用事件委托识别 handle：Handle 渲染成带 data-nodeid / source 类的 div */
function onCanvasMouseOver(event: MouseEvent) {
  if (props.readonly) return
  // 正在拖连线时不要再弹候选面板
  if (connectionStartHandle.value) return
  const target = event.target as HTMLElement | null
  const handle = target?.closest?.('.vue-flow__handle') as HTMLElement | null
  if (!handle || !handle.classList.contains('source') || !handle.classList.contains('connectable')) return
  const nodeId = handle.dataset.nodeid
  if (!nodeId) return

  const branch = handleBranch(handle.dataset.handleid ?? null)
  if (tip.value && tip.value.sourceId === nodeId && tip.value.branch === branch) {
    clearCloseTimer()
    return
  }
  clearHoverTimer()
  clearCloseTimer()
  hoverTimer = setTimeout(() => {
    hoverTimer = null
    openTip(nodeId, branch, handle)
  }, TIP_DELAY)
}

function onCanvasMouseOut(event: MouseEvent) {
  const target = event.target as HTMLElement | null
  if (!target?.closest?.('.vue-flow__handle')) return
  const to = event.relatedTarget as HTMLElement | null
  if (to && typeof to.closest === 'function' && to.closest('.connect-tip')) return
  clearHoverTimer()
  if (tip.value) scheduleClose()
}

const tipTitle = computed(() => {
  const state = tip.value
  if (!state) return ''
  const source = nodeMeta.get(state.sourceId)
  if (!source) return ''
  const label = KIND_META[source.kind]?.label ?? ''
  if (state.branch) return `${label} · ${state.branch === 'yes' ? '是' : '否'}`
  return label
})

/** 点候选类型：在 handle 右侧建节点，并立刻连一条边 */
function tipCreate(kind: NodeKind) {
  const state = tip.value
  hideTip()
  if (!state || props.readonly) return
  const source = nodeMeta.get(state.sourceId)
  if (!source) return
  if (!canConnect(source.kind, kind)) {
    ElMessage.warning(
      `「${KIND_META[source.kind].label}」不能连到「${KIND_META[kind].label}」`,
    )
    return
  }

  const rect = wrapper.value?.getBoundingClientRect()
  const point = project({
    x: state.clientX - (rect?.left ?? 0),
    y: state.clientY - (rect?.top ?? 0),
  })
  const node: DocNode = {
    id: nextId('n'),
    kind,
    title: defaultTitle(kind),
    detail: '',
    position: {
      x: Math.round(point.x + 48),
      y: Math.round(point.y - NODE_HEIGHT / 2),
    },
  }
  nodeMeta.set(node.id, node)

  const branch = needsBranch(source.kind) ? (state.branch ?? 'yes') : undefined
  const order = nextOrder(source.id, branch)
  const edge: DocEdge = {
    id: nextId('e'),
    from: source.id,
    to: node.id,
    ...(branch ? { branch } : {}),
    ...(order ? { order } : {}),
  }
  edgeMeta.set(edge.id, edge)

  projectAll(true)
  emit('select-node', node.id)
  commit()
}

/** 卡片上的执行入口是 DOM 里的一小块，靠事件委托区分「看详情」和「选中」 */
function onNodeClick(event: NodeMouseEvent) {
  hideTip()
  const target = event.event?.target as HTMLElement | null
  if (target?.closest?.('.node-action')) {
    emit('inspect-node', event.node.id)
    return
  }
  emit('select-node', event.node.id)
}

function onPaneClick() {
  hideTip()
  emit('select-node', null)
}

function onDragOver(event: DragEvent) {
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
}

function onDrop(event: DragEvent) {
  if (props.readonly) return
  event.preventDefault()
  const raw =
    event.dataTransfer?.getData(NODE_DRAG_TYPE) || event.dataTransfer?.getData('text/plain') || ''
  const kind = V1_KINDS.find((item) => item === raw)
  if (!kind) return

  const rect = wrapper.value?.getBoundingClientRect()
  const point = project({
    x: event.clientX - (rect?.left ?? 0),
    y: event.clientY - (rect?.top ?? 0),
  })
  addNode(kind, {
    x: Math.round(point.x - NODE_WIDTH / 2),
    y: Math.round(point.y - NODE_HEIGHT / 2),
  })
}

const minimapColor = (node: GraphNode) => {
  const kind = (node.data as NodeCardData | undefined)?.kind
  return kind ? KIND_META[kind].color : EDGE_COLOR.none
}

onBeforeUnmount(() => {
  clearHoverTimer()
  clearCloseTimer()
})

/** 缩放百分比给父组件的操作栏读数用，取整避免 99.999% 这种抖动 */
const zoomPercent = computed(() => Math.round(viewport.value.zoom * 100))

function resetZoom() {
  void zoomTo(1, { duration: 200 })
}

// 自动布局后坐标整批换掉，需要外部触发重新取景；fitView 只在本组件的 store 上有效
defineExpose({ fitView, zoomPercent, zoomIn, zoomOut, resetZoom })
</script>

<template>
  <div
    ref="wrapper"
    class="flow-canvas"
    :class="{ 'is-readonly': readonly }"
    @dragover="onDragOver"
    @drop="onDrop"
    @mouseover="onCanvasMouseOver"
    @mouseout="onCanvasMouseOut"
    @wheel.passive="hideTip"
  >
    <VueFlow
      :id="flowId"
      v-model:nodes="nodes"
      v-model:edges="edges"
      :node-types="nodeTypes"
      :edge-types="edgeTypes"
      :nodes-draggable="!readonly"
      :nodes-connectable="!readonly"
      :edges-updatable="false"
      :delete-key-code="readonly ? null : 'Delete'"
      :min-zoom="0.2"
      :max-zoom="2"
      :fit-view-on-init="true"
      :pan-on-scroll="true"
      :pan-on-scroll-mode="PanOnScrollMode.Free"
      :zoom-on-scroll="false"
      zoom-activation-key-code="Control"
      @connect="onConnect"
      @connect-start="hideTip"
      @node-click="onNodeClick"
      @pane-click="onPaneClick"
      @node-drag-stop="commit"
    >
      <Background :gap="16" :size="1" color="#d8dee9" />
      <Controls :show-interactive="!readonly" />
      <MiniMap pannable zoomable :node-color="minimapColor" :width="160" :height="110" />
      <Panel v-if="!readonly" position="top-left" class="palette-panel">
        <NodePalette :disabled="readonly" @add="(kind) => addNode(kind, centerPosition())" />
      </Panel>
      <Panel v-if="activeIds.length" position="top-right" class="focus-panel">
        <button type="button" class="focus-chip" @click="focusActive">
          <span class="focus-dot" />
          定位{{ activeLabel }}
        </button>
      </Panel>
    </VueFlow>

    <div
      v-if="tip"
      class="connect-tip"
      :style="{ left: `${tip.left}px`, top: `${tip.top}px` }"
      @mouseenter="clearCloseTimer"
      @mouseleave="hideTip"
    >
      <div class="connect-tip-head">{{ tipTitle }}</div>
      <div class="connect-tip-list">
        <button
          v-for="kind in tip.kinds"
          :key="kind"
          type="button"
          class="connect-tip-item"
          @click="tipCreate(kind)"
        >
          <span class="connect-tip-swatch" :style="{ background: KIND_META[kind].color }" />
          <span>{{ KIND_META[kind].label }}</span>
        </button>
        <div v-if="!tip.kinds.length" class="connect-tip-empty">
          这里没有可添加的节点类型
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.flow-canvas {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 320px;
  background: var(--el-bg-color);
}

.palette-panel {
  margin: 12px;
}

/* 画布缩到看不清卡片时，这颗按钮是唯一的「当前在哪」硬入口 */
.focus-panel {
  margin: 12px;
}

.focus-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 6px;
  background: var(--el-color-primary-light-9);
  font: inherit;
  font-size: 12px;
  color: var(--el-color-primary-dark-2);
  cursor: pointer;
}

.focus-chip:hover {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-8);
}

.focus-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-primary);
  animation: focus-pulse 1.2s ease-in-out infinite;
}

@keyframes focus-pulse {
  50% {
    opacity: 0.3;
  }
}

/* 端口候选面板挂在画布容器上，绝对定位，不受画布缩放影响 */
.connect-tip {
  position: absolute;
  z-index: 12;
  width: 176px;
  padding: 6px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color-overlay);
  box-shadow: 0 6px 18px rgb(15 23 42 / 16%);
  font-size: 12px;
}

.connect-tip-head {
  padding: 2px 4px 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.connect-tip-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 208px;
  overflow-y: auto;
}

.connect-tip-item {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 5px 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: none;
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.connect-tip-item:hover {
  border-color: var(--el-border-color);
  background: var(--el-fill-color-light);
}

.connect-tip-swatch {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.connect-tip-empty {
  padding: 6px;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}
</style>
