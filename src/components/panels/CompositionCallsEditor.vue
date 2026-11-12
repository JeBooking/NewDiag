<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import type { Composition, CompositionCall, CompositionArgument } from '@/domain/types'
import { listProjects, fetchProjectCatalog, type Project, type ProjectFunction } from '@/services/projects'
import { errorText } from '@/services/api'
import CompositionArgumentEditor from '@/components/panels/CompositionArgumentEditor.vue'

const props = defineProps<{ value: CompositionCall[]; composition: Composition; readonly?: boolean; objects?: boolean }>()
const emit = defineEmits<{ (e: 'update', value: CompositionCall[]): void }>()
const projects = ref<Project[]>([])
const catalogues = reactive<Record<string, ProjectFunction[]>>({})
const errors = reactive<Record<string, string>>({})
const loading = reactive<Record<string, boolean>>({})
const generations: Record<string, number> = {}
const extraNames = reactive<Record<number, string>>({})
function addExtra(index: number): void {
  const name = extraNames[index]?.trim()
  if (!name || name in props.value[index]!.arguments) return
  argument(index, name, { lit: null })
  extraNames[index] = ''
}
function extras(call: CompositionCall): string[] {
  const known = new Set(selected(call)?.params.map((item) => item.name) ?? [])
  return Object.keys(call.arguments).filter((name) => !known.has(name))
}
onMounted(async () => {
  try {
    projects.value = await listProjects()
    for (const call of props.value) void load(call)
  } catch (error) { errors.projects = errorText(error, '读取工程失败') }
})
function patch(index: number, patch: Partial<CompositionCall>): void {
  emit('update', props.value.map((item, position) => position === index ? { ...item, ...patch } : item))
}
function catalogKey(call: CompositionCall): string { return `${call.project}:${call.module ?? ''}` }
async function load(call: CompositionCall): Promise<void> {
  const project = projects.value.find((item) => item.name === call.project)
  if (!project) return
  const key = catalogKey(call)
  const generation = generations[key] = (generations[key] ?? 0) + 1
  loading[key] = true
  errors[key] = ''
  try {
    const items = await fetchProjectCatalog(project.id, call.module || project.module)
    if (generations[key] === generation) catalogues[key] = items
  } catch (error) {
    if (generations[key] === generation) errors[key] = errorText(error, '读取接口失败')
  } finally { if (generations[key] === generation) loading[key] = false }
}
function projectChanged(index: number, name: string): void {
  const project = projects.value.find((item) => item.name === name)
  const call = { ...props.value[index]!, project: name, module: project?.module ?? '', function: '', interface: '', arguments: {} }
  patch(index, call)
  void load(call)
}
function selected(call: CompositionCall): ProjectFunction | undefined {
  return catalogues[catalogKey(call)]?.find((item) => item.name === (call.interface || call.function))
}
function choose(index: number, name: string): void {
  const call = props.value[index]!
  patch(index, { interface: name, function: call.target ? name.split('.').at(-1)! : name, arguments: {} })
}
function argument(index: number, name: string, value?: CompositionArgument): void {
  const argumentsMap = { ...props.value[index]!.arguments }
  if (value) argumentsMap[name] = value
  else delete argumentsMap[name]
  patch(index, { arguments: argumentsMap })
}
function add(): void {
  let count = props.value.length + 1
  while (props.value.some((item) => item.id === `call${count}`)) count++
  emit('update', [...props.value, { id: `call${count}`, project: '', function: '', arguments: {}, output: props.objects ? 'object' : 'value' }])
}
</script>

<template>
  <div class="calls-editor">
    <p v-if="errors.projects" class="error" role="alert">{{ errors.projects }}</p>
    <el-collapse>
      <el-collapse-item v-for="(call, index) in value" :key="index" :name="index" :title="`${index + 1}. ${call.id || '未命名调用'} · ${call.interface || call.function || '选择接口'}`">
        <div class="fields">
          <label>结果名称</label>
          <el-input :model-value="call.id" :disabled="readonly" @change="patch(index, { id: String($event) })" />
          <label>工程</label>
          <el-select :model-value="call.project" :disabled="readonly" filterable @change="projectChanged(index, String($event))">
            <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.name" />
          </el-select>
          <label>模块</label>
          <el-input :model-value="call.module" :disabled="readonly" placeholder="默认使用工程登记模块"
            @change="patch(index, { module: String($event) })" />
          <el-button :loading="loading[catalogKey(call)]" @click="load(call)">读取接口</el-button>
          <p v-if="errors[catalogKey(call)]" class="error" role="alert">{{ errors[catalogKey(call)] }}</p>
          <p v-if="selected(call)?.compositionError" class="error" role="alert">{{ selected(call)?.compositionError }}</p>
          <label>调用方式</label>
          <el-switch :model-value="!!call.target" :disabled="readonly" active-text="已有对象的方法" inactive-text="函数／构造器"
            @change="patch(index, { target: $event ? { source: 'object', name: '' } : undefined, function: '', interface: '' })" />
          <CompositionArgumentEditor v-if="call.target" :value="call.target" :composition="composition" :values="value.slice(0, index).map((item) => item.id)"
            :readonly="readonly" @update="patch(index, { target: $event })" />
          <label>公开接口</label>
          <el-select :model-value="call.interface || call.function" :disabled="readonly" filterable @change="choose(index, String($event))">
            <el-option v-for="item in catalogues[catalogKey(call)] ?? []" :key="item.name" :label="item.name" :value="item.name" :disabled="!!item.compositionError" />
          </el-select>
          <p v-if="selected(call)?.doc" class="hint">{{ selected(call)?.doc }}</p>
          <div v-for="parameter in selected(call)?.params ?? []" :key="parameter.name" class="parameter">
            <label>{{ parameter.name }}{{ parameter.required ? '（必填）' : '（可选）' }}</label>
            <el-checkbox :model-value="parameter.name in call.arguments" :disabled="readonly"
              @change="argument(index, parameter.name, $event ? { lit: null } : undefined)">配置来源</el-checkbox>
            <CompositionArgumentEditor v-if="parameter.name in call.arguments" :value="call.arguments[parameter.name]"
              :composition="composition" :values="value.slice(0, index).map((item) => item.id)" :readonly="readonly"
              @update="argument(index, parameter.name, $event)" />
          </div>
          <template v-if="selected(call)?.variadicKeywords">
            <div v-for="name in extras(call)" :key="name" class="parameter">
              <label>{{ name }}</label>
              <CompositionArgumentEditor :value="call.arguments[name]" :composition="composition"
                :values="value.slice(0, index).map((item) => item.id)" :readonly="readonly"
                @update="argument(index, name, $event)" />
              <el-button v-if="!readonly" link @click="argument(index, name)">移除参数</el-button>
            </div>
            <div v-if="!readonly" class="actions">
              <el-input v-model="extraNames[index]" aria-label="扩展参数名称" placeholder="原方法接受的额外参数名" />
              <el-button @click="addExtra(index)">添加参数</el-button>
            </div>
          </template>
          <label>返回结果</label>
          <el-select :model-value="objects ? 'object' : call.output ?? 'value'" :disabled="readonly || objects"
            @change="patch(index, { output: $event })">
            <el-option label="结构化数据" value="value" />
            <el-option label="保留业务对象" value="object" />
            <el-option label="数据文件" value="file" />
            <el-option label="图片文件" value="image" />
          </el-select>
          <div v-if="!readonly" class="actions">
            <el-button :disabled="index === 0" @click="emit('update', [...value.slice(0, index - 1), call, value[index - 1]!, ...value.slice(index + 1)])">上移</el-button>
            <el-button type="danger" plain @click="emit('update', value.filter((_, position) => position !== index))">删除调用</el-button>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
    <el-button v-if="!readonly" @click="add">{{ objects ? '添加实例对象' : '添加调用' }}</el-button>
  </div>
</template>

<style scoped>
.fields, .calls-editor { display: grid; gap: 8px; min-width: 0; }
.fields label { font-size: 12px; }
.parameter { border-top: 1px solid var(--el-border-color-lighter); padding-top: 8px; display: grid; gap: 6px; }
.hint { font-size: 12px; white-space: pre-wrap; color: var(--el-text-color-secondary); }
.error { color: var(--el-color-danger); }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
</style>
