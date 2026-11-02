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

export function fileUrl(hash: string, inline = false): string {
  const base = `/api/files/${encodeURIComponent(hash)}`
  return inline ? `${base}?disposition=inline` : base
}

export type PreviewKind = 'image' | 'pdf' | 'text' | 'audio' | 'video'

/** 与后端 INLINE_TYPES 同源，改这张表要同步改 backend/app/api/files.py */
const PREVIEW_KINDS: Record<string, PreviewKind> = {
  png: 'image',
  jpg: 'image',
  jpeg: 'image',
  webp: 'image',
  gif: 'image',
  bmp: 'image',
  avif: 'image',
  pdf: 'pdf',
  txt: 'text',
  log: 'text',
  csv: 'text',
  md: 'text',
  json: 'text',
  yaml: 'text',
  yml: 'text',
  ini: 'text',
  cfg: 'text',
  mp3: 'audio',
  wav: 'audio',
  ogg: 'audio',
  m4a: 'audio',
  aac: 'audio',
  flac: 'audio',
  mp4: 'video',
  webm: 'video',
  mov: 'video',
}

/** 只看扩展名：Office 文件在浏览器里拿到的 media_type 常是空的 */
export function previewKind(name: string): PreviewKind | null {
  const index = name.lastIndexOf('.')
  if (index <= 0) return null
  return PREVIEW_KINDS[name.slice(index + 1).toLowerCase()] ?? null
}
