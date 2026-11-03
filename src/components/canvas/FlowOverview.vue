<script setup lang="ts">
import { computed, ref } from 'vue'
import { getBezierPath, getRectOfNodes, Panel, Position, useVueFlow } from '@vue-flow/core'
import { KIND_META } from '@/domain/kinds'
import type { NodeCardData } from '@/components/nodes'
import type { EdgeBranchData } from '@/components/edges'

const { getNodesInitialized, getEdges, viewport, dimensions, setCenter, zoomTo } = useVueFlow()
const svg = ref<SVGSVGElement | null>(null)
const visibleNodes = computed(() => getNodesInitialized.value.filter((node) => !node.hidden))
const bounds = computed(() => visibleNodes.value.length ? getRectOfNodes(visibleNodes.value) : null)
const cards = computed(() => visibleNodes.value.map((node) => {
  const data = node.data as NodeCardData
  const title = Array.from(data.title || '未命名节点')
  const meta = KIND_META[data.kind]
  return {
    id: node.id, ...node.computedPosition, ...node.dimensions,
    color: meta.color, kind: meta.label, branch: meta.branch,
    title: title.length > 8 ? title.slice(0, 7).join('') + '…' : title.join(''),
    faded: data.trailState === 'unvisited', selected: data.selected || node.selected,
  }
}))

const links = computed(() => getEdges.value.filter((edge) =>
  !edge.hidden && !edge.sourceNode.hidden && !edge.targetNode.hidden
  && [edge.sourceX, edge.sourceY, edge.targetX, edge.targetY].every(Number.isFinite),
).map((edge) => {
  const data = edge.data as EdgeBranchData | undefined
  const [path] = getBezierPath({
    sourceX: edge.sourceX, sourceY: edge.sourceY, sourcePosition: Position.Right,
    targetX: edge.targetX, targetY: edge.targetY, targetPosition: Position.Left,
  })
  return {
    id: edge.id, path, faded: data?.faded,
    color: data?.branch === 'yes' ? '#16a34a' : data?.branch === 'no' ? '#dc2626' : '#8292a6',
  }
}))

const view = computed(() => ({
  x: -viewport.value.x / viewport.value.zoom,
  y: -viewport.value.y / viewport.value.zoom,
  width: dimensions.value.width / viewport.value.zoom,
  height: dimensions.value.height / viewport.value.zoom,
}))

const viewBox = computed(() => {
  const a = bounds.value ?? view.value
  const b = view.value
  const left = Math.min(a.x, b.x)
  const top = Math.min(a.y, b.y)
  const width = Math.max(a.x + a.width, b.x + b.width) - left
  const height = Math.max(a.y + a.height, b.y + b.height) - top
  const pad = Math.max(width, height, 1) * 0.035
  return [left - pad, top - pad, width + pad * 2, height + pad * 2].join(' ')
})

function center(x: number, y: number) {
  void setCenter(x, y, { zoom: viewport.value.zoom })
}

// 固定按下时的映射，拖出原有图面边界时也不会因缩略比例变化而跳动。
let drag: { id: number; inverse: DOMMatrix; dx: number; dy: number } | null = null

function pointerDown(event: PointerEvent) {
  if (!event.isPrimary || event.button !== 0) return
  const matrix = svg.value?.getScreenCTM()
  if (!matrix || !svg.value) return
  const inverse = matrix.inverse()
  const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(inverse)
  const v = view.value
  const inside = p.x >= v.x && p.x <= v.x + v.width && p.y >= v.y && p.y <= v.y + v.height
  drag = {
    id: event.pointerId, inverse,
    dx: inside ? v.x + v.width / 2 - p.x : 0,
    dy: inside ? v.y + v.height / 2 - p.y : 0,
  }
  svg.value.setPointerCapture(event.pointerId)
  if (!inside) center(p.x, p.y)
}

function pointerMove(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.id) return
  const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(drag.inverse)
  center(p.x + drag.dx, p.y + drag.dy)
}

function endDrag(event: PointerEvent) {
  if (drag?.id === event.pointerId) drag = null
}

function wheelZoom(event: WheelEvent) {
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 120 : 1
  void zoomTo(viewport.value.zoom * Math.exp(-event.deltaY * unit * 0.002))
}

function navigate(event: KeyboardEvent) {
  const v = view.value
  const cx = v.x + v.width / 2
  const cy = v.y + v.height / 2
  switch (event.key) {
    case 'ArrowLeft': center(cx - v.width / 4, cy); break
    case 'ArrowRight': center(cx + v.width / 4, cy); break
    case 'ArrowUp': center(cx, cy - v.height / 4); break
    case 'ArrowDown': center(cx, cy + v.height / 4); break
    case '+': case '=': void zoomTo(viewport.value.zoom * 1.2); break
    case '-': void zoomTo(viewport.value.zoom / 1.2); break
    default: return
  }
  event.preventDefault()
  event.stopPropagation()
}
</script>

<template>
  <Panel position="bottom-right" class="overview-panel">
    <svg
      ref="svg"
      class="overview nopan nodrag nowheel"
      tabindex="0"
      role="group"
      aria-label="流程小地图。方向键移动视野，加减键缩放。"
      :viewBox="viewBox"
      @pointerdown.stop="pointerDown"
      @pointermove.stop="pointerMove"
      @pointerup="endDrag"
      @pointercancel="endDrag"
      @lostpointercapture="endDrag"
      @wheel.stop.prevent="wheelZoom"
      @keydown="navigate"
    >
      <g aria-hidden="true" pointer-events="none">
        <path
          v-for="link in links" :key="link.id" :d="link.path" fill="none" :stroke="link.color"
          :opacity="link.faded ? 0.45 : 1" stroke-width="1.2" vector-effect="non-scaling-stroke"
        />
        <g
          v-for="card in cards" :key="card.id" :transform="`translate(${card.x} ${card.y})`"
          :opacity="card.faded ? 0.55 : 1"
        >
          <rect
            :width="card.width" :height="card.height" rx="8" class="card"
            :stroke="card.selected ? 'var(--el-color-primary)' : 'var(--el-border-color-dark)'"
            vector-effect="non-scaling-stroke"
          />
          <rect x="2" y="3" width="7" :height="Math.max(0, card.height - 6)" rx="3" :fill="card.color" />
          <text x="16" :y="card.height * 0.34" :fill="card.color" font-size="17">{{ card.kind }}</text>
          <text x="16" :y="card.height * 0.72" class="card-title">{{ card.title }}</text>
          <template v-if="card.branch === 'yesno'">
            <circle :cx="card.width" :cy="card.height * 0.34" r="5" fill="#16a34a" />
            <circle :cx="card.width" :cy="card.height * 0.68" r="5" fill="#dc2626" />
          </template>
        </g>
        <rect
          v-bind="view" fill="var(--el-color-primary)" fill-opacity="0.045"
          stroke="var(--el-color-primary)" stroke-width="1.5" vector-effect="non-scaling-stroke"
        />
      </g>
    </svg>
  </Panel>
</template>

<style scoped>
.overview {
  display: block;
  width: clamp(140px, 18vw, 220px);
  height: auto;
  aspect-ratio: 8 / 5;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
  cursor: grab;
  touch-action: none;
  user-select: none;
}
.overview:active { cursor: grabbing; }
.overview:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.card { fill: var(--el-bg-color-overlay); }
.card-title { fill: var(--el-text-color-primary); font-size: 23px; font-weight: 600; }
</style>
