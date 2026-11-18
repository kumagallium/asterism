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
  it('線が多い図（quiet）では、未確認の名前は選んだ点のまわりだけに出す', () => {
    const un = { label: '下げる', ledger: true, thin: true, unconfirmed: true }
    expect(mapEdgeAppearance(un, pal, { ...idle, quiet: true }).label).toBe('')
    expect(mapEdgeAppearance(un, pal, { ...idle, quiet: true, on: true }).label).toBe('下げる')
    expect(mapEdgeAppearance({ label: '下げる', ledger: true }, pal, { ...idle, quiet: true }).label).toBe('下げる')
  })
})
