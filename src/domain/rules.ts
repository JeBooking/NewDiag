import type { NodeKind } from './types'

/**
 * 与后端 app/services/rules.py 保持一致的连接矩阵。
 * generic 表示非诊断元素（如开始事件），只用于边界判定。
 */
export const CONNECTION_RULES: Record<string, string[]> = {
  start: ['investigation'],
  generic: ['remediation', 'problemSolved', 'escalation', 'generic'],
  investigation: ['investigation', 'investigationAction', 'conclusion', 'escalation'],
  investigationAction: ['investigation'],
  conclusion: ['remediation', 'escalation'],
  remediation: ['remediation', 'problemSolved', 'escalation'],
  problemSolved: [],
  escalation: [],
}

export function canConnect(from: NodeKind | 'start' | 'generic', to: NodeKind | 'generic') {
  const targets = CONNECTION_RULES[from]
  if (!targets) return false
  return targets.includes(to)
}

/** 结论只允许一个后继；其余节点允许多条 */
export const isSingleSuccessor = (kind: NodeKind) => kind === 'conclusion'

/** 排查需要是/否分组，其余节点单出口 */
export const needsBranch = (kind: NodeKind) => kind === 'investigation'
