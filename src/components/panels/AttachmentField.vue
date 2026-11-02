<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import type { AttachmentRef } from '@/domain/types'
import { errorText } from '@/services/api'
import { uploadFile } from '@/services/files'
import AttachmentList from './AttachmentList.vue'

const props = defineProps<{ attachments?: AttachmentRef[]; readonly?: boolean }>()
const emit = defineEmits<{ (e: 'update', value: AttachmentRef[]): void }>()

const uploading = ref(false)

async function handleUpload(options: UploadRequestOptions): Promise<void> {
  uploading.value = true
  try {
    const file = await uploadFile(options.file)
    const existing = props.attachments ?? []
    // 同一内容重复上传时只保留一条引用
    if (!existing.some((item) => item.hash === file.hash)) {
      emit('update', [...existing, { hash: file.hash, name: file.name }])
    }
    ElMessage.success('附件已上传')
  } catch (error) {
    ElMessage.error(errorText(error, '附件上传失败'))
  } finally {
    uploading.value = false
  }
}

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
      :http-request="handleUpload"
      :disabled="uploading"
    >
      <el-button size="small" :loading="uploading">上传附件</el-button>
    </el-upload>
    <p v-if="!readonly" class="hint">
      参考文件不可在线编辑，要更新就上传新文件替换；单个文件不超过 50 MB
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
</style>
