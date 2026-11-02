<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { AttachmentRef } from '@/domain/types'
import { fileUrl, previewKind } from '@/services/files'

const props = withDefaults(
  defineProps<{ attachments?: AttachmentRef[]; readonly?: boolean; dense?: boolean }>(),
  { readonly: false, dense: false },
)
const emit = defineEmits<{ (e: 'remove', hash: string): void }>()

const items = computed(() => props.attachments ?? [])
const opened = ref<AttachmentRef | null>(null)
const visible = ref(false)
const content = ref('')
const loading = ref(false)
const failure = ref('')

const openedKind = computed(() => (opened.value ? previewKind(opened.value.name) : null))
const openedUrl = computed(() => (opened.value ? fileUrl(opened.value.hash, true) : ''))

// 连续点两个文本文件时，先发的请求可能后到；用序号丢弃过期结果
let sequence = 0

async function open(item: AttachmentRef): Promise<void> {
  const kind = previewKind(item.name)
  if (!kind) {
    ElMessage.info('该类型不支持在线预览，请下载后用本机软件打开')
    return
  }
  const token = ++sequence
  opened.value = item
  content.value = ''
  failure.value = ''
  visible.value = true
  if (kind !== 'text') return
  loading.value = true
  try {
    const response = await fetch(fileUrl(item.hash, true))
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const buffer = await response.arrayBuffer()
    if (token === sequence) content.value = decodeText(buffer)
  } catch (error) {
    if (token === sequence) failure.value = error instanceof Error ? error.message : String(error)
  } finally {
    if (token === sequence) loading.value = false
  }
}

/** 中文 Windows 的 Excel 存的 CSV 多是 GBK，按 UTF-8 硬解会变乱码 */
function decodeText(buffer: ArrayBuffer): string {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer)
  } catch {
    return new TextDecoder('gbk').decode(buffer)
  }
}

/** 走一次真实下载，保留服务端给的文件名，不依赖浏览器对 blob 的命名 */
function download(item: AttachmentRef): void {
  const link = document.createElement('a')
  link.href = fileUrl(item.hash)
  link.download = item.name
  document.body.append(link)
  link.click()
  link.remove()
}
</script>

<template>
  <div class="attachment-list" :class="{ dense }">
    <div v-for="item in items" :key="item.hash" class="row">
      <button type="button" class="name" :title="item.name" @click="open(item)">
        {{ item.name }}
      </button>
      <span v-if="!previewKind(item.name)" class="blocked">不支持在线预览</span>
      <span class="ops">
        <el-button link size="small" @click="download(item)">下载</el-button>
        <el-button
          v-if="!readonly"
          link
          size="small"
          type="danger"
          @click="emit('remove', item.hash)"
        >
          移除
        </el-button>
      </span>
    </div>
    <div v-if="!items.length" class="empty">暂无附件</div>

    <el-dialog v-model="visible" :title="opened?.name" width="720px" append-to-body destroy-on-close>
      <div v-if="failure" class="failure">预览失败：{{ failure }}，请下载后查看</div>
      <div v-else-if="loading" class="failure">读取中…</div>
      <img v-else-if="openedKind === 'image'" :src="openedUrl" :alt="opened?.name" class="image" />
      <iframe
        v-else-if="openedKind === 'pdf'"
        :src="openedUrl"
        :title="opened?.name"
        class="frame"
      />
      <pre v-else-if="openedKind === 'text'" class="text">{{ content }}</pre>
      <audio v-else-if="openedKind === 'audio'" :src="openedUrl" controls autoplay />
      <video v-else-if="openedKind === 'video'" :src="openedUrl" controls class="video" />
      <template #footer>
        <div class="footer">
          <span class="hint">参考文件不可在线编辑，要更新请重新上传</span>
          <span class="footer-ops">
            <el-button v-if="opened" size="small" @click="download(opened)">下载</el-button>
            <el-button size="small" type="primary" @click="visible = false">关闭</el-button>
          </span>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.attachment-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 4px 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}

.name {
  flex: 1;
  min-width: 0;
  padding: 0;
  border: none;
  background: none;
  text-align: left;
  font-size: 12px;
  color: var(--el-color-primary);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.name:hover {
  text-decoration: underline;
}

.blocked {
  flex: none;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.ops {
  flex: none;
  display: flex;
  align-items: center;
  gap: 4px;
}

.ops :deep(.el-button) {
  margin-left: 0;
}

.empty {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* 待办与运行记录里空间紧，去掉底色和留白 */
.dense .row {
  padding: 2px 0;
  background: none;
}

.dense .blocked {
  font-size: 11px;
}

.image {
  display: block;
  max-width: 100%;
  max-height: 60vh;
  margin: 0 auto;
}

.frame {
  width: 100%;
  height: 60vh;
  border: 1px solid var(--el-border-color);
}

.text {
  margin: 0;
  max-height: 60vh;
  overflow: auto;
  padding: 12px;
  background: var(--el-fill-color-light);
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.video {
  display: block;
  max-width: 100%;
  max-height: 60vh;
}

.failure {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.footer-ops {
  flex: none;
  display: flex;
  gap: 8px;
}

.footer-ops :deep(.el-button) {
  margin-left: 0;
}

.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-align: left;
}
</style>
