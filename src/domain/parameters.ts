import type { ParamType } from '@/domain/types'

export const PARAM_TYPES: { value: ParamType; label: string }[] = [
  { value: 'text', label: '文本' },
  { value: 'number', label: '数字' },
  { value: 'boolean', label: '布尔' },
  { value: 'datetime', label: '时间' },
  { value: 'file', label: '上传文件' },
  { value: 'array', label: '数组' },
  { value: 'object', label: '对象' },
  { value: 'json', label: 'JSON（任意结构）' },
]

export function parameterEmpty(value: unknown, type?: ParamType): boolean {
  return value === undefined || (type !== 'json'
    && (value === null || (typeof value === 'string' && !value.trim())))
}

export function parameterType(annotation?: string | null): ParamType {
  let name = (annotation ?? '').toLowerCase().replace(/typing\.|\s/g, '')
  if (PARAM_TYPES.some((item) => item.value === name)) return name as ParamType
  if (name.startsWith('optional[') && name.endsWith(']')) name = name.slice(9, -1)
  for (const member of name.split(/[|,]/)) {
    const base = member.split('[')[0]
    if (['list', 'tuple', 'sequence', 'set', 'frozenset'].includes(base)) return 'array'
    if (['dict', 'mapping', 'typeddict'].includes(base)) return 'object'
    if (['path', 'pathlib.path', 'filepath'].includes(base)) return 'file'
    if (['int', 'float', 'double', 'decimal'].includes(base)) return 'number'
    if (['bool', 'boolean'].includes(base)) return 'boolean'
    if (['datetime', 'date', 'time'].includes(base)) return 'datetime'
  }
  return 'text'
}

export function structuredValue(text: string, type: ParamType): unknown {
  const value: unknown = JSON.parse(text)
  if (type === 'array' && !Array.isArray(value)) throw new Error('请输入 JSON 数组，例如 [1, 2, 3]')
  if (type === 'object' && (!value || typeof value !== 'object' || Array.isArray(value))) {
    throw new Error('请输入 JSON 对象，例如 {"channel": "A"}')
  }
  return value
}
