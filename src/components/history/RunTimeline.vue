<script setup lang="ts">
import { computed } from 'vue'
import type { FlowDoc, RunRecord, RunStatus } from '@/domain/types'

const props = defineProps<{ runs: RunRecord[]; doc?: FlowDoc | null }>()

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

const items = computed(() =>
  props.runs.slice().sort((a, b) => a.startedAt.localeCompare(b.startedAt)),
)

function nodeTitle(nodeId: string): string {
  const node = props.doc?.nodes.find((item) => item.id === nodeId)
  return node?.title || nodeId
}

const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

function pretty(value: unknown): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function hasPayload(run: RunRecord): boolean {
  return run.input !== undefined || run.output !== undefined || run.evidence !== undefined
}
</script>

<template>
  <div class="run-timeline">
    <el-empty v-if="!items.length" description="还没有运行记录" :image-size="60" />
    <el-timeline v-else>
      <el-timeline-item
        v-for="run in items"
        :key="run.id"
        :timestamp="formatTime(run.startedAt)"
        placement="top"
        :type="run.status === 'failed' ? 'danger' : run.status === 'abandoned' ? 'info' : 'primary'"
      >
        <div class="row">
          <span class="name">{{ nodeTitle(run.nodeId) }}</span>
          <el-tag size="small" :type="STATUS_TYPE[run.status]" effect="plain">
            {{ STATUS_TEXT[run.status] }}
          </el-tag>
          <span class="meta">第 {{ run.generation }} 代 · 尝试 {{ run.attempt }}</span>
        </div>
        <div v-if="run.error" class="error">{{ run.error }}</div>
        <details v-if="hasPayload(run)" class="payload">
          <summary>输入 / 输出</summary>
          <div v-if="run.input !== undefined" class="payload-block">
            <div class="payload-label">输入</div>
            <pre>{{ pretty(run.input) }}</pre>
          </div>
          <div v-if="run.output !== undefined" class="payload-block">
            <div class="payload-label">输出</div>
            <pre>{{ pretty(run.output) }}</pre>
          </div>
          <div v-if="run.evidence !== undefined" class="payload-block">
            <div class="payload-label">依据</div>
            <pre>{{ pretty(run.evidence) }}</pre>
          </div>
        </details>
      </el-timeline-item>
    </el-timeline>
  </div>
</template>

<style scoped>
.run-timeline {
  padding: 4px 0;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.name {
  font-size: 12px;
  font-weight: 500;
}

.meta {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.error {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-color-danger);
  white-space: pre-wrap;
}

.payload {
  margin-top: 6px;
}

.payload summary {
  cursor: pointer;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.payload-block {
  margin-top: 6px;
}

.payload-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.payload pre {
  margin: 2px 0 0;
  padding: 6px 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.6;
  max-height: 200px;
  overflow: auto;
}
</style>
