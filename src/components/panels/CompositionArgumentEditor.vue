<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Composition, CompositionArgument } from '@/domain/types'

const props = defineProps<{
  value?: CompositionArgument
  composition: Composition
  values?: string[]
  readonly?: boolean
}>()
const emit = defineEmits<{ (e: 'update', value: CompositionArgument): void }>()
const failure = ref('')
const mode = computed(() => {
  const value = props.value
  if (!value || 'lit' in value) return 'lit'
  if ('system' in value) return value.system
  return 'source' in value ? value.source : 'items' in value ? 'items' : 'fields'
})
const name = computed(() => props.value && 'source' in props.value ? props.value.name : '')
const path = computed(() => props.value && 'source' in props.value ? props.value.path ?? '' : '')
const literal = computed(() => props.value && 'lit' in props.value
  ? JSON.stringify(props.value.lit) : 'null')
const literalDraft = ref('')
watch(literal, (value) => {
  // 编辑中的 JSON 可能暂时不完整；父级回写相同值时保留用户的文本格式。
  try {
    if (JSON.stringify(JSON.parse(literalDraft.value)) === value) return
  } catch { /* 外部值改变时用新值替换未完成的文本。 */ }
  literalDraft.value = value ?? 'null'
  failure.value = ''
}, { immediate: true })
const candidates = computed(() => mode.value === 'object' ? props.composition.objects.map((item) => item.id)
  : mode.value === 'resource' ? props.composition.resources.map((item) => item.id) : props.values ?? [])
const parameters = computed(() => props.value && 'source' in props.value ? props.value.arguments ?? {} : {})
const parameterName = ref('')
const fieldName = ref('')
const elements = computed(() => props.value && 'items' in props.value ? props.value.items : [])
const members = computed(() => props.value && 'fields' in props.value ? props.value.fields : {})
function addField(): void {
  const name = fieldName.value.trim()
  if (!name || name in members.value) return
  emit('update', { fields: { ...members.value, [name]: { lit: null } } })
  fieldName.value = ''
}
function removeField(name: string): void {
  const fields = { ...members.value }
  delete fields[name]
  emit('update', { fields })
}

function changeMode(value: string): void {
  failure.value = ''
  if (value === 'lit') {
    literalDraft.value = 'null'
    emit('update', { lit: null })
  } else if (value === 'items') emit('update', { items: [] })
  else if (value === 'fields') emit('update', { fields: {} })
  else if (value === 'directory' || value === 'file') emit('update', { system: value })
  else emit('update', { source: value as 'input', name: '' })
}
function patch(value: { name?: string; path?: string; file?: boolean; arguments?: Record<string, CompositionArgument> }): void {
  if (props.value && 'source' in props.value) emit('update', { ...props.value, ...value })
}
function changeLiteral(value: string): void {
  literalDraft.value = value
  try {
    emit('update', { lit: JSON.parse(value) as unknown })
    failure.value = ''
  } catch { failure.value = '请输入合法 JSON：数字、true、"文本"、数组或对象；当前修改尚未保存' }
}
function addParameter(): void {
  const key = parameterName.value.trim()
  if (key) patch({ arguments: { ...parameters.value, [key]: { lit: null } } })
  parameterName.value = ''
}
</script>

<template>
  <div class="argument-editor">
    <el-select :model-value="mode" :disabled="readonly" aria-label="参数来源" @change="changeMode">
      <el-option label="固定值" value="lit" />
      <el-option label="启动输入" value="input" />
      <el-option label="操作／资源参数" value="parameter" />
      <el-option label="此前调用结果" value="value" />
      <el-option label="上游节点结果（节点 ID）" value="node" />
      <el-option label="实例对象" value="object" />
      <el-option label="共享资源" value="resource" />
      <el-option label="本次输出目录" value="directory" />
      <el-option label="本次图片输出路径" value="file" />
      <el-option label="组合列表" value="items" />
      <el-option label="组合字段" value="fields" />
    </el-select>
    <template v-if="!['lit', 'directory', 'file', 'items', 'fields'].includes(mode)">
      <el-select :model-value="name" filterable allow-create default-first-option
        :disabled="readonly" aria-label="来源名称" placeholder="选择或输入名称"
        @change="patch({ name: String($event) })">
        <el-option v-for="item in candidates" :key="item" :label="item" :value="item" />
      </el-select>
      <el-input :model-value="path" :disabled="readonly"
        aria-label="返回字段路径" placeholder="字段路径，留空使用完整结果"
        @update:model-value="patch({ path: $event })" />
      <el-checkbox v-if="mode !== 'object'" :disabled="readonly"
        :model-value="value && 'source' in value && value.file === true"
        @change="patch({ file: Boolean($event) })">将上传文件引用转为本地文件路径</el-checkbox>
    </template>
    <template v-if="mode === 'items'">
      <div v-for="(element, index) in elements" :key="index" class="nested">
        <label>第 {{ index + 1 }} 项</label>
        <CompositionArgumentEditor :value="element" :composition="composition" :values="values" :readonly="readonly"
          @update="emit('update', { items: elements.map((item, at) => at === index ? $event : item) })" />
        <el-button v-if="!readonly" link @click="emit('update', { items: elements.filter((_, at) => at !== index) })">移除此项</el-button>
      </div>
      <el-button v-if="!readonly" @click="emit('update', { items: [...elements, { lit: null }] })">添加一项</el-button>
    </template>
    <template v-if="mode === 'fields'">
      <div v-for="(member, key) in members" :key="key" class="nested">
        <label>{{ key }}</label>
        <CompositionArgumentEditor :value="member" :composition="composition" :values="values" :readonly="readonly"
          @update="emit('update', { fields: { ...members, [key]: $event } })" />
        <el-button v-if="!readonly" link @click="removeField(String(key))">移除字段</el-button>
      </div>
      <div v-if="!readonly" class="row">
        <el-input v-model="fieldName" aria-label="组合字段名称" placeholder="字段名称" />
        <el-button @click="addField">添加字段</el-button>
      </div>
    </template>
    <el-input v-if="mode === 'lit'" :model-value="literalDraft" :disabled="readonly"
      :aria-invalid="!!failure" aria-label="固定参数值"
      placeholder='例如 12、"vibration"、{"peak":12}' @update:model-value="changeLiteral" />
    <p v-if="failure" role="alert" class="error">{{ failure }}</p>
    <template v-if="mode === 'resource'">
      <div v-for="(argument, key) in parameters" :key="key" class="nested">
        <label>{{ key }}</label>
        <CompositionArgumentEditor :value="argument" :composition="composition" :values="values"
          :readonly="readonly" @update="patch({ arguments: { ...parameters, [key]: $event } })" />
      </div>
      <div v-if="!readonly" class="row">
        <el-input v-model="parameterName" aria-label="资源参数名称" placeholder="按需资源参数名称" />
        <el-button @click="addParameter">添加参数</el-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.argument-editor { display: grid; gap: 6px; min-width: 0; }
.row { display: flex; gap: 6px; }
.nested { border-left: 2px solid var(--el-border-color); padding-left: 8px; }
.error { color: var(--el-color-danger); font-size: 12px; margin: 0; }
</style>
