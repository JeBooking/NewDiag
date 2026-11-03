<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppHeader from '@/components/layout/AppHeader.vue'
import type { InstanceStatus, InstanceSummary } from '@/domain/types'
import { useFlowsStore } from '@/stores/flows'
import { useInstancesStore } from '@/stores/instances'

const router = useRouter()
const flowsStore = useFlowsStore()
const store = useInstancesStore()

const flowFilter = ref('')
const statusFilter = ref<InstanceStatus | ''>('')

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

const statusOptions = Object.entries(STATUS_TEXT) as Array<[InstanceStatus, string]>

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

async function reload(): Promise<void> {
  await store.loadList({
    flowId: flowFilter.value || undefined,
    status: statusFilter.value || undefined,
  })
}

onMounted(async () => {
  void flowsStore.load()
  await reload()
})

watch([flowFilter, statusFilter], () => {
  void reload()
})

function openInstance(row: InstanceSummary): void {
  router.push({ name: 'instance', params: { id: row.id } })
}

function resetFilter(): void {
  flowFilter.value = ''
  statusFilter.value = ''
}
</script>

<template>
  <div class="page">
    <AppHeader />
    <main class="content">
      <div class="page-head">
        <div>
          <h1>执行记录</h1>
          <p>每次诊断的路径、运行记录与人工操作</p>
        </div>
        <div class="head-actions">
          <el-button :loading="store.listLoading" @click="reload">刷新</el-button>
        </div>
      </div>

      <div class="filters">
        <el-select v-model="flowFilter" placeholder="全部流程" clearable class="filter">
          <el-option v-for="flow in flowsStore.items" :key="flow.id" :label="flow.name" :value="flow.id" />
        </el-select>
        <el-select v-model="statusFilter" placeholder="全部状态" clearable class="filter">
          <el-option
            v-for="[value, label] in statusOptions"
            :key="value"
            :label="label"
            :value="value"
          />
        </el-select>
        <el-button v-if="flowFilter || statusFilter" text @click="resetFilter">重置</el-button>
      </div>

      <el-alert
        v-if="store.listError"
        :title="store.listError"
        type="error"
        :closable="false"
        show-icon
        class="error"
      />

      <el-table
        v-loading="store.listLoading"
        :data="store.list"
        row-key="id"
        class="table"
        @row-click="openInstance"
      >
        <el-table-column label="流程" min-width="200">
          <template #default="{ row }">
            {{ row.flowName || row.flowId }}
          </template>
        </el-table-column>
        <el-table-column label="版本" width="90">
          <template #default="{ row }">
            <span v-if="row.releaseNo">v{{ row.releaseNo }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="STATUS_TYPE[row.status as InstanceStatus]" effect="plain">
              {{ STATUS_TEXT[row.status as InstanceStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="开始时间" width="190">
          <template #default="{ row }">{{ formatTime(row.startedAt) }}</template>
        </el-table-column>
        <el-table-column label="结束时间" width="190">
          <template #default="{ row }">
            <span v-if="row.endedAt">{{ formatTime(row.endedAt) }}</span>
            <span v-else class="muted">进行中</span>
          </template>
        </el-table-column>
        <el-table-column label="发起人" width="120">
          <template #default="{ row }">{{ row.startedBy }}</template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有执行记录" :image-size="80" />
        </template>
      </el-table>
    </main>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.content {
  flex: 1;
  overflow: auto;
  padding: 20px 24px 32px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.page-head h1 {
  margin: 0 0 4px;
  font-size: 18px;
}

.page-head p {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.filters {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

.filter {
  width: 200px;
}

.error {
  margin-bottom: 12px;
}

.table {
  background: var(--el-bg-color);
  border-radius: 8px;
}

.muted {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
