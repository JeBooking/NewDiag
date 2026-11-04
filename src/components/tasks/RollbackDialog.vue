<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { FlowDoc, InstanceDetail, Plan, Token, TokenStatus } from '@/domain/types'
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
const mode = ref<'retry' | 'branch'>('retry')
const statusText: Record<TokenStatus, string> = {
  queued: '排队', running: '执行中', waiting: '待处理', done: '完成',
  failed: '失败', abandoned: '已放弃',
}

/** 该节点是否有后继；终止节点没有出边，不能作为回退目标 */
function hasOutgoing(nodeId: string): boolean {
  const step = plan.value?.steps[nodeId]
  if (step) {
    return Object.values(step.next ?? {}).some((list) => Array.isArray(list) && list.length > 0)
  }
  return (props.doc?.edges ?? []).some((edge) => edge.from === nodeId)
}

const candidates = computed(() => (props.detail?.tokens ?? [])
  .filter((token) => {
    const step = plan.value?.steps[token.nodeId]
    return token.visitedAt && step && step.kind !== 'start' && !step.terminal
  })
  .slice()
  .sort((a, b) => a.visitedAt!.localeCompare(b.visitedAt!)
    || (a.order ?? 0) - (b.order ?? 0) || a.id.localeCompare(b.id)))

const selected = computed(() => candidates.value.find((token) => token.id === target.value))
const canReuse = computed(() => {
  const token = selected.value
  if (!token || token.status === 'failed' || !hasOutgoing(token.nodeId)) return false
  const step = plan.value?.steps[token.nodeId]
  const runs = (props.detail?.runs ?? []).filter((run) => run.tokenId === token.id)
  if (step?.call || step?.pipeline) return runs.some((run) =>
    run.status === 'ok' || (run.status === 'abandoned' && !run.error
      && run.output !== undefined && run.output !== null))
  if (!step?.human) return true
  return token.status === 'done' || (props.detail?.reviews ?? []).some((review) =>
    review.tokenId === token.id && review.status === 'done'
    && ['yes', 'no', 'done'].includes(review.decision?.result ?? ''))
})

const branches = computed(() => {
  if (!target.value || !plan.value) return []
  const step = plan.value.steps[selected.value?.nodeId ?? '']
  return Object.entries(step?.next ?? {}).filter(([, next]) => next.length > 0).map(([key]) => key)
})

function visitLabel(token: Token, index: number): string {
  const time = token.visitedAt
    ? new Date(token.visitedAt).toLocaleString('zh-CN', { hour12: false }) : '时间未知'
  return `${index + 1}. ${nodeLabel(token.nodeId)} · 第 ${token.generation} 代 · ${statusText[token.status]} · ${time}`
}

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
    mode.value = 'retry'
    void ensurePlan()
  },
)

watch(target, () => {
  const list = branches.value
  branch.value = list.length > 0 ? list[0] : ''
})

watch(canReuse, (value) => {
  if (!value) mode.value = 'retry'
})

// 计划是异步读到的，先选目标后到分支时要补上默认值
watch(branches, (list) => {
  if (!list.includes(branch.value)) branch.value = list[0] ?? ''
})

function close(): void {
  emit('update:modelValue', false)
}

async function submit(): Promise<void> {
  if (!props.detail || !selected.value || !plan.value || submitting.value) return
  if (mode.value === 'branch' && (!branch.value || !canReuse.value)) return
  submitting.value = true
  try {
    await store.rollback({
      targetNodeId: selected.value.nodeId,
      targetTokenId: selected.value.id,
      mode: mode.value,
      branch: mode.value === 'branch' ? branch.value : undefined,
      reason: reason.value.trim() || undefined,
    })
    ElMessage.success('已从所选到访位置重新推进')
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
    title="从执行轨迹回退"
    width="min(720px, 95vw)"
    draggable
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="平台不会撤销任何设备操作，回退只影响系统内的执行记录"
      description="回退会取消本次诊断的全部未完成路径和待办，包括其他活跃分支。已有执行记录保留，从所选的具体到访位置开启新一代。"
    />

    <div class="form">
      <label class="label">目标到访记录</label>
      <el-select v-model="target" placeholder="选择执行轨迹中的一次到访" class="field" :disabled="loadingPlan || !plan">
        <el-option
          v-for="(token, index) in candidates"
          :key="token.id"
          :label="visitLabel(token, index)"
          :value="token.id"
        />
      </el-select>
      <div v-if="!candidates.length && !loadingPlan" class="hint">
        当前没有可回退的到访记录
      </div>

      <template v-if="target">
        <label class="label">回退方式</label>
        <el-radio-group v-model="mode">
          <el-radio value="retry">重新执行该节点</el-radio>
          <el-radio value="branch" :disabled="!canReuse">复用结果并改选分支</el-radio>
        </el-radio-group>
        <div v-if="!canReuse" class="hint">本次到访没有可复用的成功结果，需要重新执行。</div>
      </template>
      <template v-if="target && mode === 'branch'">
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
        :disabled="!selected || !plan || (mode === 'branch' && (!branch || !canReuse))"
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
