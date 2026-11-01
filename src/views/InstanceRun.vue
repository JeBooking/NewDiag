<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import AppHeader from '@/components/layout/AppHeader.vue'
import ReleaseInputForm from '@/components/panels/ReleaseInputForm.vue'
import type { FlowDetail, ReleaseSummary } from '@/domain/types'
import { errorText } from '@/services/api'
import { getFlow, getRelease, listReleases, startInstance, type ReleaseDetail } from '@/services/flows'

const route = useRoute()
const router = useRouter()
const flowId = computed(() => String(route.params.id))

const flow = ref<FlowDetail | null>(null)
const releases = ref<ReleaseSummary[]>([])
const release = ref<ReleaseDetail | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const releaseError = ref<string | null>(null)
const starting = ref(false)

const formRef = ref<InstanceType<typeof ReleaseInputForm> | null>(null)
const missingLabels = ref<string[]>([])

const latest = computed(() => releases.value[0] ?? null)

/** 老版本没有冻结启动输入，直接开始即可 */
const hasInputs = computed(() => (release.value?.inputs?.length ?? 0) > 0)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  releaseError.value = null
  release.value = null
  try {
    const [detail, list] = await Promise.all([getFlow(flowId.value), listReleases(flowId.value)])
    flow.value = detail
    releases.value = list.slice().sort((a, b) => b.versionNo - a.versionNo)
  } catch (err) {
    error.value = errorText(err, '加载流程失败')
    loading.value = false
    return
  }
  const target = latest.value
  if (target) {
    try {
      release.value = await getRelease(target.id)
    } catch (err) {
      releaseError.value = errorText(err, '读取发布版本的启动参数失败')
    }
  }
  loading.value = false
}

onMounted(load)

async function start(): Promise<void> {
  if (!latest.value) return
  const missing = formRef.value?.missing() ?? []
  if (missing.length > 0) {
    ElMessage.warning(`请填写：${missing.join('、')}`)
    return
  }
  starting.value = true
  try {
    const instance = await startInstance(flowId.value, {
      releaseId: latest.value.id,
      input: formRef.value?.build() ?? { global: {}, scope: {} },
    })
    await router.push({ name: 'instance', params: { id: instance.id } })
  } catch (err) {
    ElMessage.error(errorText(err, '启动失败'))
  } finally {
    starting.value = false
  }
}
</script>

<template>
  <div class="page">
    <AppHeader />
    <main class="content">
      <div v-if="loading" class="state" v-loading="true" />

      <el-result v-else-if="error" icon="error" title="加载失败" :sub-title="error">
        <template #extra>
          <el-button @click="load">重试</el-button>
          <el-button @click="router.push({ name: 'flows' })">返回流程列表</el-button>
        </template>
      </el-result>

      <div v-else class="start-card">
        <div class="head">
          <div>
            <h1>{{ flow?.name || '未命名流程' }}</h1>
            <p class="meta">
              <template v-if="latest">按 v{{ latest.versionNo }} 的步骤表执行，启动时固定本次参数</template>
              <template v-else>还没有发布版本</template>
            </p>
          </div>
          <el-button link @click="router.push({ name: 'instances' })">历史执行 →</el-button>
        </div>

        <el-alert
          v-if="!latest"
          type="warning"
          :closable="false"
          show-icon
          title="需要先发布一个版本才能执行"
          class="notice"
        >
          <el-button
            link
            type="primary"
            @click="router.push({ name: 'flow-releases', params: { id: flowId } })"
          >
            去发布
          </el-button>
        </el-alert>

        <template v-else>
          <el-alert
            v-if="releaseError"
            type="warning"
            :closable="false"
            show-icon
            :title="releaseError"
            class="notice"
          >
            <el-button link type="primary" @click="load">重试</el-button>
          </el-alert>

          <div v-if="!hasInputs && !releaseError" class="section-hint">
            该版本没有声明启动参数，直接开始即可
          </div>

          <ReleaseInputForm
            v-if="hasInputs"
            ref="formRef"
            :inputs="release?.inputs ?? []"
            @update:missing="missingLabels = $event"
          />

          <div class="actions">
            <span v-if="missingLabels.length" class="missing">
              还缺 {{ missingLabels.length }} 项必填
            </span>
            <el-button type="primary" :loading="starting" @click="start">开始执行</el-button>
          </div>
        </template>
      </div>
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
  display: flex;
  flex: 1;
  align-items: flex-start;
  justify-content: center;
  padding: 32px 24px;
  overflow: auto;
  background: var(--el-bg-color-page);
}

.start-card {
  width: 100%;
  max-width: 720px;
  padding: 24px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

h1 {
  margin: 0 0 4px;
  font-size: 18px;
  font-weight: 600;
}

.meta {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.notice {
  margin-bottom: 12px;
}

.section-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
}

.missing {
  font-size: 12px;
  color: var(--el-color-danger);
}

.state {
  width: 100%;
  height: 240px;
}
</style>
