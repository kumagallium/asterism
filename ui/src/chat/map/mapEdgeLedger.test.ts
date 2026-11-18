import { describe, expect, it } from 'vitest'
import { mapEdgeAppearance } from './mapEdgeAppearance'

const pal = { edge: '#687e9b', edgeProposed: '#8193ab', edgeOn: '#16243b', faded: '#c1cdde' }
const idle = { hovered: false, on: false, faded: false, lang: 'ja' as const, names: true }

describe('台帳の線の描画', () => {
  it('確認済みは濃く太く、未確認は薄く細い', () => {
    const ok = mapEdgeAppearance({ label: '下げる', ledger: true }, pal, idle)
    const un = mapEdgeAppearance({ label: '下げる', ledger: true, thin: true, unconfirmed: true }, pal, idle)
    expect(ok.color).toBe(pal.edgeOn)
    expect(ok.size).toBeGreaterThan(un.size)
    expect(un.color).toBe(pal.edgeProposed)
  })
  it('未確認の名前も出す（薄い字＝muted）。置き場の判定は MapView 側。触れている・選んだ周りは濃く、先に置く', () => {
    const un = { label: '下げる', ledger: true, thin: true, unconfirmed: true }
    expect(mapEdgeAppearance(un, pal, idle)).toMatchObject({ label: '下げる', muted: true, rank: 2 })
    expect(mapEdgeAppearance(un, pal, { ...idle, on: true })).toMatchObject({ label: '下げる', muted: false, rank: 0 })
    expect(mapEdgeAppearance({ label: '下げる', ledger: true }, pal, idle)).toMatchObject({ muted: false, rank: 1 })
    expect(mapEdgeAppearance(un, pal, { ...idle, faded: true }).label).toBe('')
  })
})
