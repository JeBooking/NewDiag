<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { Folder, Search } from '@element-plus/icons-vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import type { FlowDoc, FlowMode, FlowSummary, FolderNode, InstanceStatus } from '@/domain/types'
import { errorText } from '@/services/api'
import { createFolder, listFolders, removeFolder, updateFolder } from '@/services/folders'
import { importFlow, updateFlow, type ListFlowsFilter } from '@/services/flows'
import { useFlowsStore } from '@/stores/flows'

const router = useRouter()
const store = useFlowsStore()

/** 两个固定项用不可能与后端 id 冲突的 key */
const ALL_KEY = '__all__'
const NONE_KEY = '__none__'
const SEARCH_DELAY = 300

interface TreeItem {
  id: string
  name: string
  count?: number
  fixed?: boolean
  children?: TreeItem[]
}

const folders = ref<FolderNode[]>([])
const foldersError = ref<string | null>(null)
const selectedKey = ref(ALL_KEY)
const search = ref('')

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

const moveVisible = ref(false)
const moveTarget = ref<FlowSummary | null>(null)
const moveFolderId = ref('')
const moving = ref(false)

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

function toTreeItem(node: FolderNode): TreeItem {
  return {
    id: node.id,
    name: node.name,
    count: node.flowCount ?? 0,
    children: node.children.map(toTreeItem),
  }
}

/** 目录名索引，用于表格里标出流程所属目录 */
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

/** 未分类数量只能从不过滤目录的列表里数，搜索态下不显示避免误导 */
const unfiledCount = computed(() => {
  if (search.value.trim()) return undefined
  if (selectedKey.value === NONE_KEY) return store.items.length
  if (selectedKey.value === ALL_KEY) return store.items.filter((item) => !item.folderId).length
  return undefined
})

const treeData = computed<TreeItem[]>(() => [
  { id: ALL_KEY, name: '全部流程', fixed: true },
  { id: NONE_KEY, name: '未分类', count: unfiledCount.value, fixed: true },
  ...folders.value.map(toTreeItem),
])

const rows = computed<FlowSummary[]>(() => store.items)

/** 移动到目录的下拉数据：根级「未分类」+ 全部目录 */
const moveOptions = computed<TreeItem[]>(() => [
  { id: NONE_KEY, name: '未分类' },
  ...folders.value.map(toTreeItem),
])

const currentFolderName = computed(() => {
  if (selectedKey.value === ALL_KEY) return '全部流程'
  if (selectedKey.value === NONE_KEY) return '未分类'
  return folderNames.value.get(selectedKey.value) ?? '目录'
})

function buildFilter(): ListFlowsFilter {
  const filter: ListFlowsFilter = {}
  const keyword = search.value.trim()
  if (keyword) filter.search = keyword
  if (selectedKey.value === NONE_KEY) filter.folderId = 'root'
  else if (selectedKey.value !== ALL_KEY) filter.folderId = selectedKey.value
  return filter
}

async function reload(): Promise<void> {
  await store.load(buildFilter(), true)
}

async function refreshFolders(): Promise<void> {
  try {
    folders.value = await listFolders()
    foldersError.value = null
  } catch (error) {
    foldersError.value = errorText(error, '目录加载失败')
    folders.value = []
  }
}

onMounted(() => {
  void reload()
  void refreshFolders()
})

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    searchTimer = null
    void reload()
  }, SEARCH_DELAY)
})

function onNodeClick(data: TreeItem): void {
  if (data.id === selectedKey.value) return
  selectedKey.value = data.id
  void reload()
}

async function promptFolderName(title: string, initial = ''): Promise<string | null> {
  try {
    const result = await ElMessageBox.prompt(title, '流程目录', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputValue: initial,
      inputPlaceholder: '目录名称',
      inputValidator: (value: string) => (value?.trim() ? true : '请填写目录名称'),
    })
    return result.value.trim()
  } catch {
    return null
  }
}

async function createFolderDialog(parentId: string | null): Promise<void> {
  const name = await promptFolderName(parentId ? '新建子目录' : '新建目录')
  if (!name) return
  try {
    await createFolder({ name, parentId })
    await refreshFolders()
    ElMessage.success('目录已创建')
  } catch (error) {
    ElMessage.error(errorText(error, '新建目录失败'))
  }
}

async function renameFolder(node: TreeItem): Promise<void> {
  const name = await promptFolderName('重命名目录', node.name)
  if (!name || name === node.name) return
  try {
    await updateFolder(node.id, { name })
    await refreshFolders()
    ElMessage.success('已重命名')
  } catch (error) {
    ElMessage.error(errorText(error, '重命名失败'))
  }
}

async function deleteFolder(node: TreeItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除目录「${node.name}」？`, '删除目录', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await removeFolder(node.id)
    if (selectedKey.value === node.id) selectedKey.value = ALL_KEY
    await Promise.all([refreshFolders(), reload()])
    ElMessage.success('目录已删除')
  } catch (error) {
    // 非空目录由后端返回 409，文案直接来自 detail
    ElMessage.error(errorText(error, '删除失败'))
  }
}

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
    await reload()
    router.push({ name: 'flow-edit', params: { id: created.id } })
  } catch (error) {
    ElMessage.error(errorText(error, '导入流程失败'))
  } finally {
    importing.value = false
  }
}

function openMove(row: FlowSummary): void {
  moveTarget.value = row
  moveFolderId.value = row.folderId ?? NONE_KEY
  moveVisible.value = true
}

async function submitMove(): Promise<void> {
  const target = moveTarget.value
  if (!target) return
  moving.value = true
  try {
    await updateFlow(target.id, {
      folderId: !moveFolderId.value || moveFolderId.value === NONE_KEY ? null : moveFolderId.value,
    })
    moveVisible.value = false
    ElMessage.success('已移动')
    await Promise.all([refreshFolders(), reload()])
  } catch (error) {
    ElMessage.error(errorText(error, '移动失败'))
  } finally {
    moving.value = false
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

function folderNameOf(row: FlowSummary): string | null {
  if (!row.folderId) return null
  return folderNames.value.get(row.folderId) ?? null
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
          <el-button :loading="store.loading" @click="reload">刷新</el-button>
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

      <div class="layout">
        <aside class="side">
          <div class="side-head">
            <span class="side-title">目录</span>
            <el-button link type="primary" @click="createFolderDialog(null)">新建目录</el-button>
          </div>
          <div v-if="foldersError" class="side-hint">{{ foldersError }}</div>
          <el-tree
            :data="treeData"
            node-key="id"
            :current-node-key="selectedKey"
            :default-expand-all="true"
            :expand-on-click-node="false"
            highlight-current
            class="folder-tree"
            @node-click="onNodeClick"
          >
            <template #default="{ data }">
              <div class="tree-row">
                <el-icon class="tree-icon"><Folder /></el-icon>
                <span class="tree-label">{{ data.name }}</span>
                <span v-if="typeof data.count === 'number'" class="tree-count">{{ data.count }}</span>
                <span v-if="!data.fixed" class="tree-actions">
                  <el-button link size="small" @click.stop="createFolderDialog(data.id)">＋</el-button>
                  <el-button link size="small" @click.stop="renameFolder(data)">改名</el-button>
                  <el-button link size="small" type="danger" @click.stop="deleteFolder(data)">
                    删除
                  </el-button>
                </span>
              </div>
            </template>
          </el-tree>
        </aside>

        <section class="main">
          <div class="filters">
            <span class="filter-label">{{ currentFolderName }}</span>
            <el-input
              v-model="search"
              placeholder="搜索名称或描述"
              clearable
              class="search"
              :prefix-icon="Search"
            />
          </div>

          <el-table
            v-loading="store.loading"
            :data="rows"
            row-key="id"
            class="table"
            @row-click="openEdit"
          >
            <el-table-column label="名称" min-width="240">
              <template #default="{ row }">
                <div class="name-cell">
                  <span class="name">{{ row.name }}</span>
                  <span class="sub">
                    <span v-if="folderNameOf(row)" class="folder">{{ folderNameOf(row) }}</span>
                    <span v-if="row.description" class="desc">{{ row.description }}</span>
                  </span>
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
            <el-table-column label="操作" width="270" align="right">
              <template #default="{ row }">
                <div class="row-actions" @click.stop>
                  <el-button link type="primary" @click="openEdit(row)">设计</el-button>
                  <el-button link @click="openReleases(row)">版本</el-button>
                  <el-button link @click="openRun(row)">执行</el-button>
                  <el-button link @click="openMove(row)">移动到…</el-button>
                </div>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                :description="search ? '没有匹配的流程' : '这个目录下还没有流程'"
                :image-size="80"
              />
            </template>
          </el-table>
        </section>
      </div>
    </main>

    <el-dialog v-model="moveVisible" title="移动到目录" width="420px">
      <div class="move">
        <p class="hint">把「{{ moveTarget?.name }}」放到：</p>
        <el-tree-select
          v-model="moveFolderId"
          :data="moveOptions"
          node-key="id"
          :props="{ label: 'name', children: 'children' }"
          check-strictly
          :render-after-expand="false"
          default-expand-all
          placeholder="选择目录"
          class="move-select"
        />
      </div>
      <template #footer>
        <el-button @click="moveVisible = false">取消</el-button>
        <el-button type="primary" :loading="moving" @click="submitMove">确定</el-button>
      </template>
    </el-dialog>

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

.layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.side {
  width: 240px;
  flex: none;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.side-title {
  font-size: 12px;
  font-weight: 600;
}

.side-hint {
  margin-bottom: 8px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-color-warning);
}

.folder-tree {
  background: transparent;
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding-right: 4px;
  font-size: 13px;
}

.tree-icon {
  color: var(--el-text-color-secondary);
}

.tree-label {
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.tree-count {
  flex: none;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.tree-actions {
  display: none;
  flex: none;
  gap: 2px;
}

.tree-row:hover .tree-actions {
  display: inline-flex;
}

.tree-actions :deep(.el-button) {
  padding: 0 2px;
  font-size: 12px;
}

.main {
  flex: 1;
  min-width: 0;
}

.filters {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.filter-label {
  font-size: 13px;
  font-weight: 600;
}

.search {
  width: 260px;
  margin-left: auto;
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

.sub {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.folder {
  flex: none;
  color: var(--el-color-primary);
}

.desc {
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

.import-upload {
  margin-bottom: 12px;
}

.upload-hint {
  padding: 8px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.move {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.move .hint {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.move-select {
  width: 100%;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
