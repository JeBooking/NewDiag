<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { FlowDoc, ReviewTask } from '@/domain/types'
import { errorText } from '@/services/api'
import { useInstancesStore } from '@/stores/instances'
import ActionTask from './ActionTask.vue'
import DecisionTask from './DecisionTask.vue'
import DataTask from './DataTask.vue'

const props = defineProps<{
  reviews: ReviewTask[]
  doc: FlowDoc | null
  /** 画布上选中的节点：待办列表跟着高亮，形成「节点 ↔ 待办」双向指向 */
  focusNodeId?: string | null
}>()
const emit = defineEmits<{
  (e: 'select', nodeId: string | null): void
  /** 用户点了卡片：父级需要把画布移到这个节点，程序化选中（首屏/联动）不发这个事件 */
  (e: 'focus', nodeId: string): void
  (e: 'changed'): void
}>()

const store = useInstancesStore()

const selectedId = ref<string | null>(null)
const busy = ref(false)
const submitError = ref('')
watch(selectedId, () => { submitError.value = '' })

const selected = computed(() => props.reviews.find((item) => item.id === selectedId.value) ?? null)

function nodeTitle(nodeId: string): string {
  const node = props.doc?.nodes.find((item) => item.id === nodeId)
  return node?.title || nodeId
}

const selectedTitle = computed(() => (selected.value ? nodeTitle(selected.value.nodeId) : ''))

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

// 画布上点了某个节点，列表就跳到它名下的待办；父级已经知道选中，这里不再回抛
watch(
  () => props.focusNodeId,
  (nodeId) => {
    if (!nodeId) return
    const target = props.reviews.find((item) => item.nodeId === nodeId)
    if (target && target.id !== selectedId.value) selectedId.value = target.id
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

/** 卡片被点开：选中 + 请求画布把节点挪到视野中央 */
function onCardClick(review: ReviewTask): void {
  select(review)
  emit('focus', review.nodeId)
}

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

function kindLabel(review: ReviewTask): string {
  if (review.request.type === 'acquisition') return '提供数据'
  if (review.request.type === 'after_success') return '串行决策'
  if (review.kind === 'decision') return '判断'
  // 转交节点的「完成」语义是把问题交给别的链路，不是处置动作，标签要区分
  return review.request.nodeKind === 'jump' ? '转交' : '处置'
}

async function decide(result: 'yes' | 'no' | 'done' | 'continue', reason?: string): Promise<void> {
  if (!selected.value) return
  busy.value = true
  submitError.value = ''
  try {
    await store.decide(selected.value.id, { result, reason })
    ElMessage.success(result === 'continue' ? '已继续推进' : '已提交')
    emit('changed')
  } catch (error) {
    submitError.value = errorText(error, '提交失败')
    ElMessage.error(errorText(error, '提交失败'))
  } finally {
    busy.value = false
  }
}

async function submitData(data: Record<string, unknown>): Promise<void> {
  if (!selected.value) return
  busy.value = true
  submitError.value = ''
  try {
    await store.decide(selected.value.id, { result: 'done', data })
    ElMessage.success('数据已提交')
    emit('changed')
  } catch (error) {
    submitError.value = errorText(error, '数据解析失败，请修正后重新提交')
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
          @click="onCardClick(review)"
        >
          <div class="review-head">
            <el-tag size="small" :type="review.kind === 'decision' ? 'primary' : 'success'" effect="plain">
              {{ kindLabel(review) }}
            </el-tag>
            <span class="review-title">{{ review.request.title || nodeTitle(review.nodeId) }}</span>
          </div>
          <div class="review-meta">
            <span class="review-node">{{ nodeTitle(review.nodeId) }}</span>
            <span>{{ formatTime(review.createdAt) }}</span>
          </div>
        </button>
      </div>

      <div v-if="selected" class="detail">
        <div class="detail-node">{{ selectedTitle }}</div>
        <DataTask
          v-if="selected.request.type === 'acquisition'"
          :review="selected" :busy="busy" :error="submitError" @submit="submitData"
        />
        <DecisionTask
          v-else-if="selected.kind === 'decision'"
          :review="selected"
          :busy="busy"
          :error="submitError"
          @decide="decide"
        />
        <ActionTask
          v-else
          :review="selected"
          :busy="busy"
          @complete="complete"
        />
      </div>
    </template>
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
  flex: none;
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
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 8px 8px 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  cursor: pointer;
  text-align: left;
  font: inherit;
}

/* 未处理的待办统一用警示色左条：列表里一眼能数出还有几件在等人 */
.review-item::before {
  content: '';
  position: absolute;
  top: 6px;
  bottom: 6px;
  left: 0;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--el-color-warning);
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

.review-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

/* 节点名是「这条待办画在哪」的锚点，比创建时间重要，所以给它独立样式而不是拼在时间前面 */
.review-node {
  overflow: hidden;
  padding: 0 4px;
  border-radius: 2px;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  text-overflow: ellipsis;
  white-space: nowrap;
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
