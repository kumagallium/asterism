import type { MappingSkeleton, SkeletonMap } from './api'

/** Deterministic skeleton-level structure diagram: one box per map, its first
 *  class as a member line, and an inferred edge A --> B when A's ID-template
 *  variables strictly contain B's (A's ID embeds B's key — a parent/child
 *  hint). Boxes only — properties don't exist yet at this stage. The point is
 *  to make "how many kinds, keyed how" visible at a glance: a one-box skeleton
 *  that should be two is obvious in a picture long before it is in a table
 *  (dogfood 2026-07-23). The renderer pre-validates, so a pathological name
 *  degrades to the raw source, never a broken graphic. Own file because a
 *  component file may only export components (react-refresh). */
export function skeletonMermaid(skeleton: MappingSkeleton, edgeLabel: string): string {
  const ids = new Map<string, string>()
  skeleton.maps.forEach((m, i) => {
    let id = m.name.replace(/[^A-Za-z0-9_]/g, '_') || 'map'
    if ([...ids.values()].includes(id)) id = `${id}_${i}`
    ids.set(m.name, id)
  })
  const templateVars = (m: SkeletonMap): Set<string> => {
    const out = new Set<string>()
    for (const match of (m.subject.template ?? '').matchAll(/\{([^{}]+)\}/g)) {
      out.add(match[1])
    }
    return out
  }
  // flowchart, not classDiagram: its label boxes auto-size correctly under the
  // mono theme font (classDiagram clipped the last characters of titles), and
  // quoted labels take CURIEs / Japanese freely.
  const lines = ['flowchart LR']
  for (const m of skeleton.maps) {
    const id = ids.get(m.name)!
    const cls = (m.subject.classes ?? [])[0]?.split(':').pop()
    const label = cls && cls !== m.name ? `${m.name}（${cls}）` : m.name
    lines.push(`  ${id}["${label.replace(/"/g, "'")}"]`)
  }
  for (const a of skeleton.maps) {
    const aVars = templateVars(a)
    for (const b of skeleton.maps) {
      if (a === b) continue
      const bVars = templateVars(b)
      if (bVars.size === 0 || bVars.size >= aVars.size) continue
      if ([...bVars].every((v) => aVars.has(v))) {
        lines.push(`  ${ids.get(a.name)!} -->|${edgeLabel}| ${ids.get(b.name)!}`)
      }
    }
  }
  return lines.join('\n')
}
