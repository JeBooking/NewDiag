<script setup lang="ts">
import { computed } from 'vue'
import type { FlowDoc, RunRecord, Token, TokenStatus } from '@/domain/types'

const props = defineProps<{
  tokens: Token[]
  runs: RunRecord[]
  doc?: FlowDoc | null
}>()

const STATUS_TEXT: Record<TokenStatus, string> = {
  queued: '排队',
  running: '执行中',
  waiting: '待处理',
  done: '完成',
  failed: '失败',
  abandoned: '已放弃',
}

const STATUS_TYPE: Record<TokenStatus, 'primary' | 'success' | 'warning' | 'danger' | 'info'> = {
  queued: 'info',
  running: 'primary',
  waiting: 'warning',
  done: 'success',
  failed: 'danger',
  abandoned: 'info',
}

interface PathRow {
  token: Token
  nodes: string[]
  branch: string
  status: TokenStatus
}

interface PathGroup {
  generation: number
  rows: PathRow[]
}

function nodeTitle(nodeId: string): string {
  const node = props.doc?.nodes.find((item) => item.id === nodeId)
  return node?.title || nodeId
}

const branchText = (branch?: string): string => {
  if (branch === 'yes') return '是'
  if (branch === 'no') return '否'
  return branch || '—'
}

function runNodes(tokenId: string): RunRecord[] {
  return props.runs
    .filter((run) => run.tokenId === tokenId)
    .slice()
    .sort((a, b) => a.startedAt.localeCompare(b.startedAt))
}

const tokenById = computed(() => {
  const map = new Map<string, Token>()
  for (const token of props.tokens) map.set(token.id, token)
  return map
})

/** 沿 parentTokenId 向上追溯，得到从入口到当前 token 的完整路径 */
function chainOf(token: Token): Token[] {
  const chain: Token[] = []
  const seen = new Set<string>()
  let current: Token | undefined = token
  while (current && !seen.has(current.id)) {
    seen.add(current.id)
    chain.unshift(current)
    current = current.parentTokenId ? tokenById.value.get(current.parentTokenId) : undefined
  }
  return chain
}

function dedupe(nodes: string[]): string[] {
  const result: string[] = []
  for (const node of nodes) {
    if (result[result.length - 1] !== node) result.push(node)
  }
  return result
}

function pathNodes(token: Token): string[] {
  const chain = chainOf(token)
  const nodes = dedupe(chain.map((item) => nodeTitle(item.nodeId)))
  // 后端还没回填 parentTokenId 时退回按 run 时间重建，避免只剩一个节点
  if (nodes.length === 1) {
    const executed = dedupe(runNodes(token.id).map((run) => nodeTitle(run.nodeId)))
    if (executed.length > nodes.length) nodes.splice(0, nodes.length, ...executed)
  }
  if (!nodes.length) nodes.push(nodeTitle(token.nodeId))
  if (token.status === 'waiting' || token.status === 'running' || token.status === 'queued') {
    const last = nodes[nodes.length - 1]
    if (!last.endsWith('（当前）')) nodes[nodes.length - 1] = `${last}（当前）`
  }
  return nodes
}

const groups = computed<PathGroup[]>(() => {
  const map = new Map<number, Token[]>()
  for (const token of props.tokens) {
    const list = map.get(token.generation) ?? []
    list.push(token)
    map.set(token.generation, list)
  }
  return [...map.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([generation, tokens]) => ({
      generation,
      rows: tokens
        .slice()
        .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
        .map((token) => ({
          token,
          nodes: pathNodes(token),
          branch: branchText(token.branch),
          status: token.status,
        })),
    }))
})
</script>

<template>
  <div class="path-list">
    <el-empty v-if="!groups.length" description="还没有推进路径" :image-size="60" />
    <div v-for="group in groups" :key="group.generation" class="group">
      <div class="group-title">第 {{ group.generation }} 代</div>
      <div v-for="row in group.rows" :key="row.token.id" class="path-row">
        <div class="path-head">
          <el-tag size="small" :type="STATUS_TYPE[row.status]" effect="plain">
            {{ STATUS_TEXT[row.status] }}
          </el-tag>
          <span class="branch">入口分支：{{ row.branch }}</span>
        </div>
        <div class="chain">
          <template v-for="(node, index) in row.nodes" :key="`${row.token.id}-${index}`">
            <span class="node">{{ node }}</span>
            <span v-if="index < row.nodes.length - 1" class="arrow">→</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.path-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.group-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.path-row {
  padding: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.path-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.branch {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.chain {
  font-size: 12px;
  line-height: 1.8;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.node {
  background: var(--el-fill-color-light);
  border-radius: 4px;
  padding: 0 6px;
}

.arrow {
  color: var(--el-text-color-placeholder);
}
</style>
