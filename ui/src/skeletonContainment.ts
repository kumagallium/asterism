import type { MappingSkeleton, SkeletonMap } from './api'

/** ID の**入れ子**についての規則だけを持つ。かつてここは mermaid の図も作って
 *  いたが、図は React Flow（`shapeGraph.ts` ＋ `kantan/ShapeGraph.tsx`）に移った。
 *  残っているのは「A の ID が B のキーを含む」の 1 つの規則と、その 3 通りの
 *  読み方 — 図の線・「〜の中で数える」の 1 文・ID を直したときの帰結。 */

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
 *  reused/renamed key is not containment). The same rule draws the diagram
 *  edge (`shapeGraph.skeletonShape`), states the per-map "counted within"
 *  sentence (`containmentParents`), and answers the safe-key-fix "this now
 *  counts within…" consequence (`containmentParentsForColumns`, evaluated on a
 *  hypothetical column list, not a map already in the skeleton) — one rule,
 *  three readings, so a diagram edge and a stated sentence can never disagree. */
function embedsKey(aVars: Set<string>, bVars: Set<string>): boolean {
  return bVars.size > 0 && bVars.size < aVars.size && [...bVars].every((v) => aVars.has(v))
}

/** The coarser maps a HYPOTHETICAL key (not necessarily any map's CURRENT
 *  template — the safe-key-fix consequence needs to ask this of the fix's
 *  `from` AND `to` column lists, neither of which the skeleton may currently
 *  hold) would count within, under the SAME rule the diagram draws an
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
