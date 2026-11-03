<script setup lang="ts">
import { KIND_META, V1_KINDS } from '@/domain/kinds'
import type { NodeKind } from '@/domain/types'
import { NODE_DRAG_TYPE } from '@/components/nodes'

const props = withDefaults(defineProps<{ disabled?: boolean }>(), { disabled: false })

const emit = defineEmits<{
  (event: 'add', kind: NodeKind): void
}>()

const descriptions: Partial<Record<NodeKind, string>> = {
  investigation: '判断一个排查条件。可绑定函数自动判断，也可由执行人判断；结果沿“是”或“否”分支继续。',
  investigationAction: '要求执行人先完成一项检查或采集操作，再进入后续排查节点判断结果。',
  conclusion: '记录这条排查路径得出的结论。显示预先填写的结论文本，再沿唯一后继进入处置或上升。',
  remediation: '向执行人说明具体处置步骤。完成后可继续处置、确认问题解决，或转交处理。',
  jump: '由执行人确认转交给另一条诊断链路，确认后沿设计好的唯一后继继续。',
  problemSolved: '标记当前路径已解决问题。如果串行诊断还有未排查的分支，可选择结束或继续。',
  escalation: '标记当前路径需要上级介入处理。这条路径在此结束，不再连接后继节点。',
}

function onDragStart(event: DragEvent, kind: NodeKind) {
  if (props.disabled || !event.dataTransfer) {
    event.preventDefault()
    return
  }
  event.dataTransfer.setData(NODE_DRAG_TYPE, kind)
  event.dataTransfer.setData('text/plain', kind)
  event.dataTransfer.effectAllowed = 'copy'
}
</script>

<template>
  <div class="palette" :class="{ 'is-disabled': disabled }">
    <div class="palette-title">节点</div>
    <el-tooltip
      v-for="kind in V1_KINDS"
      :key="kind"
      :content="descriptions[kind]"
      placement="right"
      :trigger="['hover', 'focus']"
      :show-after="150"
      :popper-style="{ maxWidth: 'min(260px, 80vw)' }"
    >
      <button
        type="button"
        class="palette-item"
        :disabled="disabled"
        :draggable="!disabled"
        @click="emit('add', kind)"
        @dragstart="onDragStart($event, kind)"
      >
        <span class="swatch" aria-hidden="true" :style="{ background: KIND_META[kind].color }" />
        <span>{{ KIND_META[kind].label }}</span>
      </button>
    </el-tooltip>
  </div>
</template>

<style scoped>
.palette {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: max-content;
  padding: 8px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  background: var(--el-bg-color-overlay);
  box-shadow: 0 2px 8px rgb(15 23 42 / 8%);
}

.palette-title {
  margin-bottom: 2px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.palette-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: none;
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: grab;
}

.palette-item:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}

.palette-item:hover {
  border-color: var(--el-border-color);
  background: var(--el-fill-color-light);
}

.palette-item:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.swatch {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

</style>
