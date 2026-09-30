import { describe, expect, it } from 'vitest'
import { place } from './VocabMap'
import { nodeHeight } from './shapeGraph'
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

  it('gives a route to every line inside a frame, and none to a line into the band', () => {
    expect(routes.map((r) => (r ? r.via.length : undefined))).toEqual([0, 0, 1, undefined, 0, 0, 1])
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
