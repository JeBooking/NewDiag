import type { NodeComponent, NodeTypesObject } from '@vue-flow/core'
import type { NodeKind, NodeTrailState, NodeVisualStatus } from '@/domain/types'
import NodeCard from './NodeCard.vue'

/** 面板拖拽到画布时使用的 dataTransfer 类型，palette 与画布共用 */
export const NODE_DRAG_TYPE = 'application/x-newdiag-node-kind'

/** 单个校验问题，画布上只保留展示所需的字段 */
export interface NodeIssue {
  level: 'error' | 'warning'
  message: string
}

/** NodeCard 只消费渲染投影，业务字段始终留在 FlowDoc 里 */
export interface NodeCardData {
  kind: NodeKind
  title: string
  detail: string
  hasBinding: boolean
  /** 入口节点不画左侧 target handle */
  hasTarget: boolean
  /** 仅 start：已声明的启动参数数量，用于副标题；不传按 0 处理 */
  paramCount?: number
  /** 该节点名下的全部校验问题，按错误优先排序 */
  issues: NodeIssue[]
  /** 选中节点的直接前驱 / 后继，用不同颜色圈出来 */
  flow?: 'upstream' | 'downstream' | null
  status: NodeVisualStatus | null
  selected: boolean
  /** 诊断路径位置：当前路径正常，走过但作废的虚线降级，未到访的最暗且不显示状态 */
  trailState: NodeTrailState
  /** 该节点名下的运行记录条数；只有执行页会传，设计器不传即不渲染入口 */
  runCount?: number
  /** 该节点名下的人工待办数；人工节点不产生 run，执行详情只能靠待办记录 */
  reviewCount?: number
}

/** 单一卡片渲染器承担 6 种类型；将来换成几何形状只需替换此注册项 */
export const nodeTypes: NodeTypesObject = { diag: NodeCard as unknown as NodeComponent }

export { NodeCard }
