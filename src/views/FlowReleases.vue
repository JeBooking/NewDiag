<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import AppHeader from '@/components/layout/AppHeader.vue'
import ReleaseInputForm from '@/components/panels/ReleaseInputForm.vue'
import type { FlowDetail, ReleaseSummary } from '@/domain/types'
import { errorText } from '@/services/api'
import {
  exportFlowUrl,
  getRelease,
  listReleases,
  startInstance,
  type ReleaseDetail,
} from '@/services/flows'
import { useFlowsStore } from '@/stores/flows'

const route = useRoute()
const router = useRouter()
const flowsStore = useFlowsStore()

const flowId = computed(() => String(route.params.id))
const flow = ref<FlowDetail | null>(null)
const releases = ref<ReleaseSummary[]>([])
const loading = ref(false)
const loadError = ref<string | null>(null)

const planVisible = ref(false)
const planLoading = ref(false)
const planDetail = ref<ReleaseDetail | null>(null)

const startVisible = ref(false)
const startTarget = ref<ReleaseSummary | null>(null)
const startDetail = ref<ReleaseDetail | null>(null)
const startLoading = ref(false)
const startLoadError = ref<string | null>(null)
const startMissing = ref<string[]>([])
const startFormRef = ref<InstanceType<typeof ReleaseInputForm> | null>(null)
/** 老版本没有冻结启动输入时退回 JSON 输入 */
const startInput = ref('')
const starting = ref(false)

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const [detail, list] = await Promise.all([
      flowsStore.fetchDetail(flowId.value, true),
      listReleases(flowId.value),
    ])
    flow.value = detail
    releases.value = list.slice().sort((a, b) => b.versionNo - a.versionNo)
  } catch (error) {
    loadError.value = errorText(error, '加载版本列表失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
})

async function openPlan(release: ReleaseSummary): Promise<void> {
  planVisible.value = true
  planLoading.value = true
  planDetail.value = null
  try {
    planDetail.value = await getRelease(release.id)
  } catch (error) {
    ElMessage.error(errorText(error, '读取发布计划失败'))
  } finally {
    planLoading.value = false
  }
}

/** 只走发布版本冻结的输入，老版本 inputs 为空时退回 JSON */
const startHasInputs = computed(() => (startDetail.value?.inputs?.length ?? 0) > 0)

async function loadStartDetail(release: ReleaseSummary): Promise<void> {
  startLoading.value = true
  startLoadError.value = null
  try {
    startDetail.value = await getRelease(release.id)
  } catch (error) {
    startDetail.value = null
    startLoadError.value = errorText(error, '读取启动参数失败')
  } finally {
    startLoading.value = false
  }
}

function openStart(release: ReleaseSummary): void {
  startTarget.value = release
  startDetail.value = null
  startMissing.value = []
  startInput.value = ''
  startVisible.value = true
  void loadStartDetail(release)
}

/** 导指定发布版本的快照，和当前在线版本无关 */
function download(release: ReleaseSummary): void {
  const link = document.createElement('a')
  link.href = exportFlowUrl(flowId.value, release.id)
  link.download = ''
  document.body.appendChild(link)
  link.click()
  link.remove()
}

function parseStartInput(): Record<string, unknown> | null {
  const raw = startInput.value.trim()
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      ElMessage.warning('启动输入必须是 JSON 对象')
      return null
    }
    return parsed as Record<string, unknown>
  } catch {
    ElMessage.warning('启动输入不是合法的 JSON')
    return null
  }
}

async function submitStart(): Promise<void> {
  if (!startTarget.value) return
  let input: Record<string, unknown>
  if (startHasInputs.value) {
    const missing = startFormRef.value?.missing() ?? []
    if (missing.length > 0) {
      ElMessage.warning(`请填写：${missing.join('、')}`)
      return
    }
    input = startFormRef.value?.build() ?? { global: {}, scope: {} }
  } else {
    const parsed = parseStartInput()
    if (parsed === null) return
    input = parsed
  }
  starting.value = true
  try {
    const instance = await startInstance(flowId.value, {
      releaseId: startTarget.value.id,
      input,
    })
    startVisible.value = false
    ElMessage.success('已启动诊断')
    router.push({ name: 'instance', params: { id: instance.id } })
  } catch (error) {
    ElMessage.error(errorText(error, '启动失败'))
  } finally {
    starting.value = false
  }
}

const planJson = computed(() =>
  planDetail.value?.plan ? JSON.stringify(planDetail.value.plan, null, 2) : '',
)
</script>

<template>
  <div class="page">
    <AppHeader />
    <main class="content">
      <div class="page-head">
        <div>
          <el-button text @click="router.push({ name: 'flows' })">← 返回列表</el-button>
          <h1>{{ flow?.name || '流程版本' }}</h1>
          <p>发布版本不可修改；执行只认发布版本</p>
        </div>
        <div class="head-actions">
          <el-button :loading="loading" @click="load">刷新</el-button>
          <el-button type="primary" @click="router.push({ name: 'flow-edit', params: { id: flowId } })">
            去设计
          </el-button>
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

      <el-table v-loading="loading" :data="releases" row-key="id" class="table">
        <el-table-column label="版本" width="100">
          <template #default="{ row }">
            <span class="version">v{{ row.versionNo }}</span>
          </template>
        </el-table-column>
        <el-table-column label="发布时间" width="190">
          <template #default="{ row }">{{ formatTime(row.publishedAt) }}</template>
        </el-table-column>
        <el-table-column label="发布人" width="120">
          <template #default="{ row }">{{ row.publishedBy }}</template>
        </el-table-column>
        <el-table-column label="备注" min-width="200">
          <template #default="{ row }">{{ row.note || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240" align="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" @click="openPlan(row)">查看计划</el-button>
              <el-button link @click="openStart(row)">启动执行</el-button>
              <el-button link @click="download(row)">导出</el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有发布版本，回到设计页发布" :image-size="80" />
        </template>
      </el-table>
    </main>

    <el-drawer v-model="planVisible" title="发布计划" size="560px">
      <div v-loading="planLoading" class="plan">
        <template v-if="planDetail">
          <div class="plan-meta">
            <span>v{{ planDetail.versionNo }}</span>
            <span>入口节点：{{ planDetail.plan?.entry || '—' }}</span>
            <span>模式：{{ planDetail.plan?.mode === 'serial' ? '串行' : '并行' }}</span>
          </div>
          <pre class="json">{{ planJson }}</pre>
        </template>
        <el-empty v-else-if="!planLoading" description="计划不可用" :image-size="60" />
      </div>
    </el-drawer>

    <el-dialog v-model="startVisible" title="启动诊断" width="560px">
      <div class="start">
        <p class="hint">
          将按 v{{ startTarget?.versionNo }} 的步骤表执行，启动时采集一次快照，之后只读。
        </p>

        <el-alert
          v-if="startLoadError"
          type="warning"
          :closable="false"
          show-icon
          :title="startLoadError"
        >
          <el-button
            link
            type="primary"
            @click="startTarget && loadStartDetail(startTarget)"
          >
            重试
          </el-button>
        </el-alert>

        <div v-if="startLoading" class="start-loading" v-loading="true" />

        <ReleaseInputForm
          v-else-if="startHasInputs"
          ref="startFormRef"
          :inputs="startDetail?.inputs ?? []"
          @update:missing="startMissing = $event"
        />

        <template v-else>
          <p class="hint">该版本没有声明启动参数，可留空直接启动；需要补充时按 JSON 填写。</p>
          <label class="label">启动输入（JSON 对象，可留空）</label>
          <el-input
            v-model="startInput"
            type="textarea"
            :rows="6"
            placeholder='{ "deviceId": "A-01" }'
          />
        </template>
      </div>
      <template #footer>
        <span v-if="startHasInputs && startMissing.length" class="footer-note">
          还缺 {{ startMissing.length }} 项必填
        </span>
        <el-button @click="startVisible = false">取消</el-button>
        <el-button type="primary" :loading="starting" :disabled="startLoading" @click="submitStart">
          启动
        </el-button>
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
  padding: 16px 24px 32px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.page-head h1 {
  margin: 4px 0 4px;
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

.version {
  font-weight: 600;
}

.row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
}

.plan {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.plan-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.json {
  margin: 0;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-size: 11px;
  line-height: 1.6;
  overflow: auto;
}

.start {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.start-loading {
  height: 120px;
}

.footer-note {
  float: left;
  font-size: 12px;
  line-height: 32px;
  color: var(--el-color-danger);
}

.hint {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}

.label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
