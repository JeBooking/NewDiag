/** 带错误反馈的文件下载：fetch 成 blob 再点链接，失败能拿到 HTTP 状态。 */
export function downloadFileName(header: string | null, fallback: string): string {
  if (!header) return fallback
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      // 头里编码坏了就用兜底名
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain?.[1] ?? fallback
}

export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  const response = await fetch(url)
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const message = payload && typeof payload === 'object' && 'detail' in payload
      && typeof payload.detail === 'string' ? payload.detail : `下载失败（HTTP ${response.status}）`
    throw new Error(message)
  }
  const blob = await response.blob()
  if (blob.size === 0) throw new Error('下载内容为空')

  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = downloadFileName(response.headers.get('content-disposition'), fallbackName)
  document.body.appendChild(link)
  link.click()
  link.remove()
  // 立刻 revoke 会让部分浏览器来不及取内容
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}
