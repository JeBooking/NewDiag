<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import AppHeader from '@/components/layout/AppHeader.vue'
import type { FlowDoc, FlowMode, FlowSummary, InstanceStatus } from '@/domain/types'
import { errorText } from '@/services/api'
import { importFlow } from '@/services/flows'
import { useFlowsStore } from '@/stores/flows'

const router = useRouter()
const store = useFlowsStore()

const createVisible = ref(false)
const submitting = ref(false)
const importVisible = ref(false)
const importText = ref('')
const importing = ref(false)
const form = ref<{ name: string; description: string; mode: FlowMode }>({
  name: '',
  description: '',
  mode: 'parallel',
})

const STATUS_TEXT: Record<InstanceStatus, string> = {
  running: '运行中',
  waiting: '等待处理',
  paused: '已暂停',
  success: '成功',
  failed: '失败',
  escalated: '已上升',
}

const STATUS_TYPE: Record<InstanceStatus, 'primary' | 'warning' | 'info' | 'success' | 'danger'> = {
  running: 'primary',
  waiting: 'warning',
  paused: 'info',
  success: 'success',
  failed: 'danger',
  escalated: 'warning',
}

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

onMounted(() => {
  void store.load(true)
})

function openCreate(): void {
  form.value = { name: '', description: '', mode: 'parallel' }
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  const name = form.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写流程名称')
    return
  }
  submitting.value = true
  try {
    const created = await store.create({
      name,
      description: form.value.description.trim() || undefined,
      mode: form.value.mode,
    })
    createVisible.value = false
    ElMessage.success('流程已创建')
    router.push({ name: 'flow-edit', params: { id: created.id } })
  } catch (error) {
    ElMessage.error(errorText(error, '新建流程失败'))
  } finally {
    submitting.value = false
  }
}

function openImport(): void {
  importText.value = ''
  importVisible.value = true
}

function onImportFileChange(file: UploadFile): void {
  const raw = file.raw
  if (!raw) return
  const reader = new FileReader()
  reader.onload = () => {
    importText.value = String(reader.result ?? '')
  }
  reader.readAsText(raw)
}

async function submitImport(): Promise<void> {
  let doc: unknown
  try {
    doc = JSON.parse(importText.value)
  } catch {
    ElMessage.error('JSON 解析失败，请检查内容')
    return
  }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
    ElMessage.error('流程文档必须是 JSON 对象')
    return
  }
  importing.value = true
  try {
    const created = await importFlow(doc as FlowDoc)
    importVisible.value = false
    ElMessage.success('流程已导入')
    await store.load(true)
    router.push({ name: 'flow-edit', params: { id: created.id } })
  } catch (error) {
    ElMessage.error(errorText(error, '导入流程失败'))
  } finally {
    importing.value = false
  }
}

function openEdit(row: FlowSummary): void {
  router.push({ name: 'flow-edit', params: { id: row.id } })
}

function openReleases(row: FlowSummary): void {
  router.push({ name: 'flow-releases', params: { id: row.id } })
}

function openRun(row: FlowSummary): void {
  router.push({ name: 'flow-run', params: { id: row.id } })
}
</script>

<template>
  <div class="page">
    <AppHeader />
    <main class="content">
      <div class="page-head">
        <div>
          <h1>诊断流程</h1>
          <p>按「排查 → 结论 → 处置 → 问题解决」编排，发布后可执行</p>
        </div>
        <div class="head-actions">
          <el-button :loading="store.loading" @click="store.load(true)">刷新</el-button>
          <el-button @click="openImport">导入</el-button>
          <el-button type="primary" @click="openCreate">新建流程</el-button>
        </div>
      </div>

      <el-alert
        v-if="store.error"
        :title="store.error"
        type="error"
        :closable="false"
        show-icon
        class="error"
      />

      <el-table
        v-loading="store.loading"
        :data="store.items"
        row-key="id"
        class="table"
        @row-click="openEdit"
      >
        <el-table-column label="名称" min-width="240">
          <template #default="{ row }">
            <div class="name-cell">
              <span class="name">{{ row.name }}</span>
              <span v-if="row.description" class="desc">{{ row.description }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="模式" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">
              {{ row.mode === 'serial' ? '串行' : '并行' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="版本数" width="90">
          <template #default="{ row }">{{ row.releaseCount }}</template>
        </el-table-column>
        <el-table-column label="最近发布" width="180">
          <template #default="{ row }">
            <span v-if="row.lastPublishedAt">{{ formatTime(row.lastPublishedAt) }}</span>
            <span v-else class="muted">未发布</span>
          </template>
        </el-table-column>
        <el-table-column label="最近更新" width="180">
          <template #default="{ row }">{{ formatTime(row.updatedAt) }}</template>
        </el-table-column>
        <el-table-column label="最近执行" width="120">
          <template #default="{ row }">
            <el-tag
              v-if="row.lastRunStatus"
              size="small"
              effect="plain"
              :type="STATUS_TYPE[row.lastRunStatus as InstanceStatus]"
            >
              {{ STATUS_TEXT[row.lastRunStatus as InstanceStatus] }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="编辑状态" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.editingBy" size="small" type="warning" effect="plain">
              {{ row.editingBy }} 编辑中
            </el-tag>
            <span v-else class="muted">空闲</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" align="right">
          <template #default="{ row }">
            <div class="row-actions" @click.stop>
              <el-button link type="primary" @click="openEdit(row)">设计</el-button>
              <el-button link @click="openReleases(row)">版本</el-button>
              <el-button link @click="openRun(row)">执行</el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有流程，先新建一个" :image-size="80" />
        </template>
      </el-table>
    </main>

    <el-dialog v-model="createVisible" title="新建流程" width="480px">
      <el-form label-width="72px" label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="例如：冷却系统诊断" maxlength="80" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="200" />
        </el-form-item>
        <el-form-item label="执行模式">
          <el-radio-group v-model="form.mode">
            <el-radio-button value="parallel">并行</el-radio-button>
            <el-radio-button value="serial">串行</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="importVisible" title="导入流程" width="560px">
      <el-upload
        drag
        :auto-upload="false"
        :show-file-list="false"
        accept=".json,application/json"
        :on-change="onImportFileChange"
        class="import-upload"
      >
        <div class="upload-hint">把 .json 流程文档拖到这里，或点击选择文件</div>
      </el-upload>
      <el-input v-model="importText" type="textarea" :rows="10" placeholder="或粘贴流程文档 JSON" />
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="submitImport">导入</el-button>
      </template>
    </el-dialog>
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

.desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
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

.import-upload {
  margin-bottom: 12px;
}

.upload-hint {
  padding: 8px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
