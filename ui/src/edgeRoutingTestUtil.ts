import type { layoutKindOverview } from './kindOverview'

/** 線（直線 or 二次ベジェ）を 25 点で調べ、端点でない丸（種類・ハブ）の中に入る線の本数。 */
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
    const c = curved && e.cx != null && e.cy != null ? { x: e.cx, y: e.cy } : { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
    const hit = all.some((o) => {
      if (o.id === e.from || o.id === e.to) return false
      if ('dataset' in o && (o.dataset === e.from || o.dataset === e.to)) return false
      for (let i = 0; i <= 24; i++) {
        const t = i / 24
        const x = (1 - t) * (1 - t) * a.x + 2 * t * (1 - t) * c.x + t * t * b.x
        const y = (1 - t) * (1 - t) * a.y + 2 * t * (1 - t) * c.y + t * t * b.y
        if (Math.hypot(x - o.x, y - o.y) < o.r) return true
      }
      return false
    })
    if (hit) n++
  }
  return n
}
