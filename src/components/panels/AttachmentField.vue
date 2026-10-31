<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import type { AttachmentRef } from '@/domain/types'
import { errorText } from '@/services/api'
import { fileUrl, uploadFile } from '@/services/files'

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
    <div v-if="attachments?.length" class="list">
      <div v-for="item in attachments" :key="item.hash" class="item">
        <a :href="fileUrl(item.hash)" target="_blank" rel="noopener" class="name">{{ item.name }}</a>
        <el-button v-if="!readonly" text size="small" type="danger" @click="remove(item.hash)">
          移除
        </el-button>
      </div>
    </div>
    <div v-else class="empty">暂无附件</div>
    <el-upload
      v-if="!readonly"
      :show-file-list="false"
      :http-request="handleUpload"
      :disabled="uploading"
    >
      <el-button size="small" :loading="uploading">上传附件</el-button>
    </el-upload>
  </div>
</template>

<style scoped>
.attachment-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}

.name {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.name:hover {
  color: var(--el-color-primary);
}

.empty {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
