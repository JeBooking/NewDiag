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
</script>

<template>
  <BaseEdge
    :id="id"
    :path="path"
    :marker-end="markerEnd"
    :style="{ stroke: color, strokeWidth: selected ? 2.4 : 1.6 }"
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
</style>
