import type { DocNode, FlowDoc } from '@/domain/types'

const COL_GAP = 280
const ROW_GAP = 120

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** position 在类型上必填，导入的老文档却可能整个缺掉，或带着字符串坐标 */
function isPositioned(node: DocNode): boolean {
  const position = (node as { position?: { x?: unknown; y?: unknown } } | null)?.position
  if (!position || typeof position !== 'object') return false
  return isFiniteNumber(position.x) && isFiniteNumber(position.y)
}

/**
 * BFS 分层：列按最长路径深度，行按文档顺序。
 * 环里的节点没有稳定深度，与孤立节点一起放到最后一列。
 */
export function layoutDoc(doc: FlowDoc): FlowDoc {
  const nodes = doc.nodes ?? []
  const edges = doc.edges ?? []

  if (nodes.length === 0) {
    return { ...doc, nodes: [], edges: edges.map((e) => ({ ...e })) }
  }

  const indegree = new Map<string, number>()
  const successors = new Map<string, string[]>()
  for (const node of nodes) {
    indegree.set(node.id, 0)
    successors.set(node.id, [])
  }
  for (const edge of edges) {
    if (!successors.has(edge.from) || !indegree.has(edge.to)) continue
    successors.get(edge.from)!.push(edge.to)
    indegree.set(edge.to, indegree.get(edge.to)! + 1)
  }

  const depth = new Map<string, number>()
  const queue: string[] = []
  for (const node of nodes) {
    if (indegree.get(node.id) !== 0) continue
    // 没有任何连线的孤立节点不占首列，统一挪到最后一列
    if ((successors.get(node.id) ?? []).length === 0) continue
    depth.set(node.id, 0)
    queue.push(node.id)
  }

  for (let head = 0; head < queue.length; head += 1) {
    const id = queue[head]
    const current = depth.get(id) ?? 0
    for (const next of successors.get(id) ?? []) {
      depth.set(next, Math.max(depth.get(next) ?? 0, current + 1))
      const remaining = (indegree.get(next) ?? 1) - 1
      indegree.set(next, remaining)
      if (remaining === 0) queue.push(next)
    }
  }

  const columns = new Map<number, DocNode[]>()
  const pending: DocNode[] = []
  for (const node of nodes) {
    const level = depth.get(node.id)
    if (level === undefined) {
      pending.push(node)
      continue
    }
    const column = columns.get(level) ?? []
    column.push(node)
    columns.set(level, column)
  }

  let lastLevel = -1
  for (const level of columns.keys()) lastLevel = Math.max(lastLevel, level)
  if (pending.length > 0) {
    const column = columns.get(lastLevel + 1) ?? []
    column.push(...pending)
    columns.set(lastLevel + 1, column)
  }

  const position = new Map<string, { x: number; y: number }>()
  for (const [level, column] of columns) {
    column.forEach((node, row) => {
      position.set(node.id, { x: level * COL_GAP, y: row * ROW_GAP })
    })
  }

  return {
    ...doc,
    nodes: nodes.map((node) => ({
      ...node,
      position: position.get(node.id) ?? { ...node.position },
    })),
    edges: edges.map((edge) => ({ ...edge })),
  }
}

/**
 * 运行时兜底：只要有节点缺合法坐标就整图重排。
 * 只补缺失节点会让新旧坐标混排，连线更容易穿过卡片，不如整体重来。
 */
export function ensureLayout(doc: FlowDoc): { doc: FlowDoc; changed: boolean } {
  const nodes = doc.nodes ?? []
  if (nodes.every(isPositioned)) return { doc, changed: false }
  return { doc: layoutDoc(doc), changed: true }
}
