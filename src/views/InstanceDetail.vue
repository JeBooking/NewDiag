<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppHeader from '@/components/layout/AppHeader.vue'
import FlowCanvas from '@/components/canvas/FlowCanvas.vue'
import PathList from '@/components/history/PathList.vue'
import ExecutionTrace from '@/components/history/ExecutionTrace.vue'
import RunDetailCard from '@/components/history/RunDetailCard.vue'
import RunTimeline from '@/components/history/RunTimeline.vue'
import TaskPanel from '@/components/tasks/TaskPanel.vue'
import type { InstanceStatus, ReviewTask, RunRecord } from '@/domain/types'
import { errorText } from '@/services/api'
import { useInstancesStore } from '@/stores/instances'

const route = useRoute()
const router = useRouter()
const store = useInstancesStore()

const instanceId = computed(() => String(route.params.id))
const loadError = ref<string | null>(null)
const selectedNodeId = ref<string | null>(null)
const activeTab = ref<'runs' | 'paths' | 'trace'>('runs')
const refreshing = ref(false)
const terminating = ref(false)
/** 卡片里正在看执行详情的节点；同一时刻只允许一个 */
const inspectNodeId = ref<string | null>(null)
/** 只用于把待办卡片的点击传导到画布，画布自身上报的选中不需要回打 */
const canvasRef = ref<InstanceType<typeof FlowCanvas> | null>(null)

const STATUS_TEXT: Record<InstanceStatus, string> = {
  running: '运行中',
  waiting: '等待处理',
  paused: '已暂停',
  success: '成功',
  failed: '失败',
  escalated: '已上升',
  terminated: '已终止',
}

const STATUS_TYPE: Record<InstanceStatus, 'primary' | 'warning' | 'info' | 'success' | 'danger'> = {
  running: 'primary',
  waiting: 'warning',
  paused: 'info',
  success: 'success',
  failed: 'danger',
  escalated: 'warning',
  terminated: 'info',
}

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

const detail = computed(() => store.detail)
const canTerminate = computed(() =>
  detail.value ? ['running', 'waiting', 'paused'].includes(detail.value.status) : false,
)

async function terminate(): Promise<void> {
  if (!canTerminate.value || terminating.value) return
  const targetId = detail.value?.id
  terminating.value = true
  try {
    try {
      await ElMessageBox.confirm(
        '终止后将停止后续诊断并取消未处理的待办，已有记录保留。本次诊断无法继续。',
        '终止诊断',
        { type: 'warning', confirmButtonText: '终止诊断', cancelButtonText: '继续诊断' },
      )
    } catch {
      return
    }
    if (!canTerminate.value || detail.value?.id !== targetId) return
    await store.terminate()
    ElMessage.success('诊断已终止')
  } catch (error) {
    ElMessage.error(errorText(error, '终止诊断失败'))
    await store.refresh(instanceId.value)
  } finally {
    terminating.value = false
  }
}

/** 每个节点名下的运行记录，按开始时间倒序；卡片和节点入口共用这一份 */
const runsByNode = computed<Record<string, RunRecord[]>>(() => {
  const grouped: Record<string, RunRecord[]> = {}
  for (const run of detail.value?.runs ?? []) {
    const list = grouped[run.nodeId] ?? []
    list.push(run)
    grouped[run.nodeId] = list
  }
  for (const list of Object.values(grouped)) {
    list.sort((a, b) => b.startedAt.localeCompare(a.startedAt))
  }
  return grouped
})

const runCounts = computed<Record<string, number>>(() => {
  const counts: Record<string, number> = {}
  for (const [nodeId, list] of Object.entries(runsByNode.value)) counts[nodeId] = list.length
  return counts
})

/** 人工节点不写 run，执行情况只存在待办里；按节点聚合后给入口和卡片用 */
const reviewsByNode = computed<Record<string, ReviewTask[]>>(() => {
  const grouped: Record<string, ReviewTask[]> = {}
  for (const review of detail.value?.reviews ?? []) {
    const list = grouped[review.nodeId] ?? []
    list.push(review)
    grouped[review.nodeId] = list
  }
  for (const list of Object.values(grouped)) {
    list.sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  }
  return grouped
})

/** 节点上只挂未处理的待办数：已处理完的待办仍留在 reviews 里，不能算成「在等我」 */
const pendingCounts = computed<Record<string, number>>(() => {
  const counts: Record<string, number> = {}
  for (const review of detail.value?.reviews ?? []) {
    if (review.status !== 'pending') continue
    counts[review.nodeId] = (counts[review.nodeId] ?? 0) + 1
  }
  return counts
})

/** 进度按「已完成的节点 / 全部节点」算，比 token 数直观，且能一眼看出还剩多少 */
const nodeTotal = computed(() => store.doc?.nodes.length ?? 0)
const nodeDone = computed(() => {
  const done = new Set<string>()
  for (const token of detail.value?.tokens ?? []) {
    if (token.status === 'done') done.add(token.nodeId)
  }
  return done.size
})
const progressPct = computed(() =>
  nodeTotal.value > 0 ? Math.round((nodeDone.value / nodeTotal.value) * 100) : 0,
)

const inspectNode = computed(() => {
  const nodeId = inspectNodeId.value
  if (!nodeId) return null
  return store.doc?.nodes.find((node) => node.id === nodeId) ?? null
})

const inspectRuns = computed(() =>
  inspectNodeId.value ? (runsByNode.value[inspectNodeId.value] ?? []) : [],
)

const inspectReviews = computed(() =>
  inspectNodeId.value ? (reviewsByNode.value[inspectNodeId.value] ?? []) : [],
)

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  try {
    await store.open(instanceId.value)
  } catch (error) {
    loadError.value = errorText(error, '加载实例详情失败')
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  store.close()
})

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') inspectNodeId.value = null
}

async function reload(): Promise<void> {
  refreshing.value = true
  try {
    await store.refresh(instanceId.value)
  } finally {
    refreshing.value = false
  }
}

function onSelect(nodeId: string | null): void {
  selectedNodeId.value = nodeId
  // 点画布空白等于放弃查看，避免浮层一直盖在图上
  if (!nodeId) inspectNodeId.value = null
}

/** 待办卡片上的点击：除了高亮，还要把对应节点挪到视野中央 */
function focusCanvasNode(nodeId: string): void {
  canvasRef.value?.focusNode(nodeId)
}

function onInspect(nodeId: string): void {
  inspectNodeId.value = inspectNodeId.value === nodeId ? null : nodeId
  selectedNodeId.value = nodeId
}
</script>

<template>
  <div class="page">
    <AppHeader />

    <div v-if="detail" class="head">
      <div class="head-left">
        <el-button text @click="router.push({ name: 'instances' })">← 执行记录</el-button>
        <router-link
          class="flow-name"
          :to="{ name: 'flow-releases', params: { id: detail.flowId } }"
        >
          {{ detail.flowName || detail.flowId }}
        </router-link>
        <el-tag v-if="detail.releaseNo" size="small" effect="plain">v{{ detail.releaseNo }}</el-tag>
        <el-tag size="small" :type="STATUS_TYPE[detail.status]">{{ STATUS_TEXT[detail.status] }}</el-tag>
        <div v-if="nodeTotal" class="progress" :title="`已完成 ${nodeDone} 个节点，共 ${nodeTotal} 个`">
          <div class="progress-track">
            <div class="progress-fill" :style="{ width: `${progressPct}%` }" />
          </div>
          <span class="meta">{{ nodeDone }}/{{ nodeTotal }}</span>
        </div>
      </div>
      <div class="head-right">
        <span class="meta">开始 {{ formatTime(detail.startedAt) }}</span>
        <span v-if="detail.endedAt" class="meta">结束 {{ formatTime(detail.endedAt) }}</span>
        <span v-if="detail.snapshot" class="meta">
          快照 {{ formatTime(detail.snapshot.capturedAt) }}
        </span>
        <el-button size="small" :loading="refreshing" @click="reload">刷新</el-button>
        <el-button
          v-if="canTerminate"
          type="danger"
          :loading="terminating"
          @click="terminate"
        >终止诊断</el-button>
      </div>
    </div>

    <el-alert
      v-if="loadError || store.error"
      :title="loadError || store.error || ''"
      type="error"
      :closable="false"
      show-icon
      class="banner"
    />

    <el-alert
      v-if="store.doc && store.docSource === 'flow'"
      title="未取到发布快照，当前显示在线版本"
      description="画布结构可能与本次执行所用的发布版本不一致，请以运行记录为准"
      type="warning"
      :closable="false"
      show-icon
      class="banner"
    />

    <div class="body">
      <div class="canvas-area" v-loading="store.loading">
        <FlowCanvas
          v-if="detail && store.doc"
          ref="canvasRef"
          :doc="store.doc"
          readonly
          :issues="[]"
          :status="store.nodeStatus"
          :selected-node-id="selectedNodeId"
          :run-counts="runCounts"
          :review-counts="pendingCounts"
          :trails="store.walkedEdges"
          :node-trails="store.nodeTrails"
          @select-node="onSelect"
          @inspect-node="onInspect"
          @update:doc="() => undefined"
        />
        <el-empty v-else-if="!store.loading" description="流程快照不可用" :image-size="80" />

        <div v-if="inspectNode" class="run-card-layer">
          <RunDetailCard
            :node="inspectNode"
            :runs="inspectRuns"
            :reviews="inspectReviews"
            @close="inspectNodeId = null"
          />
        </div>
      </div>

      <aside class="aside">
        <TaskPanel
          v-if="detail && detail.status !== 'terminated'"
          :reviews="store.pendingReviews"
          :detail="detail"
          :doc="store.doc"
          :focus-node-id="selectedNodeId"
          @select="onSelect"
          @focus="focusCanvasNode"
          @changed="reload"
        />
        <el-empty v-else-if="detail" description="诊断已终止，待办已取消" :image-size="60" />
      </aside>
    </div>

    <div class="bottom">
      <el-tabs v-model="activeTab" class="tabs">
        <el-tab-pane label="运行记录" name="runs">
          <div class="tab-body">
            <RunTimeline :runs="detail?.runs ?? []" :doc="store.doc" />
          </div>
        </el-tab-pane>
        <el-tab-pane label="诊断路径" name="paths">
          <div class="tab-body">
            <PathList :tokens="detail?.tokens ?? []" :runs="detail?.runs ?? []" :doc="store.doc" />
          </div>
        </el-tab-pane>
        <el-tab-pane label="执行轨迹" name="trace">
          <div class="tab-body">
            <ExecutionTrace :tokens="detail?.tokens ?? []" :runs="detail?.runs ?? []" :doc="store.doc" />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-empty
      v-if="!detail && !store.loading"
      description="实例不存在或已被删除"
      class="missing"
    >
      <el-button @click="router.push({ name: 'instances' })">返回执行记录</el-button>
    </el-empty>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 16px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
  flex: none;
}

.head-left,
.head-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.flow-name {
  font-size: 14px;
  font-weight: 600;
}

.flow-name:hover {
  color: var(--el-color-primary);
}

.meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.progress {
  display: flex;
  align-items: center;
  gap: 6px;
}

.progress-track {
  width: 72px;
  height: 6px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--el-fill-color);
}

.progress-fill {
  height: 100%;
  background: var(--el-color-primary);
  transition: width 0.3s;
}

.banner {
  flex: none;
  border-radius: 0;
}

.body {
  flex: 1;
  display: flex;
  min-height: 0;
}

.canvas-area {
  flex: 1;
  min-width: 0;
  position: relative;
  background: var(--el-bg-color-page);
}

/* 卡片独立一层浮在画布上；容器不吃事件，底下的图还能缩放平移 */
.run-card-layer {
  position: absolute;
  inset: 0;
  z-index: 15;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  pointer-events: none;
}

.run-card-layer > * {
  pointer-events: auto;
}

.aside {
  width: var(--app-panel-width);
  flex: none;
  border-left: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
  padding: 12px 14px;
  overflow: auto;
}

.bottom {
  height: 250px;
  flex: none;
  border-top: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
  padding: 0 16px;
  overflow: hidden;
}

.tabs {
  height: 100%;
}

.tab-body {
  height: 180px;
  overflow: auto;
}

.missing {
  flex: 1;
}
</style>
