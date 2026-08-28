import type { MappingSkeleton, SkeletonMap } from './api'

/** The tail of a path, both slash styles — `m.source` and dropped `File`
 *  names are already bare filenames in practice, but this is a one-line
 *  guard against ever naming/comparing the wrong thing (a "not found"
 *  warning, an auto-adopt name match). Shared here (not `SkeletonGate.tsx`,
 *  a component file that may only export components) so the gate and the
 *  kantan wizard's reattach logic compare basenames the SAME way. */
export function basename(path: string): string {
  return path.split(/[/\\]/).pop() || path
}

const templateVars = (m: SkeletonMap): Set<string> => {
  const out = new Set<string>()
  for (const match of (m.subject.template ?? '').matchAll(/\{([^{}]+)\}/g)) {
    out.add(match[1])
  }
  return out
}

/** The ONE containment rule every consumer of this file shares: `bVars` is a
 *  genuine, PROPER subset of `aVars` — A's ID embeds B's key, so A is the
 *  finer/child map and B is the coarser/parent (a Sample card's `{No}` sits
 *  inside a Peak's `{No}/{(hkl)}`). Equal-size or empty never counts (a
 *  reused/renamed key is not containment). Used by the diagram edge
 *  (`skeletonMermaid`), the per-map "counted within" sentence
 *  (`containmentParents`), and the safe-key-fix "this now counts within…"
 *  consequence (`containmentParentsForColumns`, evaluated on a hypothetical
 *  column list, not a map already in the skeleton) — one rule, three
 *  readings, so a diagram edge and a stated sentence can never disagree. */
function embedsKey(aVars: Set<string>, bVars: Set<string>): boolean {
  return bVars.size > 0 && bVars.size < aVars.size && [...bVars].every((v) => aVars.has(v))
}

/** Deterministic skeleton-level structure diagram: one box per map, its first
 *  class as a member line, and an inferred edge A --> B when A's ID-template
 *  variables strictly contain B's (A's ID embeds B's key — a parent/child
 *  hint). Boxes only — properties don't exist yet at this stage. The point is
 *  to make "how many kinds, keyed how" visible at a glance: a one-box skeleton
 *  that should be two is obvious in a picture long before it is in a table
 *  (dogfood 2026-07-23). The renderer pre-validates, so a pathological name
 *  degrades to the raw source, never a broken graphic. Own file because a
 *  component file may only export components (react-refresh). */
export function skeletonMermaid(
  skeleton: MappingSkeleton,
  edgeLabel: string,
  opts: {
    /** `LR` は横並び（幅のある詳細モード）。`TD` は縦並び — かんたん層の図は表の
     *  右に貼り付いた細い列にいるので、横に伸ばすと箱の中の字が潰れる。 */
    direction?: 'LR' | 'TD'
    /** 箱の呼び方。省略すると map 名（＋クラス名）。かんたん層は AI が付けた名前を
     *  ①では見せないので、データ由来の呼び方を渡す。 */
    label?: (m: SkeletonMap) => string
  } = {},
): string {
  const ids = new Map<string, string>()
  skeleton.maps.forEach((m, i) => {
    let id = m.name.replace(/[^A-Za-z0-9_]/g, '_') || 'map'
    if ([...ids.values()].includes(id)) id = `${id}_${i}`
    ids.set(m.name, id)
  })
  // flowchart, not classDiagram: its label boxes auto-size correctly under the
  // mono theme font (classDiagram clipped the last characters of titles), and
  // quoted labels take CURIEs / Japanese freely.
  const lines = [`flowchart ${opts.direction ?? 'LR'}`]
  for (const m of skeleton.maps) {
    const id = ids.get(m.name)!
    const cls = (m.subject.classes ?? [])[0]?.split(':').pop()
    const label =
      opts.label?.(m) ?? (cls && cls !== m.name ? `${m.name}（${cls}）` : m.name)
    lines.push(`  ${id}["${label.replace(/"/g, "'")}"]`)
  }
  for (const a of skeleton.maps) {
    const aVars = templateVars(a)
    for (const b of skeleton.maps) {
      if (a === b) continue
      const bVars = templateVars(b)
      if (embedsKey(aVars, bVars)) {
        lines.push(`  ${ids.get(a.name)!} -->|${edgeLabel}| ${ids.get(b.name)!}`)
      }
    }
  }
  return lines.join('\n')
}

/** The coarser maps a HYPOTHETICAL key (not necessarily any map's CURRENT
 *  template — the safe-key-fix consequence needs to ask this of the fix's
 *  `from` AND `to` column lists, neither of which the skeleton may currently
 *  hold) would count within, under the SAME rule `skeletonMermaid` draws an
 *  edge for. `excludeMapName`, when given, keeps a map from counting itself
 *  as its own parent while its own template is being evaluated. */
export function containmentParentsForColumns(
  skeleton: MappingSkeleton,
  columns: string[],
  excludeMapName?: string,
): { parent: string; columns: string[] }[] {
  const aVars = new Set(columns)
  const out: { parent: string; columns: string[] }[] = []
  for (const b of skeleton.maps) {
    if (b.name === excludeMapName) continue
    const bVars = templateVars(b)
    if (embedsKey(aVars, bVars)) {
      out.push({ parent: b.name, columns: [...bVars] })
    }
  }
  return out
}

/** The coarser maps THIS map (by its own, CURRENT template) is counted
 *  within — "how many of these exist" is answered inside the parent's scope,
 *  not this map's own rows alone. Same containment rule as the diagram edge
 *  (`embedsKey`), so a "counted within" sentence and a diagram edge can never
 *  point at different maps. Empty when the map is unknown or has no parent
 *  (including: it IS the only map, or nothing embeds another map's key). */
export function containmentParents(
  skeleton: MappingSkeleton,
  mapName: string,
): { parent: string; columns: string[] }[] {
  const self = skeleton.maps.find((m) => m.name === mapName)
  if (!self) return []
  return containmentParentsForColumns(skeleton, [...templateVars(self)], mapName)
}
