import type { FlowDoc } from '@/domain/types'

export function dataPreparationDominates(doc: FlowDoc, producer: string, consumer: string): boolean {
  const entry = doc.nodes.find((node) => node.kind === 'start')?.id
  if (!entry || producer === consumer) return false
  const reachable = (skip?: string) => {
    const seen = new Set<string>()
    const pending = [entry]
    while (pending.length) {
      const current = pending.pop()!
      if (current === skip || seen.has(current)) continue
      seen.add(current)
      pending.push(...doc.edges.filter((edge) => edge.from === current).map((edge) => edge.to))
    }
    return seen
  }
  const all = reachable()
  return all.has(producer) && all.has(consumer) && !reachable(producer).has(consumer)
}
