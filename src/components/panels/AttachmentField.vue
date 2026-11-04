<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import type { AttachmentRef } from '@/domain/types'
import { errorText, isAbortError } from '@/services/api'
import { uploadSizeError, uploadFile } from '@/services/files'
import AttachmentList from './AttachmentList.vue'

const props = defineProps<{ attachments?: AttachmentRef[]; readonly?: boolean }>()
const emit = defineEmits<{ (e: 'update', value: AttachmentRef[]): void }>()

const uploading = ref(false)
const percent = ref(0)
let controller: AbortController | null = null

function beforeUpload(file: File): boolean {
  const message = uploadSizeError(file)
  if (!message) return true
  ElMessage.error(message)
  return false
}

async function handleUpload(options: UploadRequestOptions): Promise<void> {
  controller = new AbortController()
  uploading.value = true
  percent.value = 0
  try {
    const file = await uploadFile(options.file, {
      signal: controller.signal,
      onProgress: (value) => { percent.value = value },
    })
    const existing = props.attachments ?? []
    // 同一内容重复上传时只保留一条引用
    if (!existing.some((item) => item.hash === file.hash)) {
      emit('update', [...existing, { hash: file.hash, name: file.name }])
    }
    ElMessage.success('附件已上传')
  } catch (error) {
    // 用户主动取消不当作失败提示
    if (!isAbortError(error)) ElMessage.error(errorText(error, '附件上传失败'))
  } finally {
    controller = null
    uploading.value = false
    percent.value = 0
  }
}

function cancelUpload(): void {
  controller?.abort()
}

// 面板关闭后界面上已经没有取消入口，上传要跟着停
onBeforeUnmount(() => controller?.abort())

function remove(hash: string): void {
  emit('update', (props.attachments ?? []).filter((item) => item.hash !== hash))
}
</script>

<template>
  <div class="attachment-field">
    <AttachmentList :attachments="attachments" :readonly="readonly" @remove="remove" />
    <el-upload
      v-if="!readonly"
      :show-file-list="false"
      :before-upload="beforeUpload"
      :http-request="handleUpload"
      :disabled="uploading"
    >
      <el-button size="small" :loading="uploading">{{ uploading ? '上传中' : '上传附件' }}</el-button>
    </el-upload>
    <div v-if="uploading" class="upload-progress">
      <el-progress :percentage="percent" :stroke-width="6" />
      <span v-if="percent >= 100" class="hint">字节已发完，等待服务端接收…</span>
      <el-button size="small" text type="danger" @click="cancelUpload">取消上传</el-button>
    </div>
    <p v-if="!readonly" class="hint">
      参考文件不可在线编辑，要更新就上传新文件替换；单个文件不超过 5 GB
    </p>
  </div>
</template>

<style scoped>
.attachment-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.upload-progress {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.upload-progress :deep(.el-progress) {
  flex: 1;
  min-width: 160px;
}
</style>
