<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppHeader from '@/components/layout/AppHeader.vue'
import type { FolderNode } from '@/domain/types'
import { errorText } from '@/services/api'
import { listFolders } from '@/services/folders'
import { listTrashFlows, purgeFlow, restoreFlow, type TrashFlowSummary } from '@/services/flows'

const rows = ref<TrashFlowSummary[]>([])
const loading = ref(false)
const loadError = ref<string | null>(null)
const folders = ref<FolderNode[]>([])

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

/** 目录树拍平成「父 / 子」，和流程列表的显示口径一致 */
const folderNames = computed(() => {
  const map = new Map<string, string>()
  const walk = (list: FolderNode[], prefix: string): void => {
    for (const node of list) {
      const path = prefix ? `${prefix} / ${node.name}` : node.name
      map.set(node.id, path)
      walk(node.children, path)
    }
  }
  walk(folders.value, '')
  return map
})

/** 目录可能在流程进回收站之后被删掉，查不到就当没有来源目录 */
function folderNameOf(row: TrashFlowSummary): string | null {
  if (!row.folderId) return null
  return folderNames.value.get(row.folderId) ?? null
}

async function loadFolders(): Promise<void> {
  try {
    folders.value = await listFolders()
  } catch {
    folders.value = []
  }
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    rows.value = await listTrashFlows()
  } catch (error) {
    loadError.value = errorText(error, '加载回收站失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
  void loadFolders()
})

async function restore(row: TrashFlowSummary): Promise<void> {
  try {
    await restoreFlow(row.id)
    rows.value = rows.value.filter((item) => item.id !== row.id)
    ElMessage.success(`「${row.name}」已恢复`)
  } catch (error) {
    ElMessage.error(errorText(error, '恢复失败'))
  }
}

async function purge(row: TrashFlowSummary): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `彻底删除流程「${row.name}」？此操作不可恢复，历史执行记录仍会保留。`,
      '彻底删除流程',
      {
        type: 'warning',
        confirmButtonText: '彻底删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }
  try {
    await purgeFlow(row.id)
    rows.value = rows.value.filter((item) => item.id !== row.id)
    ElMessage.success('流程已彻底删除')
  } catch (error) {
    ElMessage.error(errorText(error, '彻底删除失败'))
  }
}
</script>

<template>
  <div class="page">
    <AppHeader />
    <main class="content">
      <div class="page-head">
        <div>
          <h1>回收站</h1>
          <p>删除的流程会先放到这里，恢复后回到原目录；历史执行记录始终保留</p>
        </div>
        <div class="head-actions">
          <el-button :loading="loading" @click="load">刷新</el-button>
        </div>
      </div>

      <el-alert
        v-if="loadError"
        :title="loadError"
        type="error"
        :closable="false"
        show-icon
        class="error"
      />

      <el-table v-loading="loading" :data="rows" row-key="id" class="table">
        <el-table-column label="流程名称" min-width="240">
          <template #default="{ row }">
            <div class="name-cell">
              <span class="name">{{ row.name }}</span>
              <span v-if="row.description" class="desc">{{ row.description }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="来源目录" width="200">
          <template #default="{ row }">
            <span v-if="folderNameOf(row)" class="folder">{{ folderNameOf(row) }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="删除时间" width="190">
          <template #default="{ row }">{{ formatTime(row.deletedAt) }}</template>
        </el-table-column>
        <el-table-column label="版本数" width="90">
          <template #default="{ row }">{{ row.releaseCount }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" @click="restore(row)">恢复</el-button>
              <el-button link type="danger" @click="purge(row)">彻底删除</el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="回收站是空的" :image-size="80" />
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
  margin-bottom: 16px;
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

.head-actions {
  display: flex;
  gap: 8px;
}

.error {
  margin-bottom: 12px;
}

.table {
  background: var(--el-bg-color);
  border-radius: 8px;
}

.name-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.name {
  font-weight: 500;
}

.folder {
  flex: none;
  color: var(--el-color-primary);
}

.desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.muted {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}

.row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
}
</style>
