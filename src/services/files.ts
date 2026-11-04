import { ApiError, uploadForm } from './api'

export interface UploadedFile {
  hash: string
  name: string
  mediaType?: string
  size?: number
}

/** 单文件上传上限，与 backend/app/services/storage.py 的 MAX_UPLOAD_BYTES 同源 */
export const MAX_UPLOAD_BYTES = 5 * 1024 ** 3

/** 超限文案，和后端拒绝时的提示保持逐字一致 */
export const UPLOAD_TOO_LARGE = '文件超过 5 GB 上限'

/** 上传前置校验：只看本地 File.size，超限就不发请求 */
export function uploadSizeError(file: File): string | null {
  return file.size > MAX_UPLOAD_BYTES ? UPLOAD_TOO_LARGE : null
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

export interface UploadOptions {
  /** 已上传百分比（0-100），只增不减；100 之后仍可能等后端落盘 */
  onProgress?: (percent: number) => void
  /** 中断上传；abort 后后端会清理临时文件，前端无需额外善后 */
  signal?: AbortSignal
}

export async function uploadFile(file: File, options: UploadOptions = {}): Promise<UploadedFile> {
  const form = new FormData()
  form.append('file', file)
  let last = 0
  const data = await uploadForm<Record<string, unknown>>('/files', form, {
    signal: options.signal,
    onProgress: ({ loaded, total }) => {
      if (!options.onProgress || !total) return
      const percent = Math.min(100, Math.max(last, Math.round((loaded / total) * 100)))
      if (percent === last) return
      last = percent
      options.onProgress(percent)
    },
  })
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
