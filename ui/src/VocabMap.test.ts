import { describe, expect, it } from 'vitest'
import { place } from './VocabMap'
import { labelRect, nodeHeight, pointOnEdge } from './shapeGraph'
import type { VocabShape } from './vocabGraph'

/** 地図の枠の中の線が、枠の中の箱と同じだけずれているか。線の通り道（席・まっすぐ
 *  降りる下端）は `arrange` が枠の中の座標で返すので、ずらし忘れると席が枠の外に
 *  描かれる（線は図全体の座標で描かれる）。 */

const stats = { datasets: 0, kinds: 0, items: 0, used: 0, candidates: 0, alignments: 0, shared: 0 }

/** 三角（R → a → b ＋ R → b）の枠が 2 つと、帯の標準のことば 1 つ。 */
const shape: VocabShape = {
  clusters: [
    { id: 'd1', label: 'D1' },
    { id: 'd2', label: 'D2' },
  ],
  nodes: [
    { id: 'd1::R', label: 'R', tone: 'record', cluster: 'd1', fields: [{ name: 'x' }, { name: 'y' }] },
    { id: 'd1::a', label: 'a', tone: 'record', cluster: 'd1' },
    { id: 'd1::b', label: 'b', tone: 'record', cluster: 'd1' },
    { id: 'd2::P', label: 'P', tone: 'record', cluster: 'd2' },
    { id: 'd2::q', label: 'q', tone: 'record', cluster: 'd2' },
    { id: 'd2::r', label: 'r', tone: 'record', cluster: 'd2' },
    { id: 'std:1', label: 'name', tone: 'value', vocab: 'schema.org' },
  ],
  edges: [
    { from: 'd1::R', to: 'd1::a', kind: 'link', label: 'has a' },
    { from: 'd1::a', to: 'd1::b', kind: 'link' },
    { from: 'd1::R', to: 'd1::b', kind: 'link', label: 'has b' },
    { from: 'd1::a', to: 'std:1', kind: 'used' },
    { from: 'd2::P', to: 'd2::q', kind: 'link' },
    { from: 'd2::q', to: 'd2::r', kind: 'link' },
    { from: 'd2::P', to: 'd2::r', kind: 'link' },
  ],
  stats,
}

const box = (nodes: ReturnType<typeof place>['nodes'], id: string) => {
  const n = nodes.find((x) => x.id === id)!
  const d = n.data as { width: number; height: number }
  return { x: n.position.x, y: n.position.y, w: d.width, h: d.height }
}

describe('place (vocab map)', () => {
  const { nodes, routes } = place(shape)

  it('gives a route to every line inside a frame and every line into the band', () => {
    // 帯へ降りる線（4 本目）: a の下の段（b）の席 ＋ 枠の下から行の下端まで。
    expect(routes.map((r) => (r ? r.via.length : undefined))).toEqual([0, 0, 1, 2, 0, 0, 1])
  })

  it('shifts the seat and the drop by as much as the boxes of the same frame', () => {
    for (const [cluster, top, mid, skip] of [
      ['cluster:d1', 'd1::R', 'd1::a', 2],
      ['cluster:d2', 'd2::P', 'd2::q', 6],
    ] as const) {
      const frame = box(nodes, cluster)
      const R = box(nodes, top)
      const a = box(nodes, mid)
      const [lane] = routes[skip]!.via
      // 席は途中の段（a の段）の上端から下端まで。a は 1 段目でただ 1 つの箱なので
      // 段の上端 = a の上辺、下端 = a の下辺。
      expect(lane.top).toBe(a.y)
      expect(lane.bottom).toBe(a.y + a.h)
      // 席は a の右隣（同じ段。枠の中）。
      expect(lane.x).toBeGreaterThan(a.x + a.w)
      expect(lane.x).toBeLessThan(frame.x + frame.w)
      // 出どころの段の下端 = R の下辺（R はその段でただ 1 つの箱）＋ 出入り口の半分。
      expect(routes[skip]!.drop).toBe(R.y + R.h + 3)
      // 箱は枠の中。
      for (const b of [R, a]) {
        expect(b.x).toBeGreaterThan(frame.x)
        expect(b.x + b.w).toBeLessThan(frame.x + frame.w)
        expect(b.y).toBeGreaterThan(frame.y)
        expect(b.y + b.h).toBeLessThan(frame.y + frame.h)
      }
    }
  })

  it('makes the frame as wide as the boxes and the seat together', () => {
    // 2 つ目の枠: 箱 1 つ（232）＋ すき間（22）＋ 名前の無い席（24）＋ 余白 26 × 2。
    expect(box(nodes, 'cluster:d2').w).toBe(232 + 22 + 24 + 26 * 2)
    // 1 つ目の枠の席は、名前「has b」が入る幅なので、名前の無い席より広い。
    expect(box(nodes, 'cluster:d1').w).toBeGreaterThan(box(nodes, 'cluster:d2').w)
  })

  it('keeps the box heights the map draws in step with nodeHeight', () => {
    expect(box(nodes, 'd1::R').h).toBe(nodeHeight(shape.nodes[0]))
  })
})

/** 線の上の点を刻んで、箱・枠の中に入っていないかを調べる。端（出どころ・行き先の
 *  出入り口）は箱の辺から 3 外にあるので、刻みの点は箱の中に入らないはず。 */
const STEPS = 200
function pointsOf(nodes: ReturnType<typeof place>['nodes'], route: NonNullable<ReturnType<typeof place>['routes'][number]>, from: string, to: string, fromH: number) {
  const a = box(nodes, from)
  const b = box(nodes, to)
  const p = { x: a.x + a.w / 2, y: a.y + fromH + 3 }
  const q = { x: b.x + b.w / 2, y: b.y - 3 }
  return Array.from({ length: STEPS - 1 }, (_, k) => pointOnEdge(p, q, (k + 1) / STEPS, route))
}
const inside = (pt: { x: number; y: number }, r: { x: number; y: number; w: number; h: number }) =>
  pt.x > r.x && pt.x < r.x + r.w && pt.y > r.y && pt.y < r.y + r.h

const overlap = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) => {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
  return w > 0 && h > 0 ? w * h : 0
}

type Placed = ReturnType<typeof place>
/** 出ている名前の四角（線の端は描かれる線と同じ: 出どころの下辺・行き先の上辺の中央）。 */
function labelRects(shape: VocabShape, nodes: Placed['nodes'], routes: Placed['routes'], labels: Placed['labels']) {
  const out: { edge: number; text: string; rect: ReturnType<typeof labelRect> }[] = []
  shape.edges.forEach((e, i) => {
    const label = labels[i]
    if (!label) return
    const a = box(nodes, e.from)
    const b = box(nodes, e.to)
    const p = { x: a.x + a.w / 2, y: a.y + a.h + 3 }
    const q = { x: b.x + b.w / 2, y: b.y - 3 }
    out.push({ edge: i, text: label.text, rect: labelRect(p, q, label, routes[i] ?? { via: [] }) })
  })
  return out
}

describe('place (vocab map): lines into the band', () => {
  /** 1 行目に 2 つの枠（上の段の箱から帯へ降りる線を持つ）、折り返した 2 行目に
   *  縦長の枠。2 行目の枠は 1 行目の枠のまっすぐ下にある。 */
  const chain = (d: string, n: number) =>
    Array.from({ length: n }, (_, i) => ({
      id: `${d}::${i}`,
      label: `${d}${i}`,
      tone: 'record' as const,
      cluster: d,
      fields: [{ name: 'x' }, { name: 'y' }, { name: 'z' }],
    }))
  const wide: VocabShape = {
    clusters: [
      { id: 'd1', label: 'D1' },
      { id: 'd2', label: 'D2' },
      { id: 'd3', label: 'D3' },
      { id: 'd4', label: 'D4' },
    ],
    nodes: [
      ...chain('d1', 3),
      ...chain('d2', 2),
      ...chain('d3', 2),
      ...chain('d4', 3),
      { id: 'std:1', label: 'one', tone: 'record', vocab: 'v' },
      { id: 'std:2', label: 'two', tone: 'record', vocab: 'v' },
      // 帯は 1 行に 4 つ。5 つ目・6 つ目は 2 行目（1 行目の箱のすき間を通る）。
      ...[3, 4, 5, 6].map((k) => ({ id: `std:${k}`, label: `s${k}`, tone: 'record' as const, vocab: 'v' })),
    ],
    edges: [
      { from: 'd1::0', to: 'd1::1', kind: 'link' },
      { from: 'd1::1', to: 'd1::2', kind: 'link' },
      { from: 'd1::0', to: 'd1::2', kind: 'link' },
      { from: 'd1::0', to: 'std:1', kind: 'used', label: 'u1' },
      { from: 'd1::1', to: 'std:1', kind: 'used', label: 'u2' },
      { from: 'd1::1', to: 'std:2', kind: 'used', label: 'u3' },
      { from: 'd2::0', to: 'd2::1', kind: 'link' },
      { from: 'd2::0', to: 'std:2', kind: 'used', label: 'u4' },
      { from: 'd3::0', to: 'd3::1', kind: 'link' },
      { from: 'd3::0', to: 'std:2', kind: 'candidate', label: 'u5' },
      { from: 'd4::0', to: 'd4::1', kind: 'link' },
      { from: 'd4::1', to: 'd4::2', kind: 'link' },
      { from: 'd4::0', to: 'std:1', kind: 'used', label: 'u6' },
      { from: 'd4::1', to: 'std:5', kind: 'used', label: 'u7' },
      { from: 'd2::0', to: 'std:6', kind: 'used', label: 'u8' },
      { from: 'd1::2', to: 'std:5', kind: 'used', label: 'u9' },
    ],
    stats,
  }
  const { nodes, routes, labels } = place(wide)
  const heightOf = (id: string) => nodeHeight(wide.nodes.find((n) => n.id === id)!)
  const boxes = wide.nodes.map((n) => ({ id: n.id, cluster: n.cluster, r: box(nodes, n.id) }))
  const frames = wide.clusters.map((c) => ({ id: c.id, r: box(nodes, `cluster:${c.id}`) }))
  const bandLines = wide.edges.map((e, i) => ({ e, i })).filter(({ e }) => e.to.startsWith('std:'))

  it('wraps the frames and the band into more than one row (the case this guards)', () => {
    expect(new Set(frames.map((f) => f.r.y)).size).toBeGreaterThan(1)
    expect(new Set(wide.nodes.filter((n) => !n.cluster).map((n) => box(nodes, n.id).y)).size).toBe(2)
  })

  it('never passes behind a box, nor through a frame other than its own', () => {
    for (const { e, i } of bandLines) {
      const route = routes[i]
      expect(route, `${e.from} -> ${e.to}`).toBeDefined()
      const own = wide.nodes.find((n) => n.id === e.from)!.cluster
      for (const pt of pointsOf(nodes, route!, e.from, e.to, heightOf(e.from))) {
        for (const b of boxes) expect(inside(pt, b.r), `${e.from} -> ${e.to} behind ${b.id}`).toBe(false)
        for (const f of frames) {
          if (f.id === own) continue
          expect(inside(pt, f.r), `${e.from} -> ${e.to} through ${f.id}`).toBe(false)
        }
      }
    }
  })

  it('shares one way down per box inside the frame, and one per frame below it', () => {
    // 同じ箱から出る 2 本（u2・u3）は、帯の手前まで同じ道。
    expect(routes[4]).toEqual(routes[5])
    // 同じ枠の別の箱から出る線（u1）も、下の行では同じ席を通る。
    const lastLane = (i: number) => routes[i]!.via[routes[i]!.via.length - 1]
    expect(lastLane(3)).toEqual(lastLane(4))
  })

  it('puts the name of a line into the band below every frame, clear of the band title', () => {
    const band = box(nodes, 'band:standard')
    for (const { e, i } of bandLines) {
      const route = routes[i]!
      const a = box(nodes, e.from)
      const b = box(nodes, e.to)
      const p = { x: a.x + a.w / 2, y: a.y + heightOf(e.from) + 3 }
      const q = { x: b.x + b.w / 2, y: b.y - 3 }
      const label = labels[i]
      expect(label, `${e.from} -> ${e.to}`).toBeDefined()
      const r = labelRect(p, q, label!, route)
      expect(r.y).toBeGreaterThan(Math.max(...frames.map((f) => f.r.y + f.r.h)))
      // 帯の見出し（上端から 48）には重ならない。
      expect(r.y + r.h <= band.y || r.y >= band.y + 48).toBe(true)
    }
  })

  it('never lets two names, or a name and a box, overlap', () => {
    const rects = labelRects(wide, nodes, routes, labels)
    for (const [k, r] of rects.entries()) {
      for (const b of boxes) expect(overlap(r.rect, b.r), `${r.text} on ${b.id}`).toBe(0)
      for (const o of rects.slice(k + 1)) expect(overlap(r.rect, o.rect), `${r.text} on ${o.text}`).toBe(0)
    }
  })

  it('is the same picture every time', () => {
    expect(place(wide)).toEqual(place(wide))
  })
})

describe('place (vocab map): a line into the band from a short box beside a tall one', () => {
  /** 根 → 低い箱・高い箱（同じ段）→ 下の段。低い箱から帯へ降りる線は、下の段の席へ
   *  曲がる前に、段の下端（高い箱の下辺）までまっすぐ降りる。 */
  const shape: VocabShape = {
    clusters: [{ id: 'd', label: 'D' }],
    nodes: [
      { id: 'd::root', label: 'root', tone: 'record', cluster: 'd' },
      { id: 'd::short', label: 'short', tone: 'record', cluster: 'd' },
      {
        id: 'd::tall',
        label: 'tall',
        tone: 'record',
        cluster: 'd',
        fields: Array.from({ length: 8 }, (_, k) => ({ name: `f${k}` })),
      },
      { id: 'd::low', label: 'low', tone: 'record', cluster: 'd' },
      { id: 'std:1', label: 'one', tone: 'record', vocab: 'v' },
    ],
    edges: [
      { from: 'd::root', to: 'd::short', kind: 'link' },
      { from: 'd::root', to: 'd::tall', kind: 'link' },
      { from: 'd::tall', to: 'd::low', kind: 'link' },
      { from: 'd::short', to: 'std:1', kind: 'used', label: 'u' },
    ],
    stats,
  }
  const { nodes, routes } = place(shape)

  it('drops to the bottom of its row before it turns', () => {
    const tall = box(nodes, 'd::tall')
    expect(routes[3]!.drop).toBe(tall.y + tall.h + 3)
  })
})

describe('place (vocab map): lines into the band, on many made-up maps', () => {
  /** 決まった種から作る乱数（毎回同じ地図）。 */
  const rng = (seed: number) => {
    let x = seed >>> 0 || 1
    return () => {
      x = (Math.imul(x, 1664525) + 1013904223) >>> 0
      return x / 2 ** 32
    }
  }
  /** 枠の数・箱の数・項目の数（= 箱の高さ）・線をばらばらに。同じ行に高さのちがう箱と枠が並ぶ。 */
  const made = (seed: number): VocabShape => {
    const r = rng(seed)
    const pick = (n: number) => Math.floor(r() * n)
    const clusters = Array.from({ length: 1 + pick(6) }, (_, c) => ({ id: `c${c}`, label: `C${c}` }))
    const nodes: VocabShape['nodes'] = []
    const edges: VocabShape['edges'] = []
    for (const c of clusters) {
      const n = 1 + pick(6)
      for (let k = 0; k < n; k++) {
        nodes.push({
          id: `${c.id}::${k}`,
          label: `${c.id}${k}`,
          tone: 'record',
          cluster: c.id,
          fields: Array.from({ length: pick(7) }, (_, j) => ({ name: `f${j}` })),
        })
        // 前の箱へ下向きの線（段ができる）。
        for (let j = 0; j < k; j++) {
          if (r() < 0.35) edges.push({ from: `${c.id}::${j}`, to: `${c.id}::${k}`, kind: 'link', label: r() < 0.5 ? `l${j}` : undefined })
        }
      }
    }
    const stds = Array.from({ length: 1 + pick(12) }, (_, k) => `std:${k}`)
    for (const id of stds) nodes.push({ id, label: id, tone: 'record', vocab: 'v' })
    for (const n of nodes.filter((x) => x.cluster)) {
      if (r() < 0.5) edges.push({ from: n.id, to: stds[pick(stds.length)], kind: 'used', label: `u${n.id}` })
    }
    return { clusters, nodes, edges, stats }
  }

  it('never passes behind a box, nor through a frame other than its own', () => {
    let lines = 0
    const wrong: string[] = []
    for (let seed = 1; seed <= 300; seed++) {
      const shape = made(seed)
      const { nodes, routes } = place(shape)
      const byId = new Map(shape.nodes.map((n) => [n.id, n]))
      const boxes = shape.nodes.map((n) => ({ id: n.id, r: box(nodes, n.id) }))
      const frames = shape.clusters
        .filter((c) => nodes.some((n) => n.id === `cluster:${c.id}`))
        .map((c) => ({ id: c.id, r: box(nodes, `cluster:${c.id}`) }))
      shape.edges.forEach((e, i) => {
        if (e.kind === 'link') return
        lines++
        const own = byId.get(e.from)!.cluster
        const hit = new Set<string>()
        for (const pt of pointsOf(nodes, routes[i]!, e.from, e.to, nodeHeight(byId.get(e.from)!))) {
          for (const b of boxes) if (inside(pt, b.r)) hit.add(`behind ${b.id}`)
          for (const f of frames) if (f.id !== own && inside(pt, f.r)) hit.add(`through ${f.id}`)
        }
        for (const h of hit) wrong.push(`seed ${seed}: ${e.from} -> ${e.to} ${h}`)
      })
    }
    expect(wrong).toEqual([])
    // 検査が空回りしていない（線が十分にある）。
    expect(lines).toBeGreaterThan(500)
  }, 60000)
})

describe('place (vocab map): names of lines', () => {
  /** 見本の形: 上の段が中の段と下の段を指し、中の段も下の段を指す。上 → 下 と 中 → 下 は
   *  同じ名前。上と中からは、同じ名前で帯へも降りる。 */
  const f = (name: string) => [{ name: `${name}1` }, { name: `${name}2` }, { name: `${name}3` }]
  const tri: VocabShape = {
    clusters: [{ id: 'w', label: 'W' }],
    nodes: [
      { id: 'w::top', label: 'Top', tone: 'record', cluster: 'w', fields: f('t') },
      { id: 'w::mid', label: 'Mid', tone: 'record', cluster: 'w', fields: f('m') },
      { id: 'w::low', label: 'Low', tone: 'record', cluster: 'w', fields: f('l') },
      { id: 'std:a', label: 'a', tone: 'record', vocab: 'v' },
      { id: 'std:b', label: 'b', tone: 'record', vocab: 'v' },
    ],
    edges: [
      { from: 'w::top', to: 'w::mid', kind: 'link', label: 'to mid' },
      { from: 'w::top', to: 'w::low', kind: 'link', label: 'made by' },
      { from: 'w::top', to: 'std:a', kind: 'used', label: 'made by' },
      { from: 'w::mid', to: 'w::low', kind: 'link', label: 'made by' },
      { from: 'w::mid', to: 'std:a', kind: 'used', label: 'made by' },
      { from: 'w::mid', to: 'std:b', kind: 'used', label: 'other' },
      { from: 'w::low', to: 'std:b', kind: 'used', label: 'low used' },
    ],
    stats,
  }
  const { nodes, routes, labels } = place(tri)

  it('says the same name once (as the dataset map does)', () => {
    expect(labels.filter((l) => l?.text === 'made by')).toHaveLength(1)
  })

  it('keeps every name off the other lines, the boxes and the other names', () => {
    const rects = labelRects(tri, nodes, routes, labels)
    expect(rects.map((r) => r.text).sort()).toEqual(['low used', 'made by', 'other', 'to mid'])
    for (const r of rects) {
      for (const n of tri.nodes) expect(overlap(r.rect, box(nodes, n.id)), `${r.text} on ${n.id}`).toBe(0)
      for (const o of rects) if (o !== r) expect(overlap(r.rect, o.rect), `${r.text} on ${o.text}`).toBe(0)
      tri.edges.forEach((e, j) => {
        if (j === r.edge) return
        const a = box(nodes, e.from)
        const b = box(nodes, e.to)
        const p = { x: a.x + a.w / 2, y: a.y + a.h + 3 }
        const q = { x: b.x + b.w / 2, y: b.y - 3 }
        // 端（出入り口のすぐそば）は、同じ箱から出る線どうしが必ず重なるので数えない。
        for (let k = 4; k < STEPS - 4; k++) {
          const pt = pointOnEdge(p, q, k / STEPS, routes[j] ?? { via: [] })
          expect(inside(pt, r.rect), `${r.text} crossed by ${e.from} -> ${e.to}`).toBe(false)
        }
      })
    }
  })
})
