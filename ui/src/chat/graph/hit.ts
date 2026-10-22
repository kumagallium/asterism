// 1 枚の知識の図の当たり判定（PR 11f）。札は四角（±3px）、星は半径 + 6px、線は折れ線から 7px・小さな札の上。
import type { SkyEdge, SkyLayout, SkyNode, TermNode } from '../../sky/skyLayout'
import { geomPoints } from './graphLayout'

/** 線から押せる距離（px）。 */
export const EDGE_HIT = 7

/** 押した所にある点（札・星・視点）。札を先に（星は札の下にあるので、札の枠の中は札）。重なれば中心の近いほう。 */
export function hitNode(layout: SkyLayout, px: number, py: number): SkyNode | null {
  let best: SkyNode | null = null
  let bd = Infinity
  const pass = (accept: (n: SkyNode, dx: number, dy: number, d: number) => boolean): SkyNode | null => {
    best = null
    bd = Infinity
    for (const n of layout.nodes.values()) {
      const dx = n.x - px
      const dy = n.y - py
      const d = Math.hypot(dx, dy)
      if (accept(n, dx, dy, d) && d < bd) {
        bd = d
        best = n
      }
    }
    return best
  }
  return (
    pass((n, dx, dy) => (n.type === 'term' || n.type === 'draft') && Math.abs(dx) <= n.w / 2 + 3 && Math.abs(dy) <= n.h / 2 + 3) ??
    pass((n, _dx, _dy, d) => n.type === 'kind' && d < n.r + 6) ??
    pass((n, _dx, _dy, d) => n.type === 'hub' && d < 16)
  )
}

/** 札の中の標準の語の 1 行（札の下半分）を押したか。そうなら、その札。 */
export function hitStdLine(layout: SkyLayout, px: number, py: number): TermNode | null {
  const n = hitNode(layout, px, py)
  if (!n || n.type !== 'term' || !n.stdId || !n.stdLabel) return null
  return py > n.y + 1 ? n : null
}

function distToPath(pts: { x: number; y: number }[], px: number, py: number): number {
  let best = Infinity
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]
    const b = pts[i]
    const dx = b.x - a.x
    const dy = b.y - a.y
    const len2 = dx * dx + dy * dy || 1
    const k = Math.max(0, Math.min(1, ((px - a.x) * dx + (py - a.y) * dy) / len2))
    best = Math.min(best, Math.hypot(px - (a.x + dx * k), py - (a.y + dy * k)))
  }
  return best
}

/** 押した所にある線（知識の線・提案の線）。小さな札の上か、線から 7px 以内。近いほうを返す。 */
export function hitEdge(layout: SkyLayout, px: number, py: number): SkyEdge | null {
  let best: SkyEdge | null = null
  let bd = Infinity
  for (const e of layout.edges) {
    if ((e.kind !== 'concept' && e.kind !== 'draft') || !e.geom) continue
    let d = distToPath(geomPoints(e.geom), px, py)
    if (e.pill && Math.abs(px - e.pill.x) <= 16 && Math.abs(py - e.pill.y) <= 9) d = Math.min(d, 0)
    if (d <= EDGE_HIT && d < bd) {
      bd = d
      best = e
    }
  }
  return best
}
