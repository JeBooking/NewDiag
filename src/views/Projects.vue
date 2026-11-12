<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppHeader from '@/components/layout/AppHeader.vue'
import { errorText } from '@/services/api'
import {
  createProject,
  deleteProject,
  fetchProjectCatalog,
  listProjects,
  refreshProject,
  updateProject,
  type CreateProjectPayload,
  type Project,
  type ProjectFunction,
  type ProjectFunctions,
} from '@/services/projects'

const projects = ref<Project[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const refreshingId = ref<string | null>(null)

const createVisible = ref(false)
const submitting = ref(false)
const form = ref<CreateProjectPayload>({
  name: '',
  path: '',
  module: '',
  python: 'python',
  factory: '',
})

const drawerVisible = ref(false)
const functionsLoading = ref(false)
const activeProject = ref<Project | null>(null)
const functions = ref<ProjectFunction[]>([])
const functionsError = ref('')

const editVisible = ref(false)
const editSaving = ref(false)
const editingId = ref<string | null>(null)
const editName = ref('')
const editForm = ref({ path: '', module: '', python: '', factory: '' })
const deletingId = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    projects.value = await listProjects()
  } catch (err) {
    error.value = errorText(err, '加载工程列表失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
})

function openCreate(): void {
  form.value = { name: '', path: '', module: '', python: 'python', factory: '' }
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  const payload = form.value
  if (!payload.name.trim() || !payload.path.trim() || !payload.module.trim()) {
    ElMessage.warning('名称、路径、模块为必填')
    return
  }
  submitting.value = true
  try {
    await createProject({
      name: payload.name.trim(),
      path: payload.path.trim(),
      module: payload.module.trim(),
      python: payload.python.trim() || 'python',
      factory: payload.factory.trim(),
    })
    createVisible.value = false
    ElMessage.success('工程已登记')
    await load()
  } catch (err) {
    ElMessage.error(errorText(err, '登记工程失败'))
  } finally {
    submitting.value = false
  }
}

function openEdit(row: Project): void {
  editingId.value = row.id
  editName.value = row.name
  editForm.value = {
    path: row.path,
    module: row.module,
    python: row.python,
    factory: row.factory,
  }
  editVisible.value = true
}

async function submitEdit(): Promise<void> {
  const id = editingId.value
  if (!id) return
  const payload = editForm.value
  if (!payload.path.trim() || !payload.module.trim()) {
    ElMessage.warning('路径、模块为必填')
    return
  }
  editSaving.value = true
  try {
    // 解释器留空传空串，由后端清成 NULL 回落默认解释器；这里不能跟登记一样补 'python'，否则清不掉
    await updateProject(id, {
      path: payload.path.trim(),
      module: payload.module.trim(),
      python: payload.python.trim(),
      factory: payload.factory.trim(),
    })
    editVisible.value = false
    ElMessage.success('工程已更新')
    await load()
  } catch (err) {
    ElMessage.error(errorText(err, '更新工程失败'))
  } finally {
    editSaving.value = false
  }
}

async function remove(row: Project): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除后引用该工程的流程将无法发布或执行，直到重新登记同名工程。确认删除「${row.name}」？`,
      '删除工程',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deletingId.value = row.id
  try {
    await deleteProject(row.id)
    if (activeProject.value?.id === row.id) {
      drawerVisible.value = false
      activeProject.value = null
    }
    ElMessage.success('工程已删除')
    await load()
  } catch (err) {
    ElMessage.error(errorText(err, '删除工程失败'))
  } finally {
    deletingId.value = null
  }
}

async function discover(row: Project): Promise<ProjectFunctions> {
  return row.factory
    ? refreshProject(row.id)
    : { functions: await fetchProjectCatalog(row.id, row.module), error: null }
}

async function refresh(row: Project): Promise<void> {
  refreshingId.value = row.id
  try {
    const result = await discover(row)
    if (result.error) ElMessage.warning(`接口发现失败：${result.error}`)
    else ElMessage.success('已重新发现接口')
    if (activeProject.value?.id === row.id) {
      functions.value = result.functions
      functionsError.value = result.error ?? ''
    }
  } catch (err) {
    const message = errorText(err, '刷新失败')
    if (activeProject.value?.id === row.id) {
      functions.value = []
      functionsError.value = message
    }
    ElMessage.error(message)
  } finally {
    refreshingId.value = null
  }
}

async function loadFunctions(row: Project): Promise<void> {
  functionsLoading.value = true
  functions.value = []
  functionsError.value = ''
  try {
    const result = await discover(row)
    functions.value = result.functions
    functionsError.value = result.error ?? ''
  } catch (err) {
    functionsError.value = errorText(err, '读取接口失败')
    ElMessage.error(functionsError.value)
  } finally {
    functionsLoading.value = false
  }
}

async function openFunctions(row: Project): Promise<void> {
  activeProject.value = row
  drawerVisible.value = true
  await loadFunctions(row)
}

function signatureText(fn: ProjectFunction): string {
  const params = fn.params.map((param) => {
    const annotation = param.annotation ? `: ${param.annotation}` : ''
    const defaultValue =
      param.default !== null && param.default !== undefined ? ` = ${param.default}` : ''
    return `${param.name}${annotation}${defaultValue}`
  })
  const returns = fn.returns ? ` -> ${fn.returns}` : ''
  return `${fn.name}(${params.join(', ')})${returns}`
}
</script>

<template>
  <div class="page">
    <AppHeader />
    <main class="content">
      <div class="page-head">
        <div>
          <h1>工程管理</h1>
          <p>登记业务 Python 工程，接口在运行时发现，不落库</p>
        </div>
        <div class="head-actions">
          <el-button :loading="loading" @click="load">刷新</el-button>
          <el-button type="primary" @click="openCreate">登记工程</el-button>
        </div>
      </div>

      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
        class="error"
      />

      <el-table v-loading="loading" :data="projects" row-key="id" class="table">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="path" label="路径" min-width="240" show-overflow-tooltip />
        <el-table-column prop="module" label="模块" min-width="140" show-overflow-tooltip />
        <el-table-column prop="python" label="解释器" width="160" show-overflow-tooltip />
        <el-table-column prop="factory" label="工厂" width="100" />
        <el-table-column label="操作" width="300" align="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" @click="openFunctions(row)">接口</el-button>
              <el-button
                link
                :loading="refreshingId === row.id"
                :disabled="refreshingId === row.id"
                @click="refresh(row)"
              >
                重新发现
              </el-button>
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button
                link
                type="danger"
                :loading="deletingId === row.id"
                :disabled="deletingId === row.id"
                @click="remove(row)"
              >
                删除
              </el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有登记工程" :image-size="80" />
        </template>
      </el-table>
    </main>

    <el-dialog v-model="createVisible" title="登记工程" width="520px">
      <el-form label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="例如：cooling" />
        </el-form-item>
        <el-form-item label="工程路径" required>
          <el-input v-model="form.path" placeholder="D:/projects/cooling" />
        </el-form-item>
        <el-form-item label="模块" required>
          <el-input v-model="form.module" placeholder="cooling.api" />
        </el-form-item>
        <el-form-item label="解释器">
          <el-input v-model="form.python" placeholder="python" />
        </el-form-item>
        <el-form-item label="工厂函数">
          <el-input v-model="form.factory" placeholder="组合接入留空；旧实例工厂按需填写" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">登记</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="编辑工程" width="520px">
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input :model-value="editName" disabled />
          <div class="hint">名称是流程绑定的引用键，登记后不可修改</div>
        </el-form-item>
        <el-form-item label="工程路径" required>
          <el-input v-model="editForm.path" placeholder="D:/projects/cooling" />
        </el-form-item>
        <el-form-item label="模块" required>
          <el-input v-model="editForm.module" placeholder="cooling.api" />
        </el-form-item>
        <el-form-item label="解释器">
          <el-input v-model="editForm.python" placeholder="留空则使用后端默认解释器" />
        </el-form-item>
        <el-form-item label="工厂函数">
          <el-input v-model="editForm.factory" placeholder="组合接入留空；旧实例工厂按需填写" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editSaving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="drawerVisible" :title="activeProject?.name || '接口'" size="520px">
      <div v-loading="functionsLoading" class="functions">
        <el-alert v-if="functionsError" :title="functionsError" type="error" :closable="false" show-icon />
        <el-empty v-if="!functionsLoading && !functionsError && !functions.length" description="没有发现可用接口" :image-size="60" />
        <div v-for="fn in functions" :key="fn.name" class="function">
          <div class="signature">{{ signatureText(fn) }}</div>
          <div v-if="fn.doc" class="doc">{{ fn.doc }}</div>
        </div>
      </div>
    </el-drawer>
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

.row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
}

.hint {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.functions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 120px;
}

.function {
  padding: 8px 10px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.signature {
  font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}

.doc {
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
