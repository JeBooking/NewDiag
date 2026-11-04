<script setup lang="ts">
import type { ReviewTask } from '@/domain/types'
import AttachmentList from '@/components/panels/AttachmentList.vue'

defineProps<{ review: ReviewTask; busy?: boolean }>()
const emit = defineEmits<{
  (e: 'complete'): void
  (e: 'rollback'): void
}>()

function pretty(value: unknown): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
</script>

<template>
  <div class="task">
    <div class="task-title">{{ review.request.title || '人工处置' }}</div>
    <p v-if="review.request.detail" class="task-detail">{{ review.request.detail }}</p>
    <p v-if="review.request.instructions" class="task-tip">{{ review.request.instructions }}</p>

    <div v-if="review.request.evidence" class="evidence">
      <div class="evidence-label">依据</div>
      <pre>{{ pretty(review.request.evidence) }}</pre>
    </div>

    <AttachmentList
      v-if="review.request.attachments?.length"
      :attachments="review.request.attachments"
      dense
    />

    <div class="actions">
      <el-button type="success" :loading="busy" @click="emit('complete')">完成</el-button>
      <el-button type="warning" plain :loading="busy" @click="emit('rollback')">回退</el-button>
    </div>
    <div class="hint">
      对着设备完成处置后点「完成」；没有做成选择「回退」，回到已执行的祖先节点重新排查
    </div>
  </div>
</template>

<style scoped>
.task {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.task-title {
  font-size: 13px;
  font-weight: 600;
}

.task-detail {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-regular);
  line-height: 1.7;
  white-space: pre-wrap;
}

.task-tip {
  margin: 0;
  font-size: 12px;
  color: var(--el-color-warning-dark-2);
  line-height: 1.7;
}

.evidence-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.evidence pre {
  margin: 0;
  max-height: 180px;
  overflow: auto;
  padding: 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.6;
}

.actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

.hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
</style>
