import type { NodeKind } from './types'

/**
 * 与后端 app/services/rules.py 保持一致的连接矩阵。
 * 开始节点是唯一入口，只出不进；出边只到排查节点。
 */
export const CONNECTION_RULES: Record<NodeKind, NodeKind[]> = {
  start: ['investigation'],
  investigation: ['investigation', 'investigationAction', 'conclusion', 'escalation'],
  investigationAction: ['investigation'],
  conclusion: ['remediation', 'escalation'],
  remediation: ['remediation', 'problemSolved', 'escalation'],
  problemSolved: [],
  escalation: [],
  deepDiagnosis: ['investigation'],
  jump: ['investigation'],
}

export function canConnect(from: NodeKind, to: NodeKind): boolean {
  const targets = CONNECTION_RULES[from]
  if (!targets) return false
  return targets.includes(to)
}

/** 结论只允许一个后继；其余节点允许多条 */
export const isSingleSuccessor = (kind: NodeKind) => kind === 'conclusion'

/** 排查需要是/否分组，其余节点单出口 */
export const needsBranch = (kind: NodeKind) => kind === 'investigation'
