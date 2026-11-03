import type { NodeKind } from './types'

export interface KindMeta {
  label: string
  color: string
  terminal?: boolean
  /** 只由人完成，不执行代码 */
  humanOnly?: boolean
  /** 可绑定 Python 函数 */
  codeCapable?: boolean
  /** 出线语义：yes/no 两组，或单出口 */
  branch: 'yesno' | 'single' | 'none'
  /** 只允许一条出线；不填表示可多条 */
  singleSuccessor?: boolean
  /** 唯一入口节点，不出现在节点面板里 */
  entry?: boolean
}

export const KIND_META: Record<NodeKind, KindMeta> = {
  start: {
    label: '开始',
    color: '#0f766e',
    branch: 'single',
    entry: true,
  },
  investigation: {
    label: '排查',
    color: '#2563eb',
    codeCapable: true,
    branch: 'yesno',
  },
  investigationAction: {
    label: '排查动作',
    color: '#0891b2',
    humanOnly: true,
    branch: 'single',
  },
  conclusion: {
    label: '结论',
    color: '#7c3aed',
    branch: 'single',
    singleSuccessor: true,
  },
  remediation: {
    label: '处置措施',
    color: '#059669',
    humanOnly: true,
    branch: 'single',
  },
  problemSolved: {
    label: '问题解决',
    color: '#16a34a',
    terminal: true,
    branch: 'none',
  },
  escalation: {
    label: '上升',
    color: '#d97706',
    terminal: true,
    branch: 'none',
  },
  deepDiagnosis: {
    label: '深度诊断',
    color: '#4f46e5',
    branch: 'single',
  },
  jump: {
    label: '跳转',
    color: '#64748b',
    // 转交给另一条诊断链路：当前是人工确认的文本节点，确认后接任意非 start 节点（仍只一条出线）
    humanOnly: true,
    branch: 'single',
    singleSuccessor: true,
  },
}

export const V1_KINDS: NodeKind[] = [
  'investigation',
  'investigationAction',
  'conclusion',
  'remediation',
  'jump',
  'problemSolved',
  'escalation',
]

export const isTerminalKind = (kind: NodeKind) => Boolean(KIND_META[kind].terminal)
