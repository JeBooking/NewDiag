import type { EdgeComponent, EdgeTypesObject } from '@vue-flow/core'
import type { Branch } from '@/domain/types'
import BranchEdge from './BranchEdge.vue'

/** 边的渲染投影；branch 为 null 表示普通单出口连线 */
export interface EdgeBranchData {
  branch: Branch | null
  issueLevel: 'error' | 'warning' | null
  issueMessage: string
  /** 选中节点直连的边：走虚线流动动画，方向始终是 from → to */
  flowing?: boolean
}

export const edgeTypes: EdgeTypesObject = { branch: BranchEdge as unknown as EdgeComponent }

export { BranchEdge }
