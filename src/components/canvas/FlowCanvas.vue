<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  MarkerType,
  Panel,
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
import { canConnect, needsBranch } from '@/domain/rules'
import type { Branch, DocEdge, DocNode, FlowDoc, LintIssue, NodeKind, NodeVisualStatus } from '@/domain/types'
import { NODE_DRAG_TYPE, nodeTypes, type NodeCardData } from '@/components/nodes'
import { edgeTypes, type EdgeBranchData } from '@/components/edges'
import NodePalette from './NodePalette.vue'

const NODE_WIDTH = 220
const NODE_HEIGHT = 96
const EDGE_COLOR = { yes: '#16a34a', no: '#dc2626', none: '#94a3b8' } as const

const props = withDefaults(
  defineProps<{
    doc: FlowDoc
    readonly?: boolean
    issues?: LintIssue[]
    status?: Record<string, NodeVisualStatus>
    selectedNodeId?: string | null
  }>(),
  {
    readonly: false,
    issues: () => [],
    status: () => ({}),
    selectedNodeId: null,
  },
)

const emit = defineEmits<{
  (event: 'update:doc', doc: FlowDoc): void
  (event: 'select-node', id: string | null): void
}>()

// 父组件也要 project/坐标换算，必须与 <VueFlow> 共用同一个 store
const flowId = `canvas-${Math.random().toString(36).slice(2, 8)}`
const { project, dimensions, viewport, setCenter } = useVueFlow(flowId)

const wrapper = ref<HTMLElement | null>(null)
const nodes = ref<Node[]>([])
const edges = ref<Edge[]>([])

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
  const byNode = new Map<string, LintIssue>()
  const byEdge = new Map<string, LintIssue>()
  const put = (target: Map<string, LintIssue>, id: string, issue: LintIssue) => {
    const prev = target.get(id)
    if (!prev || (prev.level === 'warning' && issue.level === 'error')) target.set(id, issue)
  }
  for (const issue of issues) {
    if (issue.nodeId) put(byNode, issue.nodeId, issue)
    else if (issue.edgeId) put(byEdge, issue.edgeId, issue)
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

  const nextNodes: Node[] = []
  for (const meta of nodeMeta.values()) {
    const issue = byNode.get(meta.id)
    const data: NodeCardData = {
      kind: meta.kind,
      title: meta.title ?? '',
      detail: meta.detail ?? '',
      hasBinding: Boolean(meta.binding?.project && meta.binding?.function),
      hasTarget: meta.id !== entry,
      issueLevel: issue?.level ?? null,
      issueMessage: issue?.message ?? '',
      status: props.status[meta.id] ?? null,
      selected: props.selectedNodeId === meta.id,
    }
    nextNodes.push({
      id: meta.id,
      type: 'diag',
      position: live.has(meta.id) ? { ...live.get(meta.id)! } : { ...meta.position },
      data,
      draggable: !props.readonly,
      connectable: !props.readonly,
      deletable: !props.readonly,
    })
  }
  nodes.value = nextNodes

  const nextEdges: Edge[] = []
  for (const meta of edgeMeta.values()) {
    const sourceKind = nodeMeta.get(meta.from)?.kind
    const issue = byEdge.get(meta.id)
    const branch = meta.branch ?? null
    const data: EdgeBranchData = {
      branch,
      issueLevel: issue?.level ?? null,
      issueMessage: issue?.message ?? '',
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
        color: branch ? EDGE_COLOR[branch] : EDGE_COLOR.none,
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
}

watch(
  () => props.doc,
  (doc) => {
    const json = canonical(doc)
    if (json === lastJson) return
    syncMetas(doc)
    lastJson = json
    projectAll(false)
  },
  { deep: true, immediate: true },
)

// 渲染投影（问题标记、执行状态、选中）变化时只重排投影，位置取自画布当前值
watch(
  () => [props.issues, props.status, props.selectedNodeId, props.readonly],
  () => projectAll(true),
  { deep: true },
)

// 外部选中（如执行页点待办定位）只在节点跑到视口外时才移过去，避免点谁跳谁
watch(
  () => props.selectedNodeId,
  (id) => {
    if (!id) return
    const node = nodes.value.find((item) => item.id === id) as GraphNode | undefined
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
    if (visible) return

    void setCenter(node.position.x + width / 2, node.position.y + height / 2, {
      zoom,
      duration: 300,
    })
  },
)

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

// Vue Flow 的删除键会同时改 nodes/edges，这里按 id 差集回收业务字段
watch(nodes, (list) => {
  if (list.length === nodeMeta.size) return
  const alive = new Set(list.map((n) => n.id))
  let removed = false
  for (const id of [...nodeMeta.keys()]) {
    if (alive.has(id)) continue
    nodeMeta.delete(id)
    removed = true
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
  if (list.length === edgeMeta.size) return
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

function onConnect(connection: Connection) {
  if (props.readonly) return
  const source = nodeMeta.get(connection.source)
  const target = nodeMeta.get(connection.target)
  if (!source || !target) return

  if (!canConnect(source.kind, target.kind)) {
    ElMessage.warning(
      `「${KIND_META[source.kind].label}」不能连到「${KIND_META[target.kind].label}」`,
    )
    return
  }

  const branch = needsBranch(source.kind) ? resolveBranch(connection, source) : undefined
  const duplicated = [...edgeMeta.values()].some(
    (e) => e.from === connection.source && e.to === connection.target && (e.branch ?? null) === (branch ?? null),
  )
  if (duplicated) {
    ElMessage.info('这条连线已经存在')
    return
  }

  let order: number | undefined
  if (branch) {
    let max = 0
    for (const edge of edgeMeta.values()) {
      if (edge.from !== connection.source || edge.branch !== branch) continue
      max = Math.max(max, edge.order ?? 0)
    }
    order = max + 1
  }

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

function onNodeClick(event: NodeMouseEvent) {
  emit('select-node', event.node.id)
}

function onPaneClick() {
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
</script>

<template>
  <div
    ref="wrapper"
    class="flow-canvas"
    :class="{ 'is-readonly': readonly }"
    @dragover="onDragOver"
    @drop="onDrop"
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
      @connect="onConnect"
      @node-click="onNodeClick"
      @pane-click="onPaneClick"
      @node-drag-stop="commit"
    >
      <Background :gap="16" :size="1" color="#d8dee9" />
      <Controls :show-interactive="!readonly" />
      <MiniMap pannable zoomable :node-color="minimapColor" :width="160" :height="110" />
      <Panel position="top-left" class="palette-panel">
        <NodePalette :disabled="readonly" @add="(kind) => addNode(kind, centerPosition())" />
      </Panel>
    </VueFlow>
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
</style>
