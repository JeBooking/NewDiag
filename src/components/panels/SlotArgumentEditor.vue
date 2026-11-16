<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { SlotArgument } from '@/domain/types'

const props = defineProps<{
  value?: SlotArgument
  inputs?: string[]
  parameters?: string[]
  previous?: boolean
  readonly?: boolean
}>()
const emit = defineEmits<{ (e: 'update', value: SlotArgument): void }>()

const failure = ref('')
const mode = computed(() => {
  const value = props.value
  if (!value) return 'lit'
  if ('lit' in value) return 'lit'
  if ('items' in value) return 'items'
  if ('fields' in value) return 'fields'
  return value.source
})
const name = computed(() => (props.value && 'name' in props.value ? props.value.name : ''))
const path = computed(() => (props.value && 'path' in props.value ? props.value.path ?? '' : ''))
const literal = computed(() => (props.value && 'lit' in props.value ? JSON.stringify(props.value.lit) : 'null'))
const draft = ref('')
watch(literal, (value) => {
  try {
    if (JSON.stringify(JSON.parse(draft.value)) === value) return
  } catch { /* 编辑中的 JSON 可能暂时不完整 */ }
  draft.value = value ?? 'null'
  failure.value = ''
}, { immediate: true })

const elements = computed(() => (props.value && 'items' in props.value ? props.value.items : []))
const members = computed(() => (props.value && 'fields' in props.value ? props.value.fields : {}))
const fieldName = ref('')

const sources = computed(() => [
  { value: 'lit', label: '固定值', disabled: false },
  { value: 'input', label: '启动输入', disabled: false },
  { value: 'parameter', label: '现场填写', disabled: false },
  // 没有上游槽时也列出来，但禁用：已配置的旧值能正常显示，也能看出为什么不能选
  {
    value: 'previous',
    label: props.previous ? '上一槽结果' : '上一槽结果（本节点需先有拉取或处理）',
    disabled: !props.previous,
  },
  { value: 'items', label: '列表', disabled: false },
  { value: 'fields', label: '字典', disabled: false },
])

const candidates = computed(() => {
  if (mode.value === 'input') return props.inputs ?? []
  if (mode.value === 'parameter') return props.parameters ?? []
  return []
})

function changeMode(value: string): void {
  failure.value = ''
  if (value === 'lit') emit('update', { lit: null })
  else if (value === 'items') emit('update', { items: [] })
  else if (value === 'fields') emit('update', { fields: {} })
  else if (value === 'previous') emit('update', { source: 'previous' })
  else emit('update', { source: value as 'input' | 'parameter', name: '' })
}
function patch(value: { name?: string; path?: string }): void {
  if (props.value && 'source' in props.value) emit('update', { ...props.value, ...value })
}
function changeLiteral(text: string): void {
  draft.value = text
  try {
    emit('update', { lit: JSON.parse(text) })
    failure.value = ''
  } catch {
    failure.value = '请输入合法 JSON：数字、true、"文本"、数组或对象；当前修改尚未保存'
  }
}
function addField(name: string): void {
  const key = name.trim()
  if (!key || key in members.value) return
  emit('update', { fields: { ...members.value, [key]: { lit: null } } })
}
</script>

<template>
  <div class="argument">
    <el-select :model-value="mode" :disabled="readonly" size="small" aria-label="参数来源" @change="changeMode">
      <el-option v-for="item in sources" :key="item.value" :label="item.label" :value="item.value"
        :disabled="item.disabled" />
    </el-select>
    <template v-if="mode === 'input' || mode === 'parameter'">
      <el-select :model-value="name" :disabled="readonly" size="small" filterable allow-create default-first-option
        :placeholder="mode === 'input' ? '启动参数名' : '现场参数名'" @change="patch({ name: String($event) })">
        <el-option v-for="item in candidates" :key="item" :label="item" :value="item" />
      </el-select>
      <el-input :model-value="path" :disabled="readonly" size="small"
        placeholder="字段路径，留空用完整值" @update:model-value="patch({ path: $event })" />
    </template>
    <el-input v-if="mode === 'lit'" :model-value="draft" :disabled="readonly" size="small"
      placeholder='例如 12、"vibration"、{"peak":12}' @update:model-value="changeLiteral" />
    <p v-if="failure" role="alert" class="error">{{ failure }}</p>
    <template v-if="mode === 'items'">
      <div v-for="(element, index) in elements" :key="index" class="nested">
        <SlotArgumentEditor :value="element" :inputs="inputs" :parameters="parameters" :previous="previous"
          :readonly="readonly" @update="emit('update', { items: elements.map((item, at) => at === index ? $event : item) })" />
        <el-button v-if="!readonly" link size="small" @click="emit('update', { items: elements.filter((_, at) => at !== index) })">移除</el-button>
      </div>
      <el-button v-if="!readonly" size="small" @click="emit('update', { items: [...elements, { lit: null }] })">添加一项</el-button>
    </template>
    <template v-if="mode === 'fields'">
      <div v-for="(member, key) in members" :key="key" class="nested">
        <label>{{ key }}</label>
        <SlotArgumentEditor :value="member" :inputs="inputs" :parameters="parameters" :previous="previous"
          :readonly="readonly" @update="emit('update', { fields: { ...members, [key]: $event } })" />
        <el-button v-if="!readonly" link size="small" @click="emit('update', { fields: Object.fromEntries(Object.entries(members).filter(([name]) => name !== key)) })">移除</el-button>
      </div>
      <div v-if="!readonly" class="row">
        <el-input v-model="fieldName" size="small" placeholder="字段名" @keyup.enter="addField(fieldName); fieldName = ''" />
        <el-button size="small" @click="addField(fieldName); fieldName = ''">添加</el-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.argument { display: grid; gap: 6px; min-width: 0; }
.nested { border-left: 2px solid var(--el-border-color); padding-left: 8px; display: grid; gap: 4px; }
.row { display: flex; gap: 6px; }
.error { color: var(--el-color-danger); font-size: 12px; margin: 0; }
</style>
