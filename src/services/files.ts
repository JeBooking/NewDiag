import { ApiError, request } from './api'

export interface UploadedFile {
  hash: string
  name: string
  mediaType?: string
  size?: number
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

export async function uploadFile(file: File): Promise<UploadedFile> {
  const form = new FormData()
  form.append('file', file)
  const data = await request<Record<string, unknown>>('/files', { method: 'POST', body: form })
  const hash = text(data?.hash)
  if (!hash) throw new ApiError(500, '上传响应缺少文件哈希')
  return {
    hash,
    name: text(data.name) ?? text(data.filename) ?? file.name,
    mediaType: text(data.mediaType) ?? text(data.media_type) ?? file.type,
    size: typeof data.size === 'number' ? data.size : file.size,
  }
}

export function fileUrl(hash: string): string {
  return `/api/files/${encodeURIComponent(hash)}`
}
