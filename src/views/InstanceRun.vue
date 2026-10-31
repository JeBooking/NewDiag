<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/layout/AppHeader.vue'
import type { InstanceSummary } from '@/domain/types'
import { errorText } from '@/services/api'
import { listInstances } from '@/services/instances'

const route = useRoute()
const router = useRouter()

const flowId = computed(() => String(route.params.id))
const loading = ref(true)
const error = ref<string | null>(null)
const empty = ref(false)

onMounted(async () => {
  try {
    const list = await listInstances({ flowId: flowId.value })
    if (list.length === 0) {
      empty.value = true
      return
    }
    const latest = list
      .slice()
      .sort((a: InstanceSummary, b: InstanceSummary) => b.startedAt.localeCompare(a.startedAt))[0]
    await router.replace({ name: 'instance', params: { id: latest.id } })
  } catch (err) {
    error.value = errorText(err, '查找执行记录失败')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="page">
    <AppHeader />
    <main class="content">
      <div v-if="loading" class="state" v-loading="true">
        <span>正在查找该流程的最新执行记录…</span>
      </div>
      <el-result
        v-else-if="empty"
        icon="info"
        title="该流程还没有执行记录"
        sub-title="先在设计页保存并发布版本，再从版本列表启动一次诊断"
      >
        <template #extra>
          <el-button @click="router.push({ name: 'flow-edit', params: { id: flowId } })">
            去设计页
          </el-button>
          <el-button type="primary" @click="router.push({ name: 'flow-releases', params: { id: flowId } })">
            看版本列表
          </el-button>
        </template>
      </el-result>
      <el-result v-else icon="error" title="无法打开执行页" :sub-title="error || '未知错误'">
        <template #extra>
          <el-button @click="router.push({ name: 'flows' })">返回流程列表</el-button>
        </template>
      </el-result>
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
  display: flex;
  align-items: center;
  justify-content: center;
}

.state {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  min-height: 80px;
  display: flex;
  align-items: center;
}
</style>
