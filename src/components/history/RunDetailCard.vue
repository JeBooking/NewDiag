<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { KIND_META } from '@/domain/kinds'
import type { DocNode, RunRecord, RunStatus } from '@/domain/types'
import { fileUrl } from '@/services/files'

const props = defineProps<{ node: DocNode; runs: RunRecord[] }>()
const emit = defineEmits<{ (event: 'close'): void }>()

const STATUS_TEXT: Record<RunStatus, string> = {
  ok: '成功',
  failed: '失败',
  abandoned: '已放弃',
}

const STATUS_TYPE: Record<RunStatus, 'success' | 'danger' | 'info'> = {
  ok: 'success',
  failed: 'danger',
  abandoned: 'info',
}

/** 设计节点时填的三段说明，就是这次执行所依据的参考数据 */
const REFERENCE_ROWS = [
  { key: 'observation', label: '观察' },
  { key: 'acquisition', label: '获取' },
  { key: 'assessment', label: '评估' },
] as const

const meta = computed(() => KIND_META[props.node.kind])
const activeId = ref<string | null>(null)

/** runs 已按开始时间倒序，默认看最新一次 */
const current = computed(
  () => props.runs.find((run) => run.id === activeId.value) ?? props.runs[0] ?? null,
)

watch(
  () => props.runs,
  (runs) => {
    // 刷新后选中项可能已不存在（回退换了代），退回最新一条
    if (activeId.value && !runs.some((run) => run.id === activeId.value)) activeId.value = null
  },
)

const referenceRows = computed(() => {
  const fields = props.node.fields
  return REFERENCE_ROWS.map((row) => ({
    label: row.label,
    value: (fields?.[row.key] ?? '').trim(),
  })).filter((row) => row.value)
})

const attachments = computed(() => props.node.attachments ?? [])
const hasReference = computed(
  () => referenceRows.value.length > 0 || attachments.value.length > 0,
)

const verdict = computed(() => {
  const evidence = current.value?.evidence
  if (!evidence || typeof evidence !== 'object') return ''
  const branch = (evidence as { branch?: unknown }).branch
  if (branch === 'yes') return '是'
  if (branch === 'no') return '否'
  return pretty(evidence)
})

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

function duration(run: RunRecord): string {
  if (!run.endedAt) return '进行中'
  const ms = new Date(run.endedAt).getTime() - new Date(run.startedAt).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const minutes = Math.floor(ms / 60000)
  const seconds = Math.round((ms % 60000) / 1000)
  return `${minutes}m ${seconds}s`
}

function pretty(value: unknown): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function runLabel(run: RunRecord): string {
  return `第${run.generation}代·${run.attempt}次`
}
</script>

<template>
  <div class="run-card" :style="{ '--kind-color': meta.color }">
    <span class="stripe" />

    <div class="head">
      <div class="head-text">
        <div class="head-row">
          <span class="kind">{{ meta.label }}</span>
          <span class="title">{{ node.title || '未命名节点' }}</span>
          <el-tag v-if="current" size="small" :type="STATUS_TYPE[current.status]" effect="plain">
            {{ STATUS_TEXT[current.status] }}
          </el-tag>
        </div>
        <div v-if="current" class="head-meta">
          <span>{{ runLabel(current) }}</span>
          <span>{{ formatTime(current.startedAt) }} → {{ current.endedAt ? formatTime(current.endedAt) : '进行中' }}</span>
          <span>耗时 {{ duration(current) }}</span>
        </div>
      </div>
      <button type="button" class="close" aria-label="关闭" @click="emit('close')">×</button>
    </div>

    <div v-if="runs.length > 1" class="switcher">
      <button
        v-for="run in runs"
        :key="run.id"
        type="button"
        class="switch-item"
        :class="{ active: run.id === current?.id }"
        @click="activeId = run.id"
      >
        <span class="dot" :class="run.status" />
        {{ runLabel(run) }}
      </button>
    </div>

    <div v-if="current" class="body">
      <div v-if="current.error" class="block">
        <div class="label is-error">错误</div>
        <pre class="error-text">{{ current.error }}</pre>
      </div>

      <div v-if="current.input !== undefined" class="block">
        <div class="label">输入</div>
        <pre>{{ pretty(current.input) }}</pre>
      </div>

      <div v-if="hasReference" class="block">
        <div class="label">参考数据</div>
        <div class="ref">
          <div v-for="row in referenceRows" :key="row.label" class="ref-row">
            <span class="ref-label">{{ row.label }}</span>
            <span class="ref-text">{{ row.value }}</span>
          </div>
          <div v-if="attachments.length" class="ref-files">
            <a
              v-for="file in attachments"
              :key="file.hash"
              :href="fileUrl(file.hash)"
              target="_blank"
              rel="noopener"
              class="ref-file"
            >
              {{ file.name }}
            </a>
          </div>
        </div>
      </div>

      <div v-if="current.output !== undefined" class="block">
        <div class="label">输出</div>
        <pre>{{ pretty(current.output) }}</pre>
      </div>

      <div v-if="verdict" class="block">
        <div class="label">判定</div>
        <div class="verdict">{{ verdict }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.run-card {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 560px;
  max-width: 100%;
  max-height: 100%;
  padding-left: 4px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
  box-shadow: 0 12px 32px rgb(15 23 42 / 18%);
  overflow: hidden;
}

.stripe {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  width: 4px;
  background: var(--kind-color);
}

.head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 12px 10px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.head-text {
  flex: 1;
  min-width: 0;
}

.head-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.kind {
  padding: 0 5px;
  border-radius: 3px;
  font-size: 11px;
  color: var(--kind-color);
  background: color-mix(in srgb, var(--kind-color) 12%, transparent);
}

.title {
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.head-meta {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.close {
  flex: none;
  padding: 0 4px;
  border: none;
  background: none;
  font-size: 18px;
  line-height: 1;
  color: var(--el-text-color-secondary);
  cursor: pointer;
}

.close:hover {
  color: var(--el-color-primary);
}

.switcher {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 8px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.switch-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  background: none;
  font: inherit;
  font-size: 11px;
  color: var(--el-text-color-regular);
  cursor: pointer;
}

.switch-item:hover {
  border-color: var(--el-color-primary-light-5);
}

.switch-item.active {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-text-color-placeholder);
}

.dot.ok {
  background: var(--el-color-success);
}

.dot.failed {
  background: var(--el-color-danger);
}

.body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 16px 16px;
  overflow: auto;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.label.is-error {
  color: var(--el-color-danger);
}

pre {
  margin: 0;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.error-text {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.ref {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
}

.ref-row {
  display: flex;
  gap: 8px;
  font-size: 11px;
  line-height: 1.6;
}

.ref-label {
  flex: none;
  width: 28px;
  color: var(--el-text-color-secondary);
}

.ref-text {
  color: var(--el-text-color-primary);
  word-break: break-word;
}

.ref-files {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.ref-file {
  font-size: 11px;
  color: var(--el-color-primary);
}

.verdict {
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  background: var(--el-fill-color-light);
}
</style>
