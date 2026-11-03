<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ReviewTask } from '@/domain/types'
import AttachmentList from '@/components/panels/AttachmentList.vue'
import DataPresentation from '@/components/panels/DataPresentation.vue'

const props = defineProps<{ review: ReviewTask; busy?: boolean; error?: string }>()
const emit = defineEmits<{ (e: 'decide', result: 'yes' | 'no' | 'done' | 'continue', reason?: string): void }>()
const reason = ref('')
watch(() => props.review.id, () => { reason.value = '' })
const missingReason = computed(() => props.review.request.type === 'judgment' && !reason.value.trim())

/** 串行模式跑到问题解决节点后的收敛待办：结束还是继续剩下的队列 */
const gate = computed(() => props.review.request.type === 'after_success')

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
    <div class="task-title">{{ review.request.title || (gate ? '串行推进决策' : '人工判断') }}</div>
    <p v-if="review.request.detail" class="task-detail">{{ review.request.detail }}</p>
    <p v-if="review.request.instructions" class="task-tip">{{ review.request.instructions }}</p>
    <DataPresentation :value="review.request.presentation" />

    <div v-if="review.request.evidence" class="evidence">
      <div class="evidence-label">依据</div>
      <pre>{{ pretty(review.request.evidence) }}</pre>
    </div>

    <AttachmentList
      v-if="review.request.attachments?.length"
      :attachments="review.request.attachments"
      dense
    />
    <template v-if="review.request.type === 'judgment'">
      <label for="judgment-reason">判断依据（必填）</label>
      <el-input
        id="judgment-reason" v-model="reason" type="textarea" :rows="3" :disabled="busy"
      />
    </template>
    <p v-if="error" role="alert" class="task-tip">{{ error }}</p>

    <div v-if="gate" class="actions">
      <el-button type="success" :loading="busy" @click="emit('decide', 'done')">结束排查</el-button>
      <el-button type="primary" plain :loading="busy" @click="emit('decide', 'continue')">
        继续排查
      </el-button>
    </div>
    <div v-else class="actions">
      <el-button
        type="success" :loading="busy" :disabled="missingReason"
        @click="emit('decide', 'yes', reason)"
      >是</el-button>
      <el-button
        type="danger" plain :loading="busy" :disabled="missingReason"
        @click="emit('decide', 'no', reason)"
      >否</el-button>
    </div>
    <div v-if="gate" class="hint">结束则本次实例记为成功；继续则按剩余队列推进</div>
    <div v-else class="hint">选择后按对应分支继续推进，判断结果会记录在实例历史中</div>
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
