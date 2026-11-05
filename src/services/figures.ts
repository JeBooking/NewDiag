export type Figure =
  | { type: 'line'; values: number[]; title?: string; unit?: string }
  | { type: 'image'; src: string; title?: string; alt?: string; caption?: string }

const PREFIX = 'newdiag:figure:'

export function imageFigureUrl(src: string): string {
  return `/figure?src=${encodeURIComponent(src)}`
}

export function openFigure(figure: Figure): void {
  const id = Array.from(crypto.getRandomValues(new Uint8Array(16)),
    (value) => value.toString(16).padStart(2, '0')).join('')
  const key = PREFIX + id
  try {
    // 新窗口复制会话存储，既不把曲线塞进 URL，也不将诊断数据长期写入本地存储。
    sessionStorage.setItem(key, JSON.stringify(figure))
    const url = figure.type === 'image' ? `${imageFigureUrl(figure.src)}&id=${id}`
      : `/figure?id=${encodeURIComponent(id)}`
    const popup = window.open(url, '_blank',
      'popup,width=1200,height=850,resizable=yes,scrollbars=yes')
    if (!popup) throw new Error('图形窗口被浏览器拦截，请允许此站点打开弹出窗口后重试')
  } finally {
    sessionStorage.removeItem(key)
  }
}

export function loadFigure(id: string): Figure | null {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(PREFIX + id) ?? 'null')
    if (!raw || typeof raw !== 'object') return null
    const value = raw as Record<string, unknown>
    if (value.type === 'line' && Array.isArray(value.values)
      && value.values.every((item) => typeof item === 'number' && Number.isFinite(item))) {
      return { type: 'line', values: value.values,
        title: typeof value.title === 'string' ? value.title : undefined,
        unit: typeof value.unit === 'string' ? value.unit : undefined }
    }
    if (value.type === 'image' && typeof value.src === 'string' && /^[a-f0-9]{64}$/.test(value.src)) {
      return { type: 'image', src: value.src,
        title: typeof value.title === 'string' ? value.title : undefined,
        alt: typeof value.alt === 'string' ? value.alt : undefined,
        caption: typeof value.caption === 'string' ? value.caption : undefined }
    }
  } catch { /* 独立打开或会话数据不可用时由预览页显示可恢复提示。 */ }
  return null
}

export function linePoints(values: number[] = []): string {
  const min = values.reduce((value, item) => Math.min(value, item), Infinity)
  const max = values.reduce((value, item) => Math.max(value, item), -Infinity)
  return values.map((value, index) =>
    [10 + index * 380 / Math.max(values.length - 1, 1),
      110 - (value - min) * 100 / (max - min || 1)].join(','),
  ).join(' ')
}
