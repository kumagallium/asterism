import { describe, expect, it } from 'vitest'
import { toFlow } from './KindOverviewMap'
import type { OverviewLayout } from './kindOverview'

const words = {
  count: (n: number) => `${n} 件`,
  bandTitle: '帯',
  bandHint: 'ヒント',
  datasetCount: (k: number, n?: number) => (n != null ? `${k} 種類・${n} 件` : `${k} 種類`),
  omitted: (n: number) => `ほか ${n} 種類`,
  participates: (n: number) => `${n} 種類が参加`,
  hubBandTitle: 'つながり',
  hubBandHint: '',
}

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
  it('節は丸の中心に揃え、下の名前の幅を取る（名前は丸の中に入れない）。件数は下に出す', () => {
    const { nodes } = toFlow(layout, words)
    const c = nodes.find((n) => n.id === 'd1::Tree')!
    // 丸の直径 80 より名前の幅 104 が広いので、節の幅は 104・中心 x=100 に揃える
    expect(c.position).toEqual({ x: 100 - 104 / 2, y: 30 })
    expect((c.data as { countText: string }).countText).toBe('1234 件')
    // ハブの名前は広めに取る（幅 132）
    const h = nodes.find((n) => n.id === 'hub:p:o')!
    expect(h.position).toEqual({ x: 400 - 132 / 2, y: 70 - 16 })
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
  it('データセットごとの俯瞰: 丸の下は「N 種類・M 件」・つながりの帯・線の title', () => {
    const ds: OverviewLayout = {
      frames: [],
      circles: [{ id: 'd1', dataset: 'd1', classIri: '', label: '果樹園', count: 50, kindCount: 3, r: 30, x: 100, y: 50 }],
      hubs: [{ id: 'hub:p:o', label: '産地', r: 16, x: 100, y: 300 }],
      stds: [],
      band: null,
      hubBand: { x: 0, y: 200, w: 300, h: 150 },
      level: 'dataset',
      edges: [{ from: 'd1', to: 'hub:p:o', kind: 'hub', kinds: 2, x1: 0, y1: 0, x2: 1, y2: 1 }],
      width: 300,
      height: 400,
    }
    const { nodes, edges } = toFlow(ds, words)
    expect((nodes.find((n) => n.id === 'd1')!.data as { countText: string }).countText).toBe('3 種類・50 件')
    expect(nodes.some((n) => n.id === 'band:hubs')).toBe(true)
    expect((edges[0].data as { title?: string }).title).toBe('2 種類が参加')
  })
  it('周りだけ開いた枠には「ほか N 種類」を持たせる', () => {
    const l: OverviewLayout = { ...layout, frames: [{ ...layout.frames[0], omitted: 4 }] }
    const f = toFlow(l, words).nodes.find((n) => n.id === 'd1')!
    expect((f.data as { omittedText?: string }).omittedText).toBe('ほか 4 種類')
  })
})
