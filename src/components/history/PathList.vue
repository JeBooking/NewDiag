<script setup lang="ts">
import { computed } from 'vue'
import type { FlowDoc, FlowMode, RunRecord, Token } from '@/domain/types'

const props = defineProps<{
  tokens: Token[]
  runs: RunRecord[]
  doc?: FlowDoc | null
  mode?: FlowMode
}>()

type StateType = 'primary' | 'success' | 'warning' | 'danger' | 'info'

interface PathState {
  text: string
  type: StateType
}

interface RowNode {
  token: Token
  title: string
  /** 走到这个节点时选的分支，用来标在箭头上 */
  branch: string
}

interface Row {
  id: string
  nodes: RowNode[]
  state: PathState
  note?: string
}

const BRANCH_TEXT: Record<string, string> = { yes: '是', no: '否', default: '默认' }

function nodeOf(nodeId: string) {
  return props.doc?.nodes.find((item) => item.id === nodeId)
}

function title(nodeId: string): string {
  return nodeOf(nodeId)?.title || nodeId
}

function time(value?: string | null): number {
  return value ? new Date(value).getTime() : 0
}

const runsByToken = computed(() => {
  const map = new Map<string, RunRecord[]>()
  for (const run of props.runs) {
    const list = map.get(run.tokenId) ?? []
    list.push(run)
    map.set(run.tokenId, list)
  }
  return map
})

const startId = computed(() => props.doc?.nodes.find((node) => node.kind === 'start')?.id)

/** 只算真正到访过的节点：没进过分支的 queued/abandoned 不算「点亮」 */
const visited = computed(() => props.tokens.filter((token) => Boolean(token.visitedAt)))

const byId = computed(() => new Map(props.tokens.map((token) => [token.id, token])))

const generation = computed(() => {
  const values = visited.value.map((token) => token.generation)
  return values.length ? Math.max(...values) : 0
})

const isSerial = computed(() => (props.mode ?? 'serial') === 'serial')

const visitedChildren = computed(() => {
  const map = new Map<string, Token[]>()
  for (const token of visited.value) {
    if (!token.parentTokenId) continue
    const list = map.get(token.parentTokenId) ?? []
    list.push(token)
    map.set(token.parentTokenId, list)
  }
  return map
})

/** 从开始节点一路爬到当前 token：回退会跨代，但父指针始终连着 */
function ancestry(token: Token): Token[] {
  const chain: Token[] = []
  const seen = new Set<string>()
  let cursor: Token | undefined = token
  while (cursor && !seen.has(cursor.id)) {
    seen.add(cursor.id)
    chain.unshift(cursor)
    cursor = cursor.parentTokenId ? byId.value.get(cursor.parentTokenId) : undefined
  }
  const startToken = chain.find((item) => item.nodeId === startId.value)
  return startToken && chain[0] !== startToken ? chain.slice(chain.indexOf(startToken)) : chain
}

/** 每条思路走到头的那次到访就是一个叶子；只保留最新一代，旧代次已被回退接管 */
const leaves = computed(() =>
  visited.value
    .filter((token) => token.generation === generation.value)
    .filter((token) => !(visitedChildren.value.get(token.id) ?? []).length)
    .sort((a, b) => time(a.visitedAt) - time(b.visitedAt) || (a.order ?? 0) - (b.order ?? 0)),
)

function stateOf(chain: Token[], leaf: Token): PathState {
  const latest = chain.filter((token) => token.generation === leaf.generation)
  const kinds = chain.map((token) => nodeOf(token.nodeId)?.kind)
  if (kinds.includes('problemSolved')) return { text: '已定位', type: 'success' }
  if (kinds.includes('escalation')) return { text: '已上升', type: 'warning' }
  if (latest.some((token) => token.status === 'waiting')) return { text: '待人工', type: 'warning' }
  if (latest.some((token) => token.status === 'running')) return { text: '排查中', type: 'primary' }
  const failed = latest.some((token) =>
    (runsByToken.value.get(token.id) ?? []).some((run) =>
      run.status === 'failed' && String(run.error ?? '').startsWith('route/')))
  if (failed) return { text: '排查失败', type: 'danger' }
  if (latest.every((token) => token.status === 'done')) return { text: '已完成', type: 'success' }
  if (latest.some((token) => token.status === 'failed')) return { text: '执行报错', type: 'danger' }
  return { text: '已停止', type: 'info' }
}

interface Built {
  id: string
  nodes: RowNode[]
  state: PathState
  leaf: Token
}

const built = computed<Built[]>(() =>
  leaves.value.map((leaf) => {
    const chain = ancestry(leaf)
    return {
      id: leaf.id,
      leaf,
      nodes: chain.map((token) => ({
        token,
        title: title(token.nodeId),
        branch: token.branch ? BRANCH_TEXT[token.branch] ?? token.branch : '',
      })),
      state: stateOf(chain, leaf),
    }
  }),
)

const rows = computed<Row[]>(() => {
  // 串行只保留正在排查的那条：换思路时上一条已经作废，不占版面，只留一句说明
  const shown = isSerial.value ? built.value.slice(-1) : built.value
  return shown.map((row) => {
    const previous = isSerial.value ? built.value.slice(0, built.value.indexOf(row)) : []
    const allFailed = previous.length > 0 && previous.every((item) =>
      item.state.text === '排查失败' || item.state.text === '执行报错')
    return {
      id: row.id,
      nodes: row.nodes,
      state: row.state,
      note: !previous.length ? undefined
        : allFailed ? `此前 ${previous.length} 条思路未走通，已换到这条继续排查`
          : `此前 ${previous.length} 条思路已结束，这里只留最后在排查的一条`,
    }
  })
})

</script>

<template>
  <div class="path-list">
    <el-empty v-if="!rows.length" description="还没有排查思路" :image-size="60" />
    <template v-else>
      <div v-for="row in rows" :key="row.id" class="path-row">
        <div class="path-head">
          <el-tag size="small" :type="row.state.type" effect="plain">{{ row.state.text }}</el-tag>
          <span class="meta">{{ isSerial ? '串行排查' : '并行思路' }}</span>
        </div>

        <div class="chain">
          <template v-for="(node, index) in row.nodes" :key="node.token.id">
            <span class="node" :class="node.token.status">{{ node.title }}</span>
            <span v-if="index < row.nodes.length - 1" class="arrow">
              <span v-if="row.nodes[index + 1].branch" class="branch">
                {{ row.nodes[index + 1].branch }}
              </span>
              →
            </span>
          </template>
        </div>

        <div v-if="row.note" class="note">{{ row.note }}</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.path-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.path-row {
  padding: 8px 10px;
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

.meta {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.chain {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  font-size: 12px;
  line-height: 1.8;
}

.node {
  padding: 0 6px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
}

.node.running,
.node.waiting {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.node.failed {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.arrow {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: var(--el-text-color-placeholder);
}

.branch {
  font-size: 10px;
  padding: 0 3px;
  border-radius: 2px;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
}

.note {
  font-size: 11px;
  color: var(--el-color-warning);
}
</style>
