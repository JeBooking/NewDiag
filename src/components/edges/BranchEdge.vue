<script setup lang="ts">
import { computed } from 'vue'
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type Position } from '@vue-flow/core'
import type { EdgeBranchData } from './index'

defineOptions({ inheritAttrs: false })

const props = defineProps<{
  id: string
  sourceX: number
  sourceY: number
  targetX: number
  targetY: number
  sourcePosition: Position
  targetPosition: Position
  markerEnd?: string
  selected?: boolean
  data?: EdgeBranchData
}>()

const BRANCH_COLOR = { yes: '#16a34a', no: '#dc2626' } as const

const branch = computed(() => props.data?.branch ?? null)
const color = computed(() => (branch.value ? BRANCH_COLOR[branch.value] : '#94a3b8'))
const label = computed(() => (branch.value === 'yes' ? '是' : branch.value === 'no' ? '否' : ''))

/** 连线自身的问题：矩阵不允许、缺分支、悬空，就地标叉，不要求用户先去列表里找 */
const issueLevel = computed(() => props.data?.issueLevel ?? null)
const issueText = computed(() => props.data?.issueMessage ?? '')

const bezier = computed(() =>
  getBezierPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    sourcePosition: props.sourcePosition,
    targetX: props.targetX,
    targetY: props.targetY,
    targetPosition: props.targetPosition,
  }),
)

const path = computed(() => bezier.value[0])

/** 叉号压在一条有「是/否」标签的边上时要让开标签，否则两者重叠成一团 */
const issueStyle = computed(() => {
  const offset = label.value ? 16 : 0
  return {
    transform: `translate(-50%, -50%) translate(${bezier.value[1]}px, ${bezier.value[2] - offset}px)`,
  }
})

/** 选中节点直连的边走虚线动画，方向天然是 from → to */
const edgeStyle = computed(() => {
  const flowing = props.data?.flowing === true
  const trail = props.data?.trail ?? null
  const faded = props.data?.faded === true
  const issue = issueLevel.value
  return {
    stroke: issue ? (issue === 'error' ? '#dc2626' : '#d97706') : color.value,
    strokeWidth: issue ? 2.2 : props.selected || flowing ? 2.4 : trail === 'taken' ? 2.2 : 1.6,
    // 走过的路径保持实线到底，没走的一律压淡：跑完之后「走了哪条」是画布要回答的首要问题
    opacity: faded && !issue ? 0.28 : trail === 'dropped' ? 0.45 : 1,
    ...(trail === 'dropped' ? { strokeDasharray: '5 4' } : {}),
    ...(issue ? { strokeDasharray: '6 4' } : {}),
    ...(flowing && trail !== 'dropped'
      ? { strokeDasharray: '7 5', animation: 'edge-flow 0.7s linear infinite' }
      : {}),
  }
})
</script>

<template>
  <BaseEdge
    :id="id"
    :path="path"
    :marker-end="markerEnd"
    :style="edgeStyle"
  />
  <EdgeLabelRenderer>
    <div
      v-if="label"
      class="edge-label"
      :class="branch"
      :style="{
        transform: `translate(-50%, -50%) translate(${bezier[1]}px, ${bezier[2]}px)`,
        borderColor: color,
        color,
      }"
    >
      {{ label }}
    </div>
    <div
      v-if="issueLevel"
      class="edge-issue"
      :class="issueLevel"
      :style="issueStyle"
      :title="issueText"
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M5 5L11 11M11 5L5 11" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
    </div>
  </EdgeLabelRenderer>
</template>

<style scoped>
.edge-label {
  position: absolute;
  padding: 0 4px;
  border: 1px solid currentcolor;
  border-radius: 3px;
  background: var(--el-bg-color-overlay);
  font-size: 10px;
  line-height: 14px;
  pointer-events: none;
  user-select: none;
}

/* 与节点上的叉号同款：红/橙实心圆 + 白色叉，位置在连线中点，带 issue 文本做悬浮说明 */
.edge-issue {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 2px solid var(--el-bg-color-overlay);
  border-radius: 50%;
  color: #fff;
  pointer-events: none;
  user-select: none;
}

.edge-issue.error {
  background: var(--el-color-danger);
}

.edge-issue.warning {
  background: var(--el-color-warning);
}

.edge-issue svg {
  width: 10px;
  height: 10px;
}
</style>

<style>
/* BaseEdge 的 path 在子组件里，动画关键帧只能放全局 */
@keyframes edge-flow {
  to {
    stroke-dashoffset: -24;
  }
}
</style>
