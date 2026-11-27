import type { NodeComponent, NodeTypesObject } from '@vue-flow/core'
import type { NodeKind, NodeTrailState, NodeVisualStatus } from '@/domain/types'
import NodeCard from './NodeCard.vue'

/** 面板拖拽到画布时使用的 dataTransfer 类型，palette 与画布共用 */
export const NODE_DRAG_TYPE = 'application/x-olive-node-kind'

/** 单个校验问题，画布上只保留展示所需的字段 */
import type { InjectionKey } from 'vue'

export interface NodeIssue {
  level: 'error' | 'warning'
  message: string
}

/** 节点上可点的一跳邻居：色点取节点类型色，branch 只在排查节点出来的边上有 */
export interface NavLink {
  id: string
  title: string
  color: string
  branch: string | null
}

/** 画布把「居中到某节点并选中」下沉给节点卡片，避免卡片自己算坐标 */
export const FOCUS_NODE_KEY: InjectionKey<(nodeId: string) => void> = Symbol('olive:focus-node')

/** 类型转换只能落到 FlowDoc 上，卡片自身改不了；由画布提供实现 */
export const CONVERT_NODE_KEY: InjectionKey<(nodeId: string, kind: NodeKind) => void> =
  Symbol('olive:convert-node')

/** NodeCard 只消费渲染投影，业务字段始终留在 FlowDoc 里 */
export interface NodeCardData {
  kind: NodeKind
  title: string
  detail: string
  /** 判定由方法给出（自动）而不是等人（人工）；只用于节点上的自动／人工标记 */
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
  /** 一跳邻居，供节点左上角的导航浮层使用；不传则不出导航入口 */
  links?: { upstream: NavLink[]; downstream: NavLink[] }
  /** 可改类型：编辑态且不是流程入口（开始节点转走会让流程失去唯一入口） */
  convertible: boolean
}

/** 单一卡片渲染器承担 6 种类型；将来换成几何形状只需替换此注册项 */
export const nodeTypes: NodeTypesObject = { diag: NodeCard as unknown as NodeComponent }

export { NodeCard }
