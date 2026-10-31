import type { NodeComponent, NodeTypesObject } from '@vue-flow/core'
import type { NodeKind, NodeVisualStatus } from '@/domain/types'
import NodeCard from './NodeCard.vue'

/** 面板拖拽到画布时使用的 dataTransfer 类型，palette 与画布共用 */
export const NODE_DRAG_TYPE = 'application/x-newdiag-node-kind'

/** NodeCard 只消费渲染投影，业务字段始终留在 FlowDoc 里 */
export interface NodeCardData {
  kind: NodeKind
  title: string
  detail: string
  hasBinding: boolean
  /** 入口节点不画左侧 target handle */
  hasTarget: boolean
  issueLevel: 'error' | 'warning' | null
  issueMessage: string
  status: NodeVisualStatus | null
  selected: boolean
}

/** 单一卡片渲染器承担 6 种类型；将来换成几何形状只需替换此注册项 */
export const nodeTypes: NodeTypesObject = { diag: NodeCard as unknown as NodeComponent }

export { NodeCard }
