import { onScopeDispose, ref, watch, type Ref } from 'vue'
import { KIND_META, isKnownKind, isTerminalKind } from '@/domain/kinds'
import { canConnect, isSingleSuccessor, needsBranch } from '@/domain/rules'
import type { DocEdge, DocNode, FlowDoc, InvestigationFields, LintIssue, SlotCall } from '@/domain/types'

const FIELD_LABELS: Array<[keyof InvestigationFields, string]> = [
  ['observation', '观察对象'],
  ['acquisition', '取数方式'],
  ['assessment', '判定依据'],
]

const displayName = (node: DocNode) =>
  node.title?.trim() || `未命名${KIND_META[node.kind]?.label ?? '节点'}`

const isInvestigation = (node: DocNode) => needsBranch(node.kind)

function indexEdges(nodes: DocNode[], edges: DocEdge[]) {
  const incoming = new Map<string, DocEdge[]>()
  const outgoing = new Map<string, DocEdge[]>()
  for (const node of nodes) {
    incoming.set(node.id, [])
    outgoing.set(node.id, [])
  }
  for (const edge of edges) {
    outgoing.get(edge.from)?.push(edge)
    incoming.get(edge.to)?.push(edge)
  }
  return { incoming, outgoing }
}

/** Tarjan 求强连通分量；返回的每个分量都至少含一个环 */
function findCycles(nodes: DocNode[], outgoing: Map<string, DocEdge[]>): string[][] {
  const index = new Map<string, number>()
  const low = new Map<string, number>()
  const onStack = new Set<string>()
  const stack: string[] = []
  const cycles: string[][] = []
  let counter = 0

  const visit = (id: string) => {
    index.set(id, counter)
    low.set(id, counter)
    counter += 1
    stack.push(id)
    onStack.add(id)

    for (const edge of outgoing.get(id) ?? []) {
      const next = edge.to
      if (!outgoing.has(next)) continue
      if (!index.has(next)) {
        visit(next)
        low.set(id, Math.min(low.get(id)!, low.get(next)!))
      } else if (onStack.has(next)) {
        low.set(id, Math.min(low.get(id)!, index.get(next)!))
      }
    }

    if (low.get(id) !== index.get(id)) return
    const component: string[] = []
    let item = id
    do {
      item = stack.pop()!
      onStack.delete(item)
      component.push(item)
    } while (item !== id)

    const selfLoop = (outgoing.get(component[0]) ?? []).some((e) => e.to === component[0])
    if (component.length > 1 || selfLoop) cycles.push(component)
  }

  for (const node of nodes) if (!index.has(node.id)) visit(node.id)
  return cycles
}

function reachableFrom(startIds: string[], next: (id: string) => string[]): Set<string> {
  const seen = new Set<string>()
  const stack = [...startIds]
  while (stack.length) {
    const id = stack.pop()!
    if (seen.has(id)) continue
    seen.add(id)
    stack.push(...next(id))
  }
  return seen
}

export function lintDoc(doc: FlowDoc): LintIssue[] {
  const issues: LintIssue[] = []
  const nodes = doc.nodes ?? []
  const edges = doc.edges ?? []

  for (const node of nodes) {
    if (!isKnownKind(node.kind)) {
      issues.push({ id: `node:${node.id}:kind`, level: 'error', nodeId: node.id,
        message: `节点类型「${node.kind}」已移除，请重新编排这个节点` })
    }
  }
  const startSlots = nodes.find((n) => n.kind === 'start')?.slots
  const objectNames = new Set(
    (startSlots?.constructs ?? []).map((item) => item.object).filter(Boolean) as string[],
  )
  const parameterNames = (node: DocNode) => new Set((node.runtimeInputs ?? []).map((item) => item.name))
  const usesPrevious = (value: unknown): boolean => {
    if (!value || typeof value !== 'object') return false
    const argument = value as Record<string, unknown>
    if (argument.source === 'previous') return true
    return ((argument.items as unknown[]) ?? []).some(usesPrevious)
      || Object.values((argument.fields as Record<string, unknown>) ?? {}).some(usesPrevious)
  }
  const previousIn = (call?: SlotCall): boolean =>
    Boolean(call) && Object.values(call?.arguments ?? {}).some(usesPrevious)
  const collectSources = (value: unknown, names: Set<string>): void => {
    if (!value || typeof value !== 'object') return
    const argument = value as Record<string, unknown>
    if (argument.source === 'parameter' && typeof argument.name === 'string') names.add(argument.name)
    for (const item of (argument.items as unknown[]) ?? []) collectSources(item, names)
    for (const item of Object.values((argument.fields as Record<string, unknown>) ?? {})) collectSources(item, names)
  }
  for (const node of nodes) {
    const slots = node.slots ?? {}
    if (node.kind === 'investigation') {
      if (!slots.present) {
        issues.push({ id: `node:${node.id}:present`, nodeId: node.id, level: 'error',
          message: '排查节点缺少展示槽：必须选择展示方法和展示类型' })
      } else if (!slots.present.view) {
        issues.push({ id: `node:${node.id}:view`, nodeId: node.id, level: 'error',
          message: '展示槽还没有选择展示类型' })
      }
      if (!slots.decide) {
        issues.push({ id: `node:${node.id}:decide`, nodeId: node.id, level: 'error',
          message: '排查节点缺少判定槽：选判定方法或人工判断' })
      }
    }
    if (previousIn(slots.process) && !slots.fetch) {
      issues.push({ id: `node:${node.id}:previous:process`, nodeId: node.id, level: 'error',
        message: '处理槽要用上一槽结果，但这个节点没有拉取槽' })
    }
    if ((previousIn(slots.present) || previousIn(slots.decide)) && !slots.fetch && !slots.process) {
      issues.push({ id: `node:${node.id}:previous:value`, nodeId: node.id, level: 'error',
        message: '要用上一槽结果，但这个节点没有拉取或处理槽：下一节点的数据要自己取' })
    }
    const calls = [...(slots.constructs ?? []),
      ...[slots.fetch, slots.process, slots.present, slots.decide].filter(Boolean)]
    for (const call of calls) {
      if (!call || call.human === true) continue
      if (call.object && !objectNames.has(call.object)) {
        issues.push({ id: `node:${node.id}:object`, nodeId: node.id, level: 'error',
          message: `槽位引用了未声明的对象「${call.object}」` })
      }
      const names = new Set<string>()
      for (const value of Object.values(call.arguments ?? {})) collectSources(value, names)
      const declared = parameterNames(node)
      for (const name of names) {
        if (!declared.has(name)) {
          issues.push({ id: `node:${node.id}:parameter:${name}`, nodeId: node.id, level: 'error',
            message: `槽位引用了未声明的现场参数「${name}」` })
        }
      }
    }
  }

  if (nodes.length === 0) {
    issues.push({ id: 'doc:empty', level: 'error', message: '流程为空，请至少添加一个排查节点' })
    return issues
  }

  const byId = new Map(nodes.map((n) => [n.id, n]))
  const { incoming, outgoing } = indexEdges(nodes, edges)
  const nameOf = (id: string) => {
    const node = byId.get(id)
    return node ? displayName(node) : id
  }

  // 1. 入口唯一：有 start 时它就是唯一入口，否则沿用「入度为 0 的排查节点」
  const startNode = nodes.find((n) => n.kind === 'start')
  const entries = nodes.filter((n) => (incoming.get(n.id)?.length ?? 0) === 0)
  if (!startNode) {
    if (entries.length === 0) {
      issues.push({
        id: 'doc:entry-missing',
        level: 'error',
        message: '没有入口节点：所有节点都有入线，请检查是否形成环',
      })
    } else if (entries.length > 1) {
      const names = entries.map(displayName).join('、')
      for (const node of entries) {
        issues.push({
          id: `node:${node.id}:entry-multiple`,
          level: 'error',
          message: `存在 ${entries.length} 个入口节点（${names}），应只保留一个`,
          nodeId: node.id,
        })
      }
    } else if (!isInvestigation(entries[0])) {
      issues.push({
        id: `node:${entries[0].id}:entry-kind`,
        level: 'error',
        message: `入口节点「${displayName(entries[0])}」必须是排查节点`,
        nodeId: entries[0].id,
      })
    }
  }

  // 2. 连接必须符合矩阵
  for (const edge of edges) {
    const from = byId.get(edge.from)
    const to = byId.get(edge.to)
    if (!from || !to) {
      issues.push({
        id: `edge:${edge.id}:dangling`,
        level: 'error',
        message: `连线指向不存在的节点（${nameOf(edge.from)} → ${nameOf(edge.to)}）`,
        edgeId: edge.id,
      })
      continue
    }
    if (!canConnect(from.kind, to.kind)) {
      issues.push({
        id: `edge:${edge.id}:rule`,
        level: 'error',
        message: `不允许从「${displayName(from)}」连到「${displayName(to)}」`,
        edgeId: edge.id,
      })
    }
  }

  for (const node of nodes) {
    const outs = outgoing.get(node.id) ?? []

    // 3. 排查的每条出线必须标明分支
    if (isInvestigation(node)) {
      for (const edge of outs) {
        if (!edge.branch) {
          issues.push({
            id: `edge:${edge.id}:branch`,
            level: 'error',
            message: `排查节点「${displayName(node)}」的出线必须标明「是」或「否」`,
            edgeId: edge.id,
          })
        }
      }
    }

    // 4. 出线数量：终止节点无出线，结论恰好一条，其余至少一条
    if (isTerminalKind(node.kind)) {
      if (outs.length > 0) {
        issues.push({
          id: `node:${node.id}:terminal-out`,
          level: 'error',
          message: `终止节点「${displayName(node)}」不能再有出线`,
          nodeId: node.id,
        })
      }
    } else if (isSingleSuccessor(node.kind)) {
      if (outs.length !== 1) {
        const label = KIND_META[node.kind]?.label ?? '节点'
        issues.push({
          id: `node:${node.id}:single-out`,
          level: 'error',
          message:
            outs.length === 0
              ? `${label}「${displayName(node)}」必须有一条出线`
              : `${label}「${displayName(node)}」只能有一条出线，当前有 ${outs.length} 条`,
          nodeId: node.id,
        })
      }
    } else if (outs.length === 0) {
      issues.push({
        id: `node:${node.id}:no-out`,
        level: 'error',
        message: `节点「${displayName(node)}」是死路：至少需要一条出线`,
        nodeId: node.id,
      })
    }

    // 5. 必填描述
    if (!node.title?.trim()) {
      issues.push({
        id: `node:${node.id}:title`,
        level: 'error',
        message: `${KIND_META[node.kind]?.label ?? '节点'}缺少标题`,
        nodeId: node.id,
      })
    }
    if (!node.detail?.trim() && node.kind !== 'start') {
      issues.push({
        id: `node:${node.id}:detail`,
        level: 'error',
        message: `节点「${displayName(node)}」缺少说明`,
        nodeId: node.id,
      })
    }
    if (isInvestigation(node)) {
      const missing = FIELD_LABELS.filter(([key]) => !node.fields?.[key]?.trim()).map(
        ([, label]) => label,
      )
      if (missing.length > 0) {
        issues.push({
          id: `node:${node.id}:fields`,
          level: 'error',
          message: `排查节点「${displayName(node)}」缺少描述：${missing.join('、')}`,
          nodeId: node.id,
        })
      }
    }
  }

  // 6. 路径可达终止节点 + 环检测
  const terminalIds = nodes.filter((n) => isTerminalKind(n.kind)).map((n) => n.id)
  const canFinish = reachableFrom(terminalIds, (id) => {
    const sources: string[] = []
    for (const edge of incoming.get(id) ?? []) sources.push(edge.from)
    return sources
  })

  const cycles = findCycles(nodes, outgoing)
  const inCycle = new Set(cycles.flat())
  for (const component of cycles) {
    const names = component.map(nameOf).join(' → ')
    for (const id of component) {
      issues.push({
        id: `node:${id}:cycle`,
        level: 'warning',
        message: `存在环：${names}，执行时会原地打转`,
        nodeId: id,
      })
    }
  }

  for (const node of nodes) {
    if (inCycle.has(node.id) || canFinish.has(node.id)) continue
    // 已由 no-out 报过的死路不再重复
    if ((outgoing.get(node.id) ?? []).length === 0) continue
    issues.push({
      id: `node:${node.id}:dead-end`,
      level: 'error',
      message: `节点「${displayName(node)}」之后的路径无法到达「问题解决」或「上升」`,
      nodeId: node.id,
    })
  }

  const entry = entries[0]
  if (entry) {
    const reachable = reachableFrom([entry.id], (id) => (outgoing.get(id) ?? []).map((e) => e.to))
    for (const node of nodes) {
      if (reachable.has(node.id)) continue
      issues.push({
        id: `node:${node.id}:unreachable`,
        level: 'warning',
        message: `节点「${displayName(node)}」从入口不可达`,
        nodeId: node.id,
      })
    }
  }

  return issues
}

/**
 * 文档一变就同步重算，和旧项目 bpmn-js-bpmnlint 监听 elements.changed 的语义一致。
 * lintDoc 是纯同步遍历（Tarjan + 连线扫描），几十个节点耗时可忽略，不需要防抖；
 * 防抖反而会让「改完标题、角标要等一下才亮」变成可感知的延迟。
 * doc 为 null（还没加载出来）时给空结果，避免拿空壳文档算出一条「流程为空」。
 */
export function useLint(getDoc: () => FlowDoc | null): Ref<LintIssue[]> {
  const issues = ref<LintIssue[]>([])
  const stop = watch(
    getDoc,
    (doc) => {
      issues.value = doc ? lintDoc(doc) : []
    },
    { deep: true, immediate: true },
  )
  onScopeDispose(() => stop())
  return issues
}
