import { describe, expect, it } from 'vitest'
import { toFlow } from './KindOverviewMap'
import type { OverviewLayout } from './kindOverview'

const words = { count: (n: number) => `${n} 件`, bandTitle: '帯', bandHint: 'ヒント' }

const layout: OverviewLayout = {
  frames: [{ id: 'd1', label: '果樹園', x: 0, y: 0, w: 200, h: 120 }],
  circles: [{ id: 'd1::Tree', dataset: 'd1', classIri: 'x:Tree', label: '木', count: 1234, r: 40, x: 100, y: 70 }],
  hubs: [{ id: 'hub:p:o', label: '産地', r: 16, x: 400, y: 70 }],
  stds: [],
  band: null,
  edges: [
    { from: 'd1::Tree', to: 'hub:p:o', kind: 'hub', x1: 140, y1: 70, x2: 384, y2: 70 },
    { from: 'd1::Tree', to: 'd1::Tree', kind: 'alignment', both: true, x1: 0, y1: 0, x2: 1, y2: 1 },
  ],
  width: 500,
  height: 120,
}

describe('toFlow', () => {
  it('丸は中心から半径だけ引いた位置に置き、件数を下に出す', () => {
    const { nodes } = toFlow(layout, words)
    const c = nodes.find((n) => n.id === 'd1::Tree')!
    expect(c.position).toEqual({ x: 60, y: 30 })
    expect((c.data as { countText: string }).countText).toBe('1234 件')
  })
  it('件数が無ければ件数の字は出さない', () => {
    const { nodes } = toFlow(layout, words)
    expect((nodes.find((n) => n.id === 'hub:p:o')!.data as { countText: string }).countText).toBe('')
  })
  it('対応は両端に矢じり、ハブの線は矢じり無し', () => {
    const { edges } = toFlow(layout, words)
    expect(edges[0].markerEnd).toBeUndefined()
    expect(edges[1].markerStart).toBeDefined()
    expect(edges[1].markerEnd).toBeDefined()
  })
  it('節は動かせない・選べない', () => {
    for (const n of toFlow(layout, words).nodes) {
      expect(n.draggable).toBe(false)
      expect(n.selectable).toBe(false)
    }
  })
})
