import type { layoutKindOverview } from './kindOverview'

/**
 * 端点でない丸（種類・ハブ）の中に入る線の本数（余白は含めない）。
 * curved=true は実際に描かれる線（縁の点 x1,y1 → 制御点 cx,cy → x2,y2。制御点が無ければ直線）を 401 点で調べる。
 * curved=false は「曲げる前」の基準で、丸の中心から中心への直線。
 */
export function crossingCount(l: ReturnType<typeof layoutKindOverview>, curved: boolean): number {
  const all = [...l.circles, ...l.hubs]
  const byId = new Map(all.map((c) => [c.id, c]))
  const rectCenter = (id: string) => {
    const f = l.frames.find((x) => x.id === id) ?? l.stds.find((x) => x.id === id)
    return f ? { x: f.x + f.w / 2, y: f.y + f.h / 2 } : null
  }
  let n = 0
  for (const e of l.edges) {
    const a = byId.get(e.from) ?? rectCenter(e.from)
    const b = byId.get(e.to) ?? rectCenter(e.to)
    if (!a || !b) continue
    const s = curved ? { x: e.x1, y: e.y1 } : a
    const t2 = curved ? { x: e.x2, y: e.y2 } : b
    const c = curved && e.cx != null && e.cy != null ? { x: e.cx, y: e.cy } : { x: (s.x + t2.x) / 2, y: (s.y + t2.y) / 2 }
    const hit = all.some((o) => {
      if (o.id === e.from || o.id === e.to) return false
      if ('dataset' in o && (o.dataset === e.from || o.dataset === e.to)) return false
      for (let i = 0; i <= 400; i++) {
        const t = i / 400
        const x = (1 - t) * (1 - t) * s.x + 2 * t * (1 - t) * c.x + t * t * t2.x
        const y = (1 - t) * (1 - t) * s.y + 2 * t * (1 - t) * c.y + t * t * t2.y
        if (Math.hypot(x - o.x, y - o.y) < o.r) return true
      }
      return false
    })
    if (hit) n++
  }
  return n
}
