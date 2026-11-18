import { describe, expect, it } from 'vitest'
import { byPlacementOrder, placeEdgeLabels, rectsOverlap, type EdgeLabelCandidate } from './edgeLabelPlacement'

const r = (x: number, y: number, w = 40, h = 18) => ({ x, y, w, h })
const c = (id: string, rect: ReturnType<typeof r>, rank: number, evidence = 0): EdgeLabelCandidate => ({ id, rect, rank, evidence })

describe('rectsOverlap', () => {
  it('重なる・離れている・辺が接するだけ（重ならない）・gap 内（重なり）', () => {
    expect(rectsOverlap(r(0, 0), r(30, 5))).toBe(true)
    expect(rectsOverlap(r(0, 0), r(100, 0))).toBe(false)
    expect(rectsOverlap(r(0, 0), r(40, 0))).toBe(false)
    expect(rectsOverlap(r(0, 0), r(41, 0), 2)).toBe(true)
  })
})

describe('placeEdgeLabels', () => {
  it('語の名前（blockers）に重なる線の名前は省く', () => {
    const got = placeEdgeLabels([c('a', r(10, 10), 1), c('b', r(200, 10), 1)], [r(20, 12)])
    expect([...got]).toEqual(['b'])
  })
  it('優先順: 触れている（0）→ 確認済み（1）→ 未確認（2）、同じなら根拠の多い順。先に置いたものが残る', () => {
    const same = r(0, 0)
    expect([...placeEdgeLabels([c('un', same, 2, 9), c('ok', same, 1, 0)], [])]).toEqual(['ok'])
    expect([...placeEdgeLabels([c('ok', same, 1, 5), c('hot', same, 0, 0)], [])]).toEqual(['hot'])
    expect([...placeEdgeLabels([c('few', same, 2, 1), c('many', same, 2, 7)], [])]).toEqual(['many'])
  })
  it('順は入力順に依らない。離れていれば全部置ける。拡大（距離が開く）と省かれた名前が出る', () => {
    const items = [c('x', r(0, 0), 2, 1), c('y', r(20, 0), 2, 2), c('z', r(300, 0), 2, 0)]
    expect([...placeEdgeLabels(items, [])].sort()).toEqual(['y', 'z'])
    expect([...placeEdgeLabels([...items].reverse(), [])].sort()).toEqual(['y', 'z'])
    const zoomed = items.map((i) => ({ ...i, rect: { ...i.rect, x: i.rect.x * 4 } }))
    expect([...placeEdgeLabels(zoomed, [])].sort()).toEqual(['x', 'y', 'z'])
  })
  it('画面の外に出る札は省く', () => {
    expect([...placeEdgeLabels([c('out', r(-5, 0), 1), c('in', r(10, 10), 1)], [], { width: 100, height: 100 })]).toEqual(['in'])
  })
  it('byPlacementOrder は rank → 根拠 → id', () => {
    expect([c('b', r(0, 0), 1, 1), c('a', r(0, 0), 1, 1), c('c', r(0, 0), 0, 0)].sort(byPlacementOrder).map((x) => x.id)).toEqual(['c', 'a', 'b'])
  })
})
