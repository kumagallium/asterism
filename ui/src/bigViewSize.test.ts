import { describe, expect, it } from 'vitest'
import { bigGraphHeight, bigViewHeight, zoomFlags } from './bigViewSize'

describe('bigViewHeight', () => {
  it('窓の高さの 8 割・下限 360', () => {
    expect(bigViewHeight(1000)).toBe(800)
    expect(bigViewHeight(900)).toBe(720)
    expect(bigViewHeight(300)).toBe(360)
  })
})

describe('bigGraphHeight', () => {
  it('帯と注意書きの分を引き、下限 320', () => {
    expect(bigGraphHeight(800)).toBe(640)
    expect(bigGraphHeight(360)).toBe(320)
    expect(bigGraphHeight(800, 100)).toBe(700)
  })
})

describe('zoomFlags', () => {
  it('省略・false では拡大縮小しない（従来どおり）', () => {
    expect(zoomFlags()).toEqual({ zoomOnScroll: false, zoomOnDoubleClick: false })
    expect(zoomFlags(false)).toEqual({ zoomOnScroll: false, zoomOnDoubleClick: false })
  })
  it('true で両方 on', () => {
    expect(zoomFlags(true)).toEqual({ zoomOnScroll: true, zoomOnDoubleClick: true })
  })
})
