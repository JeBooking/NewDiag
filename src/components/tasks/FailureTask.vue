<script setup lang="ts">
import type { ReviewTask } from '@/domain/types'

defineProps<{ review: ReviewTask; busy?: boolean }>()
const emit = defineEmits<{ (e: 'stop'): void; (e: 'rollback'): void }>()
</script>

<template>
  <div class="failure">
    <p class="hint">
      节点执行报错，这条思路已经停下。可以退回某个已完成的节点换个方向继续排查，也可以直接结束这次诊断。
    </p>
    <div class="block">
      <div class="label">错误</div>
      <pre class="message">{{ review.request.detail || review.request.title }}</pre>
    </div>
    <div class="actions">
      <el-button :disabled="busy" @click="emit('rollback')">回退到某个节点…</el-button>
      <el-button type="danger" :disabled="busy" @click="emit('stop')">结束流程</el-button>
    </div>
  </div>
</template>

<style scoped>
.failure {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
}

.label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.message {
  margin: 2px 0 0;
  padding: 8px;
  max-height: 260px;
  overflow: auto;
  border-radius: 4px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
