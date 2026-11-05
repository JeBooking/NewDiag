<script setup lang="ts">
import { computed } from 'vue'
import {
  formatStageDuration,
  stageTarget,
  summarizeArguments,
  type PipelineStage,
} from '@/services/evidence'

const props = defineProps<{ stages: PipelineStage[] }>()

const rows = computed(() =>
  props.stages.map((stage) => ({
    stage,
    target: stageTarget(stage),
    argumentsText: summarizeArguments(stage.arguments),
    durationText: formatStageDuration(stage.durationMs),
  })),
)
</script>

<template>
  <div class="stages">
    <div
      v-for="row in rows"
      :key="row.stage.id"
      class="stage"
      :class="{ 'is-error': row.stage.error }"
    >
      <div class="stage-head">
        <span class="stage-name">{{ row.stage.label }}</span>
        <span class="stage-target" :title="row.target">{{ row.target }}</span>
        <span class="stage-time">{{ row.durationText }}</span>
      </div>
      <div v-if="row.argumentsText" class="stage-args" :title="row.argumentsText">
        {{ row.argumentsText }}
      </div>
      <div v-if="row.stage.error" class="stage-error">{{ row.stage.error }}</div>
    </div>
  </div>
</template>

<style scoped>
.stages {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  overflow: hidden;
}

.stage {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 10px;
  background: var(--el-fill-color-light);
}

.stage + .stage {
  border-top: 1px solid var(--el-border-color-lighter);
}

.stage.is-error {
  background: var(--el-color-danger-light-9);
}

.stage-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 11px;
}

.stage-name {
  flex: none;
  min-width: 32px;
  color: var(--el-text-color-secondary);
}

.stage-target {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-family: var(--el-font-family-mono, monospace);
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--el-text-color-primary);
}

.stage-time {
  flex: none;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.stage-args {
  overflow: hidden;
  font-size: 11px;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--el-text-color-secondary);
}

.stage-error {
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--el-color-danger);
}
</style>
