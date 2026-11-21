<script setup lang="ts">
import { computed } from 'vue'
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type Position } from '@vue-flow/core'
import { ICON_ERROR, ICON_WARNING } from '@/components/canvas/issue-icons'
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

/** 连线自身的全部问题：矩阵不允许、缺分支、悬空，就地标叉，悬停看具体原因 */
const issues = computed(() => props.data?.issues ?? [])
/** 角标取最严重的一条；列表已按错误优先排序 */
const issueLevel = computed(() => issues.value[0]?.level ?? null)

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
    // 选中节点就必须能看出流向：旧代/已回退的边同样要动，颜色和透明度负责区分新旧
    ...(flowing ? { animation: 'edge-flow 0.7s linear infinite' } : {}),
    ...(flowing && trail !== 'dropped' ? { strokeDasharray: '7 5' } : {}),
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
      class="edge-issue-wrap"
      :style="issueStyle"
    >
      <div class="edge-issue" :class="issueLevel">
        <svg v-if="issueLevel === 'error'" viewBox="0 0 512 512" aria-hidden="true">
          <path :d="ICON_ERROR" fill="currentColor" />
        </svg>
        <svg v-else viewBox="0 0 512 512" aria-hidden="true">
          <path :d="ICON_WARNING" fill="currentColor" />
        </svg>
      </div>
      <div class="edge-issue-pop">
        <div class="edge-issue-head">
          校验{{ issueLevel === 'error' ? '错误' : '提示' }} · {{ issues.length }} 条
        </div>
        <div v-for="(item, index) in issues" :key="index" class="edge-issue-row">
          <span class="edge-issue-dot" :class="item.level" />
          <span>{{ item.message }}</span>
        </div>
      </div>
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

/* 角标本身不吃事件，由外层容器接收，否则悬停不到、气泡出不来 */
.edge-issue-wrap {
  position: absolute;
  pointer-events: auto;
}

.edge-issue {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 5px;
  border-radius: 50%;
  color: #fff;
  pointer-events: none;
  user-select: none;
}

/* 与节点的 issue-pop 同一套观感：白底、细边、轻阴影，悬停才展开 */
.edge-issue-pop {
  position: absolute;
  top: 22px;
  left: 50%;
  display: none;
  width: 220px;
  padding: 8px 10px;
  transform: translateX(-50%);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
  box-shadow: 0 6px 16px rgb(0 0 0 / 8%);
  font-size: 12px;
  line-height: 1.5;
}

.edge-issue-wrap:hover .edge-issue-pop {
  display: block;
}

.edge-issue-head {
  margin-bottom: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.edge-issue-row {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  color: var(--el-text-color-primary);
}

.edge-issue-dot {
  flex: none;
  width: 6px;
  height: 6px;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--el-color-warning);
}

.edge-issue-dot.error {
  background: var(--el-color-danger);
}

.edge-issue.error {
  background: #cc0000;
}

.edge-issue.warning {
  background: #f7c71a;
}

.edge-issue svg {
  width: 100%;
  height: 100%;
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
