import type { NodeKind } from './types'
import { KIND_META } from './kinds'

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

/**
 * 分支语义只认 KIND_META.branch 一处：卡片画几个出口、连线记不记 branch 必须同源。
 * 否则新增是/否类型时，卡片长出两个出口、边却按默认分支落库，两条路会被静默合并。
 */
export const needsBranch = (kind: NodeKind) => KIND_META[kind].branch === 'yesno'

/** 结论只允许一个后继；由 KIND_META 声明，不再按类型名写死 */
export const isSingleSuccessor = (kind: NodeKind) => Boolean(KIND_META[kind].singleSuccessor)
