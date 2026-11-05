<script setup lang="ts">
import { computed, ref } from 'vue'
import { KIND_META } from '@/domain/kinds'
import type {
  ArgExpr,
  FlowDoc,
  Plan,
  PlanStep,
  StageArgument,
  StageFunction,
  StageName,
} from '@/domain/types'

const props = defineProps<{
  plan: Plan
  /** 只用来把 nodeId 换成节点标题，缺了也不影响计划本身的展示 */
  doc: FlowDoc | null
}>()

const STAGE_TITLES: Record<StageName, string> = {
  acquire: '获取',
  parse: '解析',
  present: '展示',
  judge: '判断',
}

const SOURCE_TITLES: Record<string, string> = {
  raw: '原始数据',
  parsed: '解析结果',
  decision: '判断结果',
  inputs: '启动输入',
}

interface StageRow {
  key: string
  label: string
  target: string
  args: { name: string; text: string }[]
}

interface StepRow {
  id: string
  depth: number
  title: string
  kindLabel: string
  color: string
  chips: string[]
  stages: StageRow[]
  call?: { target: string; version?: string; args: { name: string; text: string }[] }
  pipelineArgs: { name: string; text: string }[]
  diagnosis?: string
  human: boolean
  terminal: boolean
  expandable: boolean
}

const expanded = ref<string[]>([])

function briefLiteral(value: unknown): string {
  if (typeof value === 'string') {
    return value.length > 24 ? `"${value.slice(0, 24)}…"` : `"${value}"`
  }
  if (value === null) return 'null'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    const text = JSON.stringify(value)
    return text && text.length > 24 ? `${text.slice(0, 24)}…` : (text ?? String(value))
  } catch {
    return String(value)
  }
}

function exprText(expr: ArgExpr): string {
  if ('lit' in expr) return `常量 ${briefLiteral(expr.lit)}`
  if ('snapshot' in expr) return `快照 ${expr.snapshot}${expr.optional ? '（可缺省）' : ''}`
  if ('run' in expr) return `${expr.run}.${expr.path}${expr.optional ? '（可缺省）' : ''}`
  return '—'
}

function stageArgText(arg: StageArgument): string {
  if ('omit' in arg) return '省略该参数'
  if ('lit' in arg) return `常量 ${briefLiteral(arg.lit)}`
  const source = SOURCE_TITLES[arg.source] ?? arg.source
  return arg.path ? `${source}.${arg.path}` : source
}

function mapArgs(args: Record<string, ArgExpr> | undefined): { name: string; text: string }[] {
  return Object.entries(args ?? {}).map(([name, expr]) => ({ name, text: exprText(expr) }))
}

function bindingRow(key: string, binding: StageFunction): StageRow {
  return {
    key,
    label: STAGE_TITLES[key as StageName] ?? key,
    target: `${binding.project}.${binding.function}`,
    args: Object.entries(binding.arguments ?? {}).map(([name, arg]) => ({
      name,
      text: stageArgText(arg),
    })),
  }
}

function stageRows(step: PlanStep): StageRow[] {
  const pipeline = step.pipeline
  if (!pipeline) return []
  const rows: StageRow[] = []
  for (const key of Object.keys(STAGE_TITLES)) {
    const binding = pipeline[key as StageName]
    if (binding) rows.push(bindingRow(key, binding))
  }
  return rows
}

const nodeTitles = computed(() => {
  const map = new Map<string, string>()
  for (const node of props.doc?.nodes ?? []) map.set(node.id, node.title)
  return map
})

function buildRow(id: string, depth: number): StepRow {
  const step = props.plan.steps[id]
  const meta = KIND_META[step.kind]
  const stages = stageRows(step)
  const pipelineArgs = mapArgs(step.pipeline?.args)
  const call = step.call
    ? {
        target: `${step.call.project}.${step.call.function}`,
        version: step.call.version,
        args: mapArgs(step.call.args),
      }
    : undefined
  const diagnosis = step.diagnosisMethod
    ? `${step.diagnosisMethod.name}（${mapArgs(step.diagnosisMethod.args)
        .map((arg) => `${arg.name} ← ${arg.text}`)
        .join('，') || '无参数'}）`
    : undefined
  const chips = stages.map((stage) => stage.label)
  const title = nodeTitles.value.get(id) || step.text || id
  const expandable =
    stages.length > 0 || Boolean(call) || pipelineArgs.length > 0 || Boolean(diagnosis)
  return {
    id,
    depth,
    title,
    kindLabel: meta?.label ?? step.kind,
    color: meta?.color ?? 'var(--el-text-color-secondary)',
    chips,
    stages,
    call,
    pipelineArgs,
    diagnosis,
    human: step.human === true,
    terminal: step.terminal === true,
    expandable,
  }
}

/** 从入口沿 next 广度优先排；环上的重复访问跳过，游离步骤补在末尾，不漏展示 */
const rows = computed<StepRow[]>(() => {
  const steps = props.plan.steps ?? {}
  const order: { id: string; depth: number }[] = []
  const seen = new Set<string>()
  const queue: { id: string; depth: number }[] = [{ id: props.plan.entry, depth: 0 }]
  while (queue.length) {
    const item = queue.shift()
    if (!item || seen.has(item.id) || !steps[item.id]) continue
    seen.add(item.id)
    order.push(item)
    const next = steps[item.id].next ?? {}
    for (const targets of Object.values(next)) {
      for (const target of targets) queue.push({ id: target, depth: Math.min(item.depth + 1, 6) })
    }
  }
  for (const id of Object.keys(steps)) if (!seen.has(id)) order.push({ id, depth: 0 })
  return order.map((item) => buildRow(item.id, item.depth))
})

function toggle(id: string): void {
  const index = expanded.value.indexOf(id)
  if (index >= 0) expanded.value.splice(index, 1)
  else expanded.value.push(id)
}

function isExpanded(id: string): boolean {
  return expanded.value.includes(id)
}
</script>

<template>
  <div class="plan">
    <div class="plan-meta">
      <span>入口 {{ nodeTitles.get(plan.entry) || plan.entry }}</span>
      <span>{{ plan.mode === 'serial' ? '串行' : '并行' }}</span>
      <span>{{ rows.length }} 个步骤</span>
    </div>

    <div class="steps">
      <div
        v-for="row in rows"
        :key="row.id"
        class="step"
        :style="{ paddingLeft: `${row.depth * 12}px` }"
      >
        <button
          type="button"
          class="step-head"
          :class="{ 'is-expandable': row.expandable }"
          :aria-expanded="row.expandable ? isExpanded(row.id) : undefined"
          @click="row.expandable && toggle(row.id)"
        >
          <span class="caret" :class="{ open: row.expandable && isExpanded(row.id) }">
            {{ row.expandable ? '▸' : '·' }}
          </span>
          <span class="kind" :style="{ color: row.color }">{{ row.kindLabel }}</span>
          <span class="title">{{ row.title }}</span>
          <span v-if="row.human" class="chip">人工</span>
          <span v-if="row.terminal" class="chip">终止</span>
          <span class="node-id">{{ row.id }}</span>
          <span v-if="row.chips.length" class="chips">{{ row.chips.join(' → ') }}</span>
        </button>

        <div v-if="row.expandable && isExpanded(row.id)" class="step-body">
          <div v-if="row.call" class="line">
            <span class="line-label">函数</span>
            <span class="mono">{{ row.call.target }}</span>
            <span v-if="row.call.version" class="muted">{{ row.call.version }}</span>
          </div>
          <div v-if="row.diagnosis" class="line">
            <span class="line-label">诊断方法</span>
            <span class="mono">{{ row.diagnosis }}</span>
          </div>
          <div v-if="row.pipelineArgs.length" class="line">
            <span class="line-label">节点入参</span>
            <span class="arg-list">
              <span v-for="arg in row.pipelineArgs" :key="arg.name" class="arg">
                {{ arg.name }} ← {{ arg.text }}
              </span>
            </span>
          </div>

          <div v-for="stage in row.stages" :key="stage.key" class="stage">
            <div class="line">
              <span class="line-label">{{ stage.label }}</span>
              <span class="mono">{{ stage.target }}</span>
            </div>
            <div v-if="stage.args.length" class="arg-list is-indent">
              <span v-for="arg in stage.args" :key="arg.name" class="arg">
                {{ arg.name }} ← {{ arg.text }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.plan {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.plan-meta {
  display: flex;
  gap: 14px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.steps {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.step {
  display: flex;
  flex-direction: column;
}

.step-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  width: 100%;
  padding: 4px 6px;
  border: none;
  border-radius: 4px;
  background: none;
  font: inherit;
  font-size: 12px;
  text-align: left;
  color: var(--el-text-color-primary);
}

.step-head.is-expandable {
  cursor: pointer;
}

.step-head.is-expandable:hover {
  background: var(--el-fill-color-light);
}

.caret {
  flex: none;
  width: 10px;
  font-size: 10px;
  color: var(--el-text-color-placeholder);
  transition: transform 0.15s;
}

.caret.open {
  transform: rotate(90deg);
}

.kind {
  flex: none;
  font-size: 11px;
}

.title {
  overflow: hidden;
  font-weight: 500;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.chip {
  flex: none;
  padding: 0 4px;
  border-radius: 3px;
  background: var(--el-fill-color);
  font-size: 10px;
  color: var(--el-text-color-secondary);
}

.node-id {
  flex: none;
  font-family: var(--el-font-family-mono, monospace);
  font-size: 10px;
  color: var(--el-text-color-placeholder);
}

.chips {
  margin-left: auto;
  flex: none;
  font-size: 10px;
  color: var(--el-text-color-secondary);
}

.step-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 2px 0 6px 16px;
  padding: 6px 10px;
  border-left: 2px solid var(--el-border-color-lighter);
}

.line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 11px;
  line-height: 1.6;
}

.line-label {
  flex: none;
  min-width: 44px;
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: var(--el-font-family-mono, monospace);
  word-break: break-all;
}

.muted {
  color: var(--el-text-color-placeholder);
}

.arg-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
}

.arg-list.is-indent {
  margin-left: 52px;
}

.arg {
  font-size: 11px;
  color: var(--el-text-color-regular);
  word-break: break-all;
}

.stage {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
</style>
