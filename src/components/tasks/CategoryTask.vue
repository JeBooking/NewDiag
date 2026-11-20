<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ReviewTask } from '@/domain/types'
import { UNNAMED_INVESTIGATION, categoryOptions, manualRequest } from '@/services/instances'

const props = defineProps<{ review: ReviewTask; busy?: boolean; error?: string }>()
const emit = defineEmits<{ (e: 'choose', target: string): void }>()

const choice = ref('')
watch(() => props.review.id, () => { choice.value = '' })

const request = computed(() => manualRequest(props.review))
const options = computed(() =>
  categoryOptions(props.review).map((item) => ({
    node: item.node,
    title: item.title?.trim() || UNNAMED_INVESTIGATION,
  })),
)

function confirm(): void {
  if (!choice.value) return
  emit('choose', choice.value)
}
</script>

<template>
  <div class="task">
    <div class="task-title">{{ request.title || '选择排查方向' }}</div>
    <p v-if="request.detail" class="task-detail">{{ request.detail }}</p>

    <div class="options-label">选择一条排查方向（必选）</div>
    <el-radio-group v-model="choice" class="options" :disabled="busy">
      <el-radio
        v-for="(option, index) in options"
        :key="option.node"
        :value="option.node"
        class="option"
      >
        <span class="option-index">{{ index + 1 }}</span>
        <span class="option-title">{{ option.title }}</span>
      </el-radio>
    </el-radio-group>

    <p v-if="error" role="alert" class="task-tip">{{ error }}</p>

    <div class="actions">
      <el-button type="primary" :loading="busy" :disabled="!choice" @click="confirm">
        确认分流
      </el-button>
    </div>
    <div class="hint">
      选中后按该排查节点继续推进；选项顺序来自分类节点的出线顺序
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

.options-label {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.options {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: stretch;
}

/* 单选行做成整行可点的卡片，长标题也能换行 */
.options :deep(.el-radio) {
  display: flex;
  align-items: flex-start;
  width: 100%;
  height: auto;
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  line-height: 1.6;
}

.options :deep(.el-radio.is-checked) {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.options :deep(.el-radio__label) {
  display: flex;
  gap: 6px;
  padding-left: 6px;
  white-space: normal;
}

.option-index {
  flex: none;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  line-height: 20px;
}

.option-title {
  font-size: 12px;
}

.task-tip {
  margin: 0;
  font-size: 12px;
  color: var(--el-color-danger);
  line-height: 1.7;
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
