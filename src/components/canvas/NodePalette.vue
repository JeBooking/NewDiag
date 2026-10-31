<script setup lang="ts">
import { KIND_META, V1_KINDS } from '@/domain/kinds'
import type { NodeKind } from '@/domain/types'
import { NODE_DRAG_TYPE } from '@/components/nodes'

const props = withDefaults(defineProps<{ disabled?: boolean }>(), { disabled: false })

const emit = defineEmits<{
  (event: 'add', kind: NodeKind): void
}>()

const outletHint = (kind: NodeKind) => {
  const branch = KIND_META[kind].branch
  if (branch === 'yesno') return '是 / 否'
  if (branch === 'single') return '单出口'
  return '终止'
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
    <button
      v-for="kind in V1_KINDS"
      :key="kind"
      type="button"
      class="palette-item"
      :disabled="disabled"
      :draggable="!disabled"
      @click="emit('add', kind)"
      @dragstart="onDragStart($event, kind)"
    >
      <span class="swatch" :style="{ background: KIND_META[kind].color }" />
      <span class="label">{{ KIND_META[kind].label }}</span>
      <span class="hint">{{ outletHint(kind) }}</span>
    </button>
  </div>
</template>

<style scoped>
.palette {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 168px;
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

.label {
  flex: 1;
}

.hint {
  font-size: 10px;
  color: var(--el-text-color-placeholder);
}
</style>
