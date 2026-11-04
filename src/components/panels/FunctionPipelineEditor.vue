<script setup lang="ts">
import { reactive } from 'vue'
import type {
  FunctionPipeline, ManualField, NodeBinding, StageArgument, StageName,
} from '@/domain/types'
import type { ProjectFunction, ProjectFunctionParam } from '@/services/projects'
import { isScalarParam } from '@/services/paramTypes'
import BindingEditor from '@/components/panels/BindingEditor.vue'

const props = defineProps<{ value: FunctionPipeline; readonly?: boolean }>()
const emit = defineEmits<{ (e: 'update', value: FunctionPipeline): void }>()
const descriptions = reactive<Partial<Record<StageName, ProjectFunction>>>({})
const errors = reactive<Record<string, string>>({})
const stages: { name: StageName; title: string; hint: string }[] = [
  { name: 'acquire', title: '获取数据', hint: '可选；留空时由人工提供数据。参数在开始节点配置。' },
  { name: 'parse', title: '解析数据', hint: '必选；默认把原始数据传给第一个参数。' },
  { name: 'present', title: '展示数据', hint: '必选；默认接收解析结果，判断前后各调用一次。' },
  { name: 'judge', title: '判断结果', hint: '可选；留空时展示数据并等待人工判断。' },
]
const sources = [
  { value: 'raw', label: '原始数据' }, { value: 'parsed', label: '解析结果' },
  { value: 'decision', label: '判断结果' }, { value: 'inputs', label: '本次启动输入' },
  { value: 'lit', label: '固定值' },
]

function select(stage: StageName, binding?: NodeBinding): void {
  const next = { ...props.value }
  if (binding) next[stage] = { project: binding.project, function: binding.function }
  else delete next[stage]
  delete descriptions[stage]
  for (const name of Object.keys(errors)) {
    if (name.startsWith(`${stage}:`)) delete errors[name]
  }
  emit('update', next)
}

function describe(stage: StageName, value: ProjectFunction | null): void {
  if (value) descriptions[stage] = value
  else delete descriptions[stage]
}

function argument(stage: StageName, name: string): StageArgument | undefined {
  return props.value[stage]?.arguments?.[name]
}

function source(stage: StageName, name: string): string {
  const expression = argument(stage, name)
  if (!expression) return 'auto'
  if ('lit' in expression) return 'lit'
  if ('omit' in expression) return 'omit'
  return expression.source
}

// judge 没有「自动」兜底：不显式选来源时保持未配置，避免默认把整个解析结果传进去
function unset(stage: StageName, name: string): boolean {
  return stage === 'judge' && !argument(stage, name)
}

// 显式选了来源却留空路径，等于把整个数据传给参数；判断函数通常不接受对象，只提示不拦截
function missingPath(stage: StageName, name: string): boolean {
  if (stage !== 'judge') return false
  const expression = argument(stage, name)
  return !!expression && 'source' in expression && !expression.path?.trim()
}

// 后端编译期会拒绝「judge 标量参数没填字段路径」，前端同步标必填，避免发布时才报错
function pathRequired(stage: StageName, param: ProjectFunctionParam): boolean {
  if (stage !== 'judge' || !isScalarParam(param)) return false
  const expression = argument(stage, param.name)
  // 未配置的可选参数可省略、后端不要求路径；但 index 0 会被缺省映射，不选来源等于传整段
  if (!expression) {
    if (param.required !== false) return true
    return (descriptions[stage]?.params.indexOf(param) ?? -1) === 0
  }
  return 'source' in expression
}

function setArgument(stage: StageName, name: string, expression?: StageArgument): void {
  const binding = props.value[stage]
  if (!binding) return
  const args = { ...binding.arguments }
  if (expression) args[name] = expression
  else delete args[name]
  emit('update', { ...props.value, [stage]: { ...binding, arguments: args } })
}

function changeSource(stage: StageName, name: string, selected: string): void {
  delete errors[`${stage}:${name}`]
  if (selected === 'auto') setArgument(stage, name)
  else if (selected === 'lit') setArgument(stage, name, { lit: null })
  else if (selected === 'omit') setArgument(stage, name, { omit: true })
  else setArgument(stage, name, { source: selected as 'raw' | 'parsed' | 'decision' | 'inputs' })
}

function path(stage: StageName, name: string): string {
  const expression = argument(stage, name)
  return expression && 'source' in expression ? expression.path ?? '' : ''
}

function changePath(stage: StageName, name: string, value: string): void {
  const expression = argument(stage, name)
  if (expression && 'source' in expression) setArgument(stage, name, { ...expression, path: value })
}

function literal(stage: StageName, name: string): string {
  const expression = argument(stage, name)
  return expression && 'lit' in expression ? JSON.stringify(expression.lit) : ''
}

function changeLiteral(stage: StageName, name: string, value: string): void {
  try {
    const parsed: unknown = JSON.parse(value)
    setArgument(stage, name, { lit: parsed })
    delete errors[`${stage}:${name}`]
  } catch {
    errors[`${stage}:${name}`] = '请输入合法 JSON，例如 210、true 或 "文本"；尚未保存此修改'
  }
}

function allowed(stage: StageName) {
  // raw 只在解析阶段可用：人工判断恢复只重跑展示，那时上下文里已经没有原始数据
  const usable: Record<StageName, string[]> = {
    acquire: [],
    parse: ['raw', 'inputs', 'lit'],
    present: ['parsed', 'decision', 'inputs', 'lit'],
    judge: ['parsed', 'inputs', 'lit'],
  }
  return sources.filter((item) => usable[stage].includes(item.value))
}

function updateField(index: number, patch: Partial<ManualField>): void {
  const fields = props.value.manualFields.map((field, i) => i === index ? { ...field, ...patch } : field)
  emit('update', { ...props.value, manualFields: fields })
}

function addField(): void {
  emit('update', { ...props.value, manualFields: [
    ...props.value.manualFields, { name: '', type: 'text', required: true },
  ] })
}

function removeField(index: number): void {
  emit('update', { ...props.value,
    manualFields: props.value.manualFields.filter((_, i) => i !== index) })
}
</script>

<template>
  <div class="pipeline-editor">
    <section v-for="stage in stages" :key="stage.name" class="stage">
      <strong>{{ stage.title }}</strong>
      <p class="hint">{{ stage.hint }}</p>
      <p v-if="stage.name === 'judge'" class="hint judge-tip">
        判断函数需要的是「解析结果」里的某个字段（例如 voltage），不是整个解析结果对象。
        数值、文本等标量参数必须填写字段路径，留空会在发布时被拒绝。
      </p>
      <BindingEditor
        :binding="value[stage.name]" :readonly="readonly" plain-only
        @update="select(stage.name, $event)" @describe="describe(stage.name, $event)"
      />
      <template v-if="stage.name !== 'acquire' && value[stage.name]">
        <div v-for="param in descriptions[stage.name]?.params" :key="param.name" class="argument">
          <label :for="`source-${stage.name}-${param.name}`">
            {{ param.name }} {{ param.required ? '（必填）' : '（可选）' }}
            <span v-if="pathRequired(stage.name, param)" class="required">需字段路径</span>
          </label>
          <el-select
            :id="`source-${stage.name}-${param.name}`" :disabled="readonly"
            :model-value="unset(stage.name, param.name) ? undefined : source(stage.name, param.name)"
            :placeholder="stage.name === 'judge' ? '请选择来源并填写字段路径' : '自动连接'"
            @change="changeSource(stage.name, param.name, String($event))"
          >
            <el-option v-if="stage.name !== 'judge'" label="自动连接" value="auto" />
            <el-option v-if="!param.required" label="使用函数默认值" value="omit" />
            <el-option v-for="item in allowed(stage.name)" :key="item.value" v-bind="item" />
          </el-select>
          <el-input
            v-if="source(stage.name, param.name) === 'lit'" :disabled="readonly"
            :model-value="literal(stage.name, param.name)" aria-label="固定参数值"
            @change="changeLiteral(stage.name, param.name, String($event))"
          />
          <el-input
            v-else-if="!unset(stage.name, param.name)
              && !['auto', 'omit'].includes(source(stage.name, param.name))"
            :disabled="readonly" :model-value="path(stage.name, param.name)"
            :placeholder="stage.name === 'judge'
              ? '字段路径，例如 voltage' : '字段路径，留空传入整个数据'"
            :aria-required="pathRequired(stage.name, param)"
            aria-label="来源字段路径"
            @update:model-value="changePath(stage.name, param.name, $event)"
          />
          <p v-if="!readonly && pathRequired(stage.name, param)" class="required-hint">
            判断函数只接受具体字段值：请选择来源并填写字段路径，留空会在发布时被拒绝。
          </p>
          <p v-else-if="!readonly && missingPath(stage.name, param.name)" class="warning">
            未填写字段路径，将把整个数据传给该参数；判断函数通常需要具体字段，请补全。
          </p>
          <p v-if="errors[`${stage.name}:${param.name}`]" class="error" role="alert">
            {{ errors[`${stage.name}:${param.name}`] }}
          </p>
        </div>
      </template>
    </section>
    <section v-if="!value.acquire" class="stage">
      <strong>人工数据字段</strong>
      <p class="hint">定义执行人员需要填写或上传的数据，解析函数接收以字段名为键的对象。</p>
      <div v-for="(field, index) in value.manualFields" :key="index" class="manual-field">
        <label :for="`manual-name-${index}`">字段名</label>
        <el-input
          :id="`manual-name-${index}`" :model-value="field.name" :disabled="readonly"
          @update:model-value="updateField(index, { name: $event })"
        />
        <label :for="`manual-label-${index}`">显示名称</label>
        <el-input
          :id="`manual-label-${index}`" :model-value="field.label" :disabled="readonly"
          @update:model-value="updateField(index, { label: $event })"
        />
        <label :for="`manual-type-${index}`">数据类型</label>
        <el-select
          :id="`manual-type-${index}`" :model-value="field.type ?? 'text'" :disabled="readonly"
          @change="updateField(index, { type: $event as ManualField['type'] })"
        >
          <el-option label="文本" value="text" /><el-option label="数字" value="number" />
          <el-option label="是／否" value="boolean" /><el-option label="时间" value="datetime" />
          <el-option label="JSON 数据" value="json" /><el-option label="文件" value="file" />
        </el-select>
        <el-checkbox
          :model-value="field.required !== false" :disabled="readonly"
          @update:model-value="updateField(index, { required: Boolean($event) })"
        >必填</el-checkbox>
        <el-button v-if="!readonly" text type="danger" @click="removeField(index)">删除字段</el-button>
      </div>
      <el-button v-if="!readonly" @click="addField">添加人工数据字段</el-button>
    </section>
  </div>
</template>

<style scoped>
.pipeline-editor { display: grid; gap: 14px; }
.stage { display: grid; gap: 8px; padding: 12px 0; border-top: 1px solid var(--el-border-color); }
.argument, .manual-field { display: grid; gap: 6px; padding-top: 8px; min-width: 0; }
.manual-field { padding: 10px; background: var(--el-fill-color-light); border-radius: 4px; }
.hint { margin: 0; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.judge-tip { color: var(--el-color-warning-dark-2); }
.required { color: var(--el-color-danger); font-weight: 600; }
.required-hint { margin: 0; font-size: 12px; color: var(--el-color-danger); }
.warning { margin: 0; font-size: 12px; color: var(--el-color-warning); }
label { font-size: 12px; }
.error { margin: 0; font-size: 12px; color: var(--el-color-danger); }
</style>
