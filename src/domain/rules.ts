import type { NodeKind } from './types'
import { KIND_META } from './kinds'

/**
 * 与后端 app/services/rules.py 保持一致的连接矩阵。
 * 开始节点是唯一入口，只出不进；出边只到排查节点。
 */
export const CONNECTION_RULES: Record<NodeKind, NodeKind[]> = {
  start: ['investigation', 'dataPreparation'],
  dataPreparation: ['dataPreparation', 'investigation'],
  investigation: ['dataPreparation', 'investigation', 'investigationAction', 'conclusion', 'escalation', 'jump'],
  investigationAction: ['investigation', 'dataPreparation'],
  conclusion: ['remediation', 'escalation', 'jump'],
  remediation: ['remediation', 'problemSolved', 'escalation', 'jump'],
  problemSolved: [],
  escalation: [],
  deepDiagnosis: ['investigation'],
  // 跳转是转交到另一条诊断链路的枢纽：除开始节点（唯一入口、只出不进）外都可作为后继。
  // 仍只允许一条出线，放开的是「能连什么类型」，不是「能连几条」。
  jump: [
    'dataPreparation',
    'investigation',
    'investigationAction',
    'conclusion',
    'remediation',
    'problemSolved',
    'escalation',
    'deepDiagnosis',
    'jump',
  ],
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
