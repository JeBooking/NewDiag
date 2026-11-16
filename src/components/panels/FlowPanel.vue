<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { FlowDetail } from '@/domain/types'

const props = defineProps<{
  flow: FlowDetail
  readonly?: boolean
  publishing?: boolean
}>()
const emit = defineEmits<{
  (e: 'update-meta', patch: { name?: string }): void
  (e: 'publish'): void
}>()

const name = ref(props.flow.name)
const nameDirty = computed(() => name.value.trim() !== props.flow.name && name.value.trim().length > 0)

watch(
  () => props.flow.name,
  (value) => {
    name.value = value
  },
)

const latestRelease = computed(() => {
  const list = props.flow.releases ?? []
  return list.reduce<(typeof list)[number] | null>(
    (latest, item) => (!latest || item.versionNo > latest.versionNo ? item : latest),
    null,
  )
})

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

function commitName(): void {
  if (!nameDirty.value) return
  emit('update-meta', { name: name.value.trim() })
}
</script>

<template>
  <div class="flow-panel">
    <div class="title">流程属性</div>

    <label class="label">名称</label>
    <el-input
      v-model="name"
      :disabled="readonly"
      placeholder="流程名称"
      @blur="commitName"
      @keyup.enter="commitName"
    />

    <div class="section-title">版本</div>
    <div class="kv">
      <span class="key">发布版本数</span>
      <span class="value">{{ flow.releases?.length ?? 0 }}</span>
    </div>
    <div class="kv">
      <span class="key">最近发布</span>
      <span class="value">
        <template v-if="latestRelease">
          v{{ latestRelease.versionNo }} · {{ formatTime(latestRelease.publishedAt) }}
        </template>
        <template v-else>尚未发布</template>
      </span>
    </div>
    <div class="kv">
      <span class="key">最近更新</span>
      <span class="value">{{ formatTime(flow.updatedAt) }}</span>
    </div>

    <el-button
      class="publish"
      type="primary"
      :disabled="readonly"
      :loading="publishing"
      @click="emit('publish')"
    >
      发布新版本
    </el-button>
    <div class="section-hint">发布前会按校验规则编译为步骤表，版本不可修改</div>
  </div>
</template>

<style scoped>
.flow-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.title {
  font-size: 13px;
  font-weight: 600;
}

.label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.section-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.kv {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.key {
  color: var(--el-text-color-secondary);
  flex: none;
}

.value {
  text-align: right;
  color: var(--el-text-color-regular);
}

.publish {
  margin-top: 16px;
  width: 100%;
}
</style>
