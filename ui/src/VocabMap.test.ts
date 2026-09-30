import { describe, expect, it } from 'vitest'
import { place } from './VocabMap'
import { nodeHeight, pointOnEdge } from './shapeGraph'
import type { VocabShape } from './vocabGraph'

/** 地図の枠の中の線が、枠の中の箱と同じだけずれているか。線の通り道（席・まっすぐ
 *  降りる下端）は `arrange` が枠の中の座標で返すので、ずらし忘れると席が枠の外に
 *  描かれる（線は図全体の座標で描かれる）。 */

const stats = { datasets: 0, kinds: 0, items: 0, used: 0, candidates: 0, alignments: 0 }

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
  const { nodes, routes, labelAts } = place(wide)
  const heightOf = (id: string) => nodeHeight(wide.nodes.find((n) => n.id === id)!)
  const boxes = wide.nodes.map((n) => ({ id: n.id, cluster: n.cluster, r: box(nodes, n.id) }))
  const frames = wide.clusters.map((c) => ({ id: c.id, r: box(nodes, `cluster:${c.id}`) }))
  const band = wide.edges.map((e, i) => ({ e, i })).filter(({ e }) => e.to.startsWith('std:'))

  it('wraps the frames and the band into more than one row (the case this guards)', () => {
    expect(new Set(frames.map((f) => f.r.y)).size).toBeGreaterThan(1)
    expect(new Set(wide.nodes.filter((n) => !n.cluster).map((n) => box(nodes, n.id).y)).size).toBe(2)
  })

  it('never passes behind a box, nor through a frame other than its own', () => {
    for (const { e, i } of band) {
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

  it('puts the name of a line into the band below the last frame it passes', () => {
    for (const { e, i } of band) {
      const route = routes[i]!
      const a = box(nodes, e.from)
      const b = box(nodes, e.to)
      const p = { x: a.x + a.w / 2, y: a.y + heightOf(e.from) + 3 }
      const q = { x: b.x + b.w / 2, y: b.y - 3 }
      const at = pointOnEdge(p, q, labelAts[i]!, route)
      // どの線も最後の行の下を通るので、名前はどの枠よりも下、帯の上端より上。
      expect(at.y).toBeGreaterThan(Math.max(...frames.map((f) => f.r.y + f.r.h)))
      expect(at.y).toBeLessThan(box(nodes, 'band:standard').y)
    }
  })

  it('is the same picture every time', () => {
    expect(place(wide)).toEqual(place(wide))
  })
})
