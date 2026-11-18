// 線の名前の置き場（大きい図）。語の名前を先に置き、線の名前は「触れている・選んだ周り → 確認済み → 根拠の多い順」に置いていき、
// 既に置いた札（語の名前・ほかの線の名前）と重なるものは省く。拡大すると札どうしが離れるので、省かれていた名前が出てくる。
// 画面の座標（px）で判定する純関数。sigma には触れない（MapView が毎フレームの前に呼ぶ）。

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

/** 2 つの矩形が重なるか（`gap` px 離れていなければ重なりとみなす）。辺が接するだけなら重ならない。 */
export function rectsOverlap(a: Rect, b: Rect, gap = 0): boolean {
  return a.x < b.x + b.w + gap && b.x < a.x + a.w + gap && a.y < b.y + b.h + gap && b.y < a.y + a.h + gap
}

export interface EdgeLabelCandidate {
  id: string
  /** 札の矩形（画面の px）。 */
  rect: Rect
  /** 小さいほど先に置く: 0＝触れている・選んだ周り／1＝確認済み／2＝未確認。 */
  rank: number
  /** 根拠の数（多いほど先）。 */
  evidence: number
}

/** 先に置く順（rank → 根拠の多い順 → id。同じ入力なら同じ結果）。 */
export function byPlacementOrder(a: EdgeLabelCandidate, b: EdgeLabelCandidate): number {
  return a.rank - b.rank || b.evidence - a.evidence || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
}

/** 置ける線の名前の id。`blockers`（語の名前など先に置いた札）と、先に置いた線の名前に重なるものは省く。
 *  `view` を渡すと、画面の外に出る札は省く。 */
export function placeEdgeLabels(candidates: readonly EdgeLabelCandidate[], blockers: readonly Rect[], view?: { width: number; height: number }, gap = 2): Set<string> {
  const placed: Rect[] = [...blockers]
  const out = new Set<string>()
  for (const c of [...candidates].sort(byPlacementOrder)) {
    const r = c.rect
    if (view && (r.x < 0 || r.y < 0 || r.x + r.w > view.width || r.y + r.h > view.height)) continue
    if (placed.some((p) => rectsOverlap(r, p, gap))) continue
    placed.push(r)
    out.add(c.id)
  }
  return out
}
