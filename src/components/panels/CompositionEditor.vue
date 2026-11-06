<script setup lang="ts">
import { ref } from 'vue'
import type { Composition, CompositionOperation, CompositionResource } from '@/domain/types'
import CompositionCallsEditor from '@/components/panels/CompositionCallsEditor.vue'
import CompositionArgumentEditor from '@/components/panels/CompositionArgumentEditor.vue'

const props = defineProps<{ value: Composition; readonly?: boolean }>()
const emit = defineEmits<{ (e: 'update', value: Composition): void }>()
const tab = ref('resources')
const defaultErrors = ref<Record<number, string>>({})
function defaults(index: number, text: string): void {
  try {
    const value: unknown = JSON.parse(text)
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('请填写参数名到默认值的 JSON 对象')
    resource(index, { defaults: value as Record<string, unknown> })
    defaultErrors.value[index] = ''
  } catch (error) { defaultErrors.value[index] = error instanceof Error ? error.message : '默认值格式错误' }
}
function resource(index: number, patch: Partial<CompositionResource>): void {
  emit('update', { ...props.value, resources: props.value.resources.map((item, position) => position === index ? { ...item, ...patch } : item) })
}
function operation(index: number, patch: Partial<CompositionOperation>): void {
  emit('update', { ...props.value, operations: props.value.operations.map((item, position) => position === index ? { ...item, ...patch } : item) })
}
function unique(prefix: string, names: string[]): string {
  let number = names.length + 1
  while (names.includes(`${prefix}${number}`)) number++
  return `${prefix}${number}`
}
function addResource(): void {
  emit('update', { ...props.value, resources: [...props.value.resources, {
    id: unique('resource', props.value.resources.map((item) => item.id)), trigger: 'startup', calls: [],
    result: { source: 'value', name: '' },
  }] })
}
function addOperation(): void {
  emit('update', { ...props.value, operations: [...props.value.operations, {
    id: unique('operation', props.value.operations.map((item) => item.id)), calls: [], presentation: [], parameters: [],
  }] })
}
</script>

<template>
  <div class="composition-editor">
    <p class="hint">启动资源获取解析一次；按需资源首次使用时加载。各排查节点选择下方定义的操作。</p>
    <el-tabs v-model="tab" stretch>
      <el-tab-pane label="实例对象" name="objects">
        <CompositionCallsEditor :value="value.objects" :composition="value" :readonly="readonly" objects
          @update="emit('update', { ...value, objects: $event })" />
      </el-tab-pane>
      <el-tab-pane label="共享资源" name="resources">
        <section v-for="(item, index) in value.resources" :key="index" class="section">
          <label>资源名称</label>
          <el-input :model-value="item.id" :disabled="readonly" @change="resource(index, { id: String($event) })" />
          <label>加载时机</label>
          <el-select :model-value="item.trigger" :disabled="readonly" @change="resource(index, { trigger: $event })">
            <el-option label="诊断启动时" value="startup" />
            <el-option label="首次使用时" value="lazy" />
          </el-select>
          <CompositionCallsEditor :value="item.calls" :composition="value" :readonly="readonly"
            @update="resource(index, { calls: $event })" />
          <label>请求参数默认值（如 {"metrics":["vibration"]}）</label>
          <el-input :model-value="JSON.stringify(item.defaults ?? {})" :disabled="readonly"
            @change="defaults(index, String($event))" />
          <p v-if="defaultErrors[index]" role="alert">{{ defaultErrors[index] }}</p>
          <label>资源结果</label>
          <CompositionArgumentEditor :value="item.result" :composition="value" :values="item.calls.map((call) => call.id)"
            :readonly="readonly" @update="resource(index, { result: $event })" />
          <el-button v-if="!readonly" type="danger" link @click="emit('update', { ...value, resources: value.resources.filter((_, position) => position !== index) })">删除资源</el-button>
        </section>
        <el-button v-if="!readonly" @click="addResource">添加共享资源</el-button>
      </el-tab-pane>
      <el-tab-pane label="排查操作" name="operations">
        <section v-for="(item, index) in value.operations" :key="index" class="section">
          <label>操作名称</label>
          <el-input :model-value="item.id" :disabled="readonly" @change="operation(index, { id: String($event) })" />
          <label>操作参数（逗号分隔，如 metric、threshold）</label>
          <el-input :model-value="item.parameters?.join(', ')" :disabled="readonly"
            @change="operation(index, { parameters: String($event).split(/[,，]/).map((name) => name.trim()).filter(Boolean) })" />
          <CompositionCallsEditor :value="item.calls" :composition="value" :readonly="readonly"
            @update="operation(index, { calls: $event })" />
          <label>展示内容</label>
          <div v-for="(block, position) in item.presentation" :key="position" class="section">
            <el-select :model-value="block.type" :disabled="readonly"
              @change="operation(index, { presentation: item.presentation.map((entry, at) => at === position ? { ...entry, type: $event } : entry) })">
              <el-option label="图片" value="image" /><el-option label="文字／结构化结果" value="text" />
            </el-select>
            <CompositionArgumentEditor :value="block.value" :composition="value" :values="item.calls.map((call) => call.id)" :readonly="readonly"
              @update="operation(index, { presentation: item.presentation.map((entry, at) => at === position ? { ...entry, value: $event } : entry) })" />
            <el-button v-if="!readonly" link type="danger" @click="operation(index, { presentation: item.presentation.filter((_, at) => at !== position) })">移除展示</el-button>
          </div>
          <el-button v-if="!readonly" @click="operation(index, { presentation: [...item.presentation, { type: 'text', value: { source: 'value', name: '' } }] })">添加展示</el-button>
          <el-switch :model-value="!!item.decision" :disabled="readonly" active-text="自动判断" inactive-text="人工判断"
            @change="operation(index, { decision: $event ? { result: { source: 'value', name: '', path: 'overall' }, reason: { source: 'value', name: '', path: 'reasons' }, normalReason: '配置的规则均未触发' } : undefined })" />
          <template v-if="item.decision">
            <label>是／否结果</label>
            <CompositionArgumentEditor :value="item.decision.result" :composition="value" :values="item.calls.map((call) => call.id)" :readonly="readonly"
              @update="operation(index, { decision: { ...item.decision!, result: $event } })" />
            <label>判断依据</label>
            <CompositionArgumentEditor :value="item.decision.reason" :composition="value" :values="item.calls.map((call) => call.id)" :readonly="readonly"
              @update="operation(index, { decision: { ...item.decision!, reason: $event } })" />
            <label>未触发规则时的依据</label>
            <el-input :model-value="item.decision.normalReason" :disabled="readonly"
              @update:model-value="operation(index, { decision: { ...item.decision!, normalReason: $event } })" />
          </template>
          <el-button v-if="!readonly" type="danger" link @click="emit('update', { ...value, operations: value.operations.filter((_, position) => position !== index) })">删除操作</el-button>
        </section>
        <el-button v-if="!readonly" @click="addOperation">添加排查操作</el-button>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.section { display: grid; gap: 8px; padding: 12px 0; border-bottom: 1px solid var(--el-border-color); }
.section label { font-size: 12px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
</style>
