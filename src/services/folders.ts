import type { Folder, FolderNode } from '@/domain/types'
import { request } from './api'

export interface CreateFolderPayload {
  name: string
  parentId?: string | null
}

export interface UpdateFolderPayload {
  name?: string
  parentId?: string | null
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

function toFolderNode(raw: unknown): FolderNode | null {
  const item = record(raw)
  const id = text(item?.id)
  if (!item || !id) return null
  const children = Array.isArray(item.children)
    ? item.children
        .map(toFolderNode)
        .filter((child): child is FolderNode => Boolean(child))
    : []
  const parentId = typeof item.parentId === 'string' && item.parentId ? item.parentId : null
  return {
    id,
    name: text(item.name) ?? id,
    parentId,
    createdAt: text(item.createdAt) ?? '',
    flowCount: typeof item.flowCount === 'number' ? item.flowCount : 0,
    children,
  }
}

/** 后端可能返回扁平列表，缺 children 时按 parentId 自行组装，避免前端树空掉 */
function buildTree(list: FolderNode[]): FolderNode[] {
  if (list.some((item) => item.children.length > 0)) return list
  const byId = new Map(list.map((item) => [item.id, { ...item, children: [] as FolderNode[] }]))
  const roots: FolderNode[] = []
  for (const item of byId.values()) {
    const parent = item.parentId ? byId.get(item.parentId) : undefined
    if (parent) parent.children.push(item)
    else roots.push(item)
  }
  return roots
}

export async function listFolders(): Promise<FolderNode[]> {
  const data = await request<unknown>('/folders')
  const raw = Array.isArray(data) ? data : []
  const list = raw.map(toFolderNode).filter((item): item is FolderNode => Boolean(item))
  return buildTree(list)
}

export function createFolder(payload: CreateFolderPayload): Promise<Folder> {
  return request<Folder>('/folders', { method: 'POST', body: JSON.stringify(payload) })
}

export function updateFolder(id: string, payload: UpdateFolderPayload): Promise<Folder> {
  return request<Folder>(`/folders/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function removeFolder(id: string): Promise<unknown> {
  return request<unknown>(`/folders/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
