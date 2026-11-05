<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { UploadRequestOptions } from 'element-plus'
import type { ParamType } from '@/domain/types'
import { structuredValue } from '@/domain/parameters'
import { uploadFile, uploadSizeError } from '@/services/files'
import { errorText, isAbortError } from '@/services/api'

const props = defineProps<{
  modelValue?: unknown
  type?: ParamType
  placeholder?: string
  readonly?: boolean
  id?: string
  min?: number
  max?: number
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: unknown): void
  (e: 'validity', valid: boolean): void
}>()

const kind = computed<ParamType>(() => props.type ?? 'text')
const structured = computed(() => ['array', 'object', 'json'].includes(kind.value))
const jsonPlaceholder = computed(() => kind.value === 'array' ? '[1, 2, 3]'
  : kind.value === 'object' ? '{"channel": "A"}' : '输入 JSON')
const draft = ref('')
const error = ref('')
const uploading = ref(false)
const percent = ref(0)
let controller: AbortController | null = null
let disposed = false
const fileName = computed(() => {
  const value = props.modelValue
  return value && typeof value === 'object' && 'hash' in value
    ? String(('name' in value && value.name) || String(value.hash).slice(0, 12)) : ''
})

watch(() => [props.modelValue, props.type], () => {
  error.value = ''
  if (structured.value) {
    try {
      if (draft.value && JSON.stringify(structuredValue(draft.value, kind.value))
        === JSON.stringify(props.modelValue)) return
    } catch { /* 外部切换参数时覆盖未保存的草稿。 */ }
    draft.value = props.modelValue === undefined ? '' : JSON.stringify(props.modelValue, null, 2)
  }
  emit('validity', true)
}, { immediate: true })

function onStructured(value: unknown): void {
  draft.value = String(value ?? '')
  if (!draft.value.trim()) {
    error.value = ''
    emit('update:modelValue', undefined)
    emit('validity', true)
    return
  }
  try {
    const parsed = structuredValue(draft.value, kind.value)
    error.value = ''
    emit('update:modelValue', parsed)
    emit('validity', true)
  } catch (failure) {
    error.value = failure instanceof SyntaxError ? '请输入合法 JSON；当前修改尚未保存'
      : errorText(failure, '输入结构不正确')
    emit('validity', false)
  }
}

function beforeUpload(file: File): boolean {
  error.value = uploadSizeError(file) ?? ''
  emit('validity', !error.value)
  return !error.value
}

async function upload(options: UploadRequestOptions): Promise<void> {
  controller = new AbortController()
  uploading.value = true
  percent.value = 0
  emit('validity', false)
  try {
    const file = await uploadFile(options.file, {
      signal: controller.signal, onProgress: (value) => { percent.value = value },
    })
    if (disposed) return
    error.value = ''
    emit('update:modelValue', { hash: file.hash, name: file.name })
    emit('validity', true)
  } catch (failure) {
    if (disposed) return
    error.value = isAbortError(failure) ? '' : errorText(failure, '上传失败，请重试')
    emit('validity', !error.value)
  } finally {
    uploading.value = false
    controller = null
  }
}

onBeforeUnmount(() => { disposed = true; controller?.abort() })

const textValue = computed(() =>
  props.modelValue === undefined || props.modelValue === null ? '' : String(props.modelValue),
)
const numberValue = computed(() =>
  typeof props.modelValue === 'number' && Number.isFinite(props.modelValue)
    ? props.modelValue
    : undefined,
)
const boolValue = computed(
  () => props.modelValue === true || props.modelValue === 'true' || props.modelValue === 1 ? true
    : props.modelValue === false || props.modelValue === 'false' || props.modelValue === 0 ? false : undefined,
)

function toText(value: unknown): string {
  return typeof value === 'string' ? value : String(value ?? '')
}

function onText(value: unknown): void {
  const next = toText(value)
  emit('update:modelValue', next === '' ? undefined : next)
}

function onNumber(value: unknown): void {
  emit('update:modelValue', typeof value === 'number' && Number.isFinite(value) ? value : undefined)
}

function onBool(value: unknown): void {
  emit('update:modelValue', typeof value === 'boolean' ? value : undefined)
}

function onDate(value: unknown): void {
  const next = toText(value)
  emit('update:modelValue', next === '' ? undefined : next)
}
</script>

<template>
  <el-input
    v-if="kind === 'text'"
    :model-value="textValue"
    :id="id"
    :placeholder="placeholder"
    :disabled="readonly"
    size="small"
    clearable
    @update:model-value="onText"
  />
  <el-input-number
    v-else-if="kind === 'number'"
    :model-value="numberValue"
    :min="min" :max="max"
    :id="id"
    :placeholder="placeholder"
    :disabled="readonly"
    size="small"
    controls-position="right"
    class="number"
    @update:model-value="onNumber"
  />
  <el-select
    v-else-if="kind === 'boolean'"
    :model-value="boolValue"
    :id="id"
    :disabled="readonly"
    placeholder="请选择"
    clearable
    size="small"
    @update:model-value="onBool"
  ><el-option label="是" :value="true" /><el-option label="否" :value="false" /></el-select>
  <el-date-picker
    v-else-if="kind === 'datetime'"
    :id="id"
    :model-value="textValue"
    type="datetime"
    value-format="YYYY-MM-DDTHH:mm:ssZ"
    :placeholder="placeholder || '选择时间'"
    :disabled="readonly"
    size="small"
    class="date"
    @update:model-value="onDate"
  />
  <div v-else-if="kind === 'file'" class="compound-input">
    <el-upload :show-file-list="false" :disabled="readonly || uploading"
      :before-upload="beforeUpload" :http-request="upload">
      <el-button :id="id" :loading="uploading" size="small">选择文件并上传</el-button>
    </el-upload>
    <span v-if="fileName" class="hint">{{ fileName }}</span>
    <el-button v-if="(fileName || error) && !readonly && !uploading" text size="small"
      @click="error = ''; emit('update:modelValue', undefined); emit('validity', true)">移除</el-button>
    <template v-if="uploading">
      <el-progress :percentage="percent" />
      <el-button size="small" text @click="controller?.abort()">取消上传</el-button>
    </template>
    <span class="hint">上传完成后，业务方法接收服务器上的文件路径。</span>
    <span v-if="error" class="error" role="alert">{{ error }}</span>
  </div>
  <div v-else class="compound-input">
    <el-input :id="id" :model-value="draft" type="textarea" :rows="4" :disabled="readonly"
      :placeholder="jsonPlaceholder"
      :aria-invalid="!!error" @update:model-value="onStructured" />
    <span v-if="error" class="error" role="alert">{{ error }}</span>
  </div>
</template>

<style scoped>
.number,
.date {
  width: 100%;
}
.compound-input { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.error { font-size: 12px; color: var(--el-color-danger); }
</style>
