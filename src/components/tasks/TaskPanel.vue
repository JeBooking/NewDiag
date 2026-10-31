<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { FlowDoc, InstanceDetail, ReviewTask } from '@/domain/types'
import { errorText } from '@/services/api'
import { useInstancesStore } from '@/stores/instances'
import ActionTask from './ActionTask.vue'
import DecisionTask from './DecisionTask.vue'
import RollbackDialog from './RollbackDialog.vue'

const props = defineProps<{
  reviews: ReviewTask[]
  detail: InstanceDetail | null
  doc: FlowDoc | null
}>()
const emit = defineEmits<{
  (e: 'select', nodeId: string | null): void
  (e: 'changed'): void
}>()

const store = useInstancesStore()

const selectedId = ref<string | null>(null)
const busy = ref(false)
const rollbackVisible = ref(false)

const selected = computed(() => props.reviews.find((item) => item.id === selectedId.value) ?? null)

const selectedTitle = computed(() => {
  if (!selected.value) return ''
  const node = props.doc?.nodes.find((item) => item.id === selected.value?.nodeId)
  return node?.title || node?.id || selected.value.nodeId
})

watch(
  () => props.reviews,
  (reviews) => {
    if (selectedId.value && !reviews.some((item) => item.id === selectedId.value)) {
      selectedId.value = null
    }
    if (!selectedId.value && reviews.length > 0) {
      select(reviews[0])
    }
  },
)

// 首屏在挂载后选中，避免在父组件渲染期间回写选中节点
onMounted(() => {
  if (!selectedId.value && props.reviews.length > 0) select(props.reviews[0])
})

function select(review: ReviewTask): void {
  selectedId.value = review.id
  emit('select', review.nodeId)
}

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

function kindLabel(review: ReviewTask): string {
  if (review.request.type === 'after_success') return '串行决策'
  return review.kind === 'decision' ? '判断' : '处置'
}

async function decide(result: 'yes' | 'no' | 'done' | 'continue'): Promise<void> {
  if (!selected.value) return
  busy.value = true
  try {
    await store.decide(selected.value.id, { result })
    ElMessage.success(result === 'continue' ? '已继续推进' : '已提交')
    emit('changed')
  } catch (error) {
    ElMessage.error(errorText(error, '提交失败'))
  } finally {
    busy.value = false
  }
}

async function complete(): Promise<void> {
  if (!selected.value) return
  busy.value = true
  try {
    await store.decide(selected.value.id, { result: 'done' })
    ElMessage.success('已标记完成')
    emit('changed')
  } catch (error) {
    ElMessage.error(errorText(error, '提交失败'))
  } finally {
    busy.value = false
  }
}

function onRolledBack(): void {
  selectedId.value = null
  emit('changed')
  emit('select', null)
}
</script>

<template>
  <div class="task-panel">
    <div class="panel-head">
      <span class="panel-title">待办</span>
      <el-tag v-if="reviews.length" size="small" type="warning">{{ reviews.length }}</el-tag>
    </div>

    <div v-if="!reviews.length" class="empty">
      <el-empty description="暂无待办" :image-size="60" />
    </div>

    <template v-else>
      <div class="list">
        <button
          v-for="review in reviews"
          :key="review.id"
          type="button"
          class="review-item"
          :class="{ active: review.id === selectedId }"
          @click="select(review)"
        >
          <div class="review-head">
            <el-tag size="small" :type="review.kind === 'decision' ? 'primary' : 'success'" effect="plain">
              {{ kindLabel(review) }}
            </el-tag>
            <span class="review-title">{{ review.request.title || review.nodeId }}</span>
          </div>
          <div class="review-time">{{ formatTime(review.createdAt) }}</div>
        </button>
      </div>

      <div v-if="selected" class="detail">
        <div class="detail-node">{{ selectedTitle }}</div>
        <DecisionTask
          v-if="selected.kind === 'decision'"
          :review="selected"
          :busy="busy"
          @decide="decide"
        />
        <ActionTask
          v-else
          :review="selected"
          :busy="busy"
          @complete="complete"
          @rollback="rollbackVisible = true"
        />
      </div>
    </template>

    <RollbackDialog
      v-model="rollbackVisible"
      :detail="detail"
      :doc="doc"
      @submitted="onRolledBack"
    />
  </div>
</template>

<style scoped>
.task-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 0 10px;
}

.panel-title {
  font-size: 13px;
  font-weight: 600;
  flex: 1;
}

.empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow: auto;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.review-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-bg-color);
  cursor: pointer;
  text-align: left;
  font: inherit;
}

.review-item:hover {
  border-color: var(--el-color-primary-light-5);
}

.review-item.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.review-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.review-title {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review-time {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.detail {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding-top: 12px;
}

.detail-node {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 8px;
}
</style>
