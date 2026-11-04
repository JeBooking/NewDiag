import type { ProjectFunctionParam } from '@/services/projects'

/** 与后端 compiler._scalar_type 保持一致：剥掉 optional/union 后逐成员精确匹配 */
const SCALAR_TYPES = new Set([
  'text', 'number', 'boolean', 'datetime', 'str', 'int', 'float', 'bool', 'date', 'time', 'decimal',
])

export function isScalarAnnotation(annotation?: string | null): boolean {
  let text = (annotation ?? '').trim().toLowerCase().replace(/typing\./g, '')
  if (!text) return false
  for (const wrapper of ['optional[', 'union[']) {
    if (text.startsWith(wrapper) && text.endsWith(']')) {
      text = text.slice(wrapper.length, -1)
      break
    }
  }
  let found = false
  for (const raw of text.split(/[,|]/)) {
    const member = raw.trim()
    if (!member || member === 'none') continue
    found = true
    if (!SCALAR_TYPES.has(member)) return false
  }
  return found
}

export function isScalarParam(param: ProjectFunctionParam): boolean {
  return isScalarAnnotation(param.annotation)
}
