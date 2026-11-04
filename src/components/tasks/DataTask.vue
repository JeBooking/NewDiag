<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import type { UploadRequestOptions } from 'element-plus'
import type { ReviewTask } from '@/domain/types'
import { uploadSizeError, uploadFile } from '@/services/files'
import { errorText, isAbortError } from '@/services/api'
import AttachmentList from '@/components/panels/AttachmentList.vue'

const props = defineProps<{ review: ReviewTask; busy?: boolean; error?: string }>()
const emit = defineEmits<{ (e: 'submit', data: Record<string, unknown>): void }>()
const values = reactive<Record<string, unknown>>({})
const errors = reactive<Record<string, string>>({})
/** 正在上传的字段名，空串表示空闲；同一时刻只允许一个上传 */
const activeField = ref('')
const percent = ref(0)
let controller: AbortController | null = null

watch(() => props.review.id, () => {
  controller?.abort()
  for (const name of Object.keys(values)) delete values[name]
  for (const name of Object.keys(errors)) delete errors[name]
  for (const field of props.review.request.fields ?? []) {
    values[field.name] = field.type === 'json' && field.default !== undefined
      ? JSON.stringify(field.default, null, 2) : field.default
  }
}, { immediate: true })

function beforeUpload(name: string, file: File): boolean {
  const message = uploadSizeError(file)
  if (!message) return true
  errors[name] = message
  return false
}

async function upload(name: string, options: UploadRequestOptions): Promise<void> {
  controller = new AbortController()
  activeField.value = name
  percent.value = 0
  try {
    const file = await uploadFile(options.file, {
      signal: controller.signal,
      onProgress: (value) => { percent.value = value },
    })
    values[name] = { hash: file.hash, name: file.name }
    delete errors[name]
  } catch (error) {
    // 用户主动取消不当作失败提示
    if (!isAbortError(error)) errors[name] = errorText(error, '上传失败')
  } finally {
    controller = null
    activeField.value = ''
    percent.value = 0
  }
}

function cancelUpload(): void {
  controller?.abort()
}

// 面板切走或卸载时不该让上传继续跑，界面上已经没有地方能取消了
onBeforeUnmount(() => controller?.abort())

function fileName(name: string): string {
  const value = values[name]
  return value && typeof value === 'object' && 'name' in value ? String(value.name) : ''
}

function numberValue(name: string): number | undefined {
  const value = values[name]
  return typeof value === 'number' ? value : undefined
}

function submit(): void {
  for (const name of Object.keys(errors)) delete errors[name]
  const data: Record<string, unknown> = {}
  for (const field of props.review.request.fields ?? []) {
    const value = values[field.name]
    if (value === undefined || value === null || value === '') {
      if (field.required !== false) errors[field.name] = '请提供此项数据'
      continue
    }
    if (field.type === 'json') {
      try {
        data[field.name] = JSON.parse(String(value)) as unknown
      } catch {
        errors[field.name] = '请输入合法的 JSON'
      }
    } else data[field.name] = value
  }
  if (!Object.keys(errors).length) emit('submit', data)
}
</script>

<template>
  <div class="data-task">
    <strong>{{ review.request.title }}</strong>
    <p>{{ review.request.instructions }}</p>
    <p v-if="review.request.detail">{{ review.request.detail }}</p>
    <details v-if="review.request.pipeline?.inputs">
      <summary>本次诊断输入</summary><pre>{{ JSON.stringify(review.request.pipeline.inputs, null, 2) }}</pre>
    </details>
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item
        v-for="field in review.request.fields" :key="field.name"
        :label="`${field.label || field.name}${field.unit ? `（${field.unit}）` : ''}`"
        :required="field.required !== false"
        :error="errors[field.name]" :for="`data-${field.name}`"
      >
        <el-input-number
          v-if="field.type === 'number'" :id="`data-${field.name}`"
          :model-value="numberValue(field.name)"
          :min="field.min" :max="field.max" :disabled="busy"
          @update:model-value="values[field.name] = $event"
        />
        <el-select
          v-else-if="field.type === 'boolean'" :id="`data-${field.name}`"
          :model-value="values[field.name]"
          :disabled="busy" @update:model-value="values[field.name] = $event"
        >
          <el-option label="是" :value="true" /><el-option label="否" :value="false" />
        </el-select>
        <div v-else-if="field.type === 'file'">
          <el-upload
            :show-file-list="false" :disabled="busy || activeField !== ''"
            :before-upload="(file: File) => beforeUpload(field.name, file)"
            :http-request="(options: UploadRequestOptions) => upload(field.name, options)"
          >
            <el-button :loading="activeField === field.name">
              {{ activeField === field.name ? '上传中' : '上传数据文件' }}
            </el-button>
          </el-upload>
          <div v-if="activeField === field.name" class="upload-progress">
            <el-progress :percentage="percent" :stroke-width="6" />
            <span v-if="percent >= 100" class="hint">字节已发完，等待服务端接收…</span>
            <el-button size="small" text type="danger" @click="cancelUpload">取消上传</el-button>
          </div>
          <span>{{ fileName(field.name) }}</span>
        </div>
        <el-input
          v-else :id="`data-${field.name}`" :model-value="String(values[field.name] ?? '')"
          :type="field.type === 'json' ? 'textarea' : 'text'" :rows="5" :disabled="busy"
          @update:model-value="values[field.name] = $event"
        />
        <div v-if="field.description" class="hint">{{ field.description }}</div>
      </el-form-item>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <el-button type="primary" native-type="submit" :loading="busy" :disabled="activeField !== ''">
        提交数据并解析
      </el-button>
    </el-form>
    <AttachmentList
      v-if="review.request.attachments?.length" :attachments="review.request.attachments" dense
    />
  </div>
</template>

<style scoped>
.data-task { display: grid; gap: 10px; }
p { margin: 0; white-space: pre-wrap; font-size: 13px; line-height: 1.6; }
.hint { width: 100%; color: var(--el-text-color-secondary); font-size: 12px; }
.upload-progress { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 6px; }
.upload-progress :deep(.el-progress) { flex: 1; min-width: 160px; }
.error { color: var(--el-color-danger); }
pre { overflow: auto; max-height: 160px; font-size: 12px; }
</style>
