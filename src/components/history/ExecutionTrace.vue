<script setup lang="ts">
import { computed } from 'vue'
import type { FlowDoc, RunRecord, RunStatus, Token, TokenStatus } from '@/domain/types'

const props = defineProps<{ tokens: Token[]; runs: RunRecord[]; doc?: FlowDoc | null }>()

const items = computed(() => props.tokens
  .filter((token) => token.visitedAt)
  .slice()
  .sort((a, b) => a.visitedAt!.localeCompare(b.visitedAt!)
    || (a.order ?? 0) - (b.order ?? 0) || a.id.localeCompare(b.id)))

const statusText: Record<TokenStatus, string> = {
  queued: '排队', running: '执行中', waiting: '待处理', done: '完成',
  failed: '失败', abandoned: '已放弃',
}

function title(nodeId: string): string {
  return props.doc?.nodes.find((node) => node.id === nodeId)?.title || nodeId
}

function time(value?: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

const resultText: Record<RunStatus, string> = { failed: '失败', ok: '成功', abandoned: '已放弃' }
const runsByToken = computed(() => {
  const grouped: Record<string, RunRecord[]> = {}
  for (const run of props.runs) (grouped[run.tokenId] ??= []).push(run)
  return grouped
})
</script>

<template>
  <div class="execution-trace">
    <el-empty v-if="!items.length" description="暂无实际到访记录" :image-size="60" />
    <ol v-else class="visits">
      <li v-for="(token, index) in items" :key="token.id" class="visit">
        <span class="sequence">{{ index + 1 }}</span>
        <span class="name">{{ title(token.nodeId) }}</span>
        <el-tag size="small" :type="token.status === 'failed' ? 'danger' : 'info'" effect="plain">
          {{ statusText[token.status] }}
        </el-tag>
        <span class="meta">第 {{ token.generation }} 代 · {{ time(token.visitedAt) }}</span>
        <details v-if="runsByToken[token.id]?.length" class="outcome">
          <summary>
            执行结果：{{ runsByToken[token.id].map((run) => resultText[run.status]).join('、') }}
          </summary>
          <div v-for="run in runsByToken[token.id]" :key="run.id" class="error">
            {{ run.error || `尝试 ${run.attempt} · ${time(run.startedAt)}` }}
          </div>
        </details>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.visits {
  list-style: none;
  padding: 0;
  margin: 0;
}

.visit {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.name { font-size: 13px; }
.sequence, .meta { font-size: 12px; color: var(--el-text-color-secondary); }
.sequence { min-width: 24px; }
.outcome { font-size: 12px; }
.error { margin-top: 4px; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
