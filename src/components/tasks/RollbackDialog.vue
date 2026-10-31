<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { FlowDoc, InstanceDetail, Plan } from '@/domain/types'
import { KIND_META } from '@/domain/kinds'
import { errorText } from '@/services/api'
import { getRelease } from '@/services/flows'
import { useInstancesStore } from '@/stores/instances'

const props = defineProps<{
  modelValue: boolean
  detail: InstanceDetail | null
  doc: FlowDoc | null
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'submitted'): void
}>()

const store = useInstancesStore()

const plan = ref<Plan | null>(null)
const planReleaseId = ref<string | null>(null)
const loadingPlan = ref(false)
const submitting = ref(false)
const target = ref('')
const branch = ref('')
const reason = ref('')

const currentGeneration = computed(() => {
  const tokens = props.detail?.tokens ?? []
  return tokens.reduce((max, token) => Math.max(max, token.generation), 1)
})

interface Candidate {
  nodeId: string
  at: string
}

/** 该节点是否有后继；终止节点没有出边，不能作为回退目标 */
function hasOutgoing(nodeId: string): boolean {
  const step = plan.value?.steps[nodeId]
  if (step) {
    return Object.values(step.next ?? {}).some((list) => Array.isArray(list) && list.length > 0)
  }
  return (props.doc?.edges ?? []).some((edge) => edge.from === nodeId)
}

const candidates = computed<Candidate[]>(() => {
  const runs = props.detail?.runs ?? []
  const firstRunAt = new Map<string, string>()
  const okRunNodes = new Set<string>()
  for (const run of runs) {
    const current = firstRunAt.get(run.tokenId)
    if (!current || run.startedAt < current) firstRunAt.set(run.tokenId, run.startedAt)
    if (run.status === 'ok') okRunNodes.add(run.nodeId)
  }

  // 回退是「回到已完成的节点改选分支」，不是「重做当前步」，等人工的节点不算目标
  const pendingNodes = new Set(
    (props.detail?.reviews ?? [])
      .filter((review) => review.status === 'pending')
      .map((review) => review.nodeId),
  )

  const byNode = new Map<string, Candidate>()
  for (const token of props.detail?.tokens ?? []) {
    if (token.generation > currentGeneration.value) continue
    if (token.status !== 'done') continue
    if (pendingNodes.has(token.nodeId)) continue
    if (!hasOutgoing(token.nodeId)) continue
    // 执行过代码的节点必须有 ok 的 run，否则引擎会拒绝
    if (plan.value?.steps[token.nodeId]?.call && !okRunNodes.has(token.nodeId)) continue

    const candidate: Candidate = {
      nodeId: token.nodeId,
      at: firstRunAt.get(token.id) ?? '',
    }
    const current = byNode.get(token.nodeId)
    if (!current || candidate.at < current.at) byNode.set(token.nodeId, candidate)
  }
  return [...byNode.values()].sort((a, b) => {
    if (a.at && b.at) return a.at.localeCompare(b.at)
    if (a.at) return -1
    if (b.at) return 1
    return 0
  })
})

const branches = computed(() => {
  if (!target.value || !plan.value) return []
  const step = plan.value.steps[target.value]
  return Object.keys(step?.next ?? {})
})

function nodeLabel(nodeId: string): string {
  const node = props.doc?.nodes.find((item) => item.id === nodeId)
  const step = plan.value?.steps[nodeId]
  const kind = node ? KIND_META[node.kind].label : step ? KIND_META[step.kind].label : ''
  const title = node?.title || nodeId
  return kind ? `${title}（${kind}）` : title
}

async function ensurePlan(): Promise<void> {
  const releaseId = props.detail?.releaseId
  if (!releaseId) return
  if (plan.value && planReleaseId.value === releaseId) return
  loadingPlan.value = true
  try {
    const release = await getRelease(releaseId)
    plan.value = release.plan ?? null
    planReleaseId.value = releaseId
  } catch (error) {
    ElMessage.error(errorText(error, '读取发布计划失败'))
  } finally {
    loadingPlan.value = false
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) return
    target.value = ''
    branch.value = ''
    reason.value = ''
    void ensurePlan()
  },
)

watch(target, () => {
  const list = branches.value
  branch.value = list.length > 0 ? list[0] : ''
})

// 计划是异步读到的，先选目标后到分支时要补上默认值
watch(branches, (list) => {
  if (!list.includes(branch.value)) branch.value = list[0] ?? ''
})

function close(): void {
  emit('update:modelValue', false)
}

async function submit(): Promise<void> {
  if (!props.detail || !target.value || !branch.value) return
  submitting.value = true
  try {
    await store.rollback({
      targetNodeId: target.value,
      branch: branch.value,
      reason: reason.value.trim() || undefined,
    })
    ElMessage.success('已回退到所选节点')
    emit('submitted')
    close()
  } catch (error) {
    ElMessage.error(errorText(error, '回退失败'))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="回退到已执行节点"
    width="520px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="平台不会撤销任何设备操作，回退只影响系统内的执行记录"
      description="回退后本次之后产生的路径与运行记录标记为已放弃并保留，按所选分支重新推进，代数加一。"
    />

    <div class="form">
      <label class="label">目标节点</label>
      <el-select v-model="target" placeholder="选择已执行过的节点" class="field">
        <el-option
          v-for="run in candidates"
          :key="run.nodeId"
          :label="nodeLabel(run.nodeId)"
          :value="run.nodeId"
        />
      </el-select>
      <div v-if="!candidates.length && !loadingPlan" class="hint">
        当前没有可回退的节点：终止节点和正在等待处理的节点不能作为目标
      </div>

      <template v-if="target">
        <label class="label">重新走的分支</label>
        <el-select
          v-model="branch"
          placeholder="选择分支"
          class="field"
          :disabled="!branches.length"
        >
          <el-option v-for="item in branches" :key="item" :label="item" :value="item" />
        </el-select>
        <div v-if="!branches.length" class="hint">
          该节点没有后继分支，不能作为回退目标
        </div>
      </template>

      <label class="label">原因（可选）</label>
      <el-input v-model="reason" type="textarea" :rows="3" placeholder="记录回退原因" />

      <div v-if="loadingPlan" class="hint">正在读取发布计划…</div>
      <div v-else-if="!plan" class="hint">发布计划不可用，暂不能回退</div>
    </div>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button
        type="primary"
        :loading="submitting"
        :disabled="!target || !branch"
        @click="submit"
      >
        确认回退
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.form {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 12px;
}

.label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 6px;
}

.field {
  width: 100%;
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
