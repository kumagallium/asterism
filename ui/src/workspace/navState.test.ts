import { describe, expect, it, vi } from 'vitest'
import skyViewSource from '../sky/SkyView.tsx?raw'
import narrowSource from './useNarrow.ts?raw'
import { SHEET_MAX_NAV_FOLDED, SHEET_MAX_NAV_OPEN, navCollapsedNow, publishNavCollapsed, sheetMaxWidth } from './navState'


describe('会話をシートにする境はナビの状態で変わる（1 か所）', () => {
  it('ナビが開いていれば 1100px・畳んでいれば 980px', () => {
    expect(SHEET_MAX_NAV_OPEN).toBe(1100)
    expect(SHEET_MAX_NAV_FOLDED).toBe(980)
    expect(sheetMaxWidth(false)).toBe(1100)
    expect(sheetMaxWidth(true)).toBe(980)
  })

  it('App が知らせた状態を返す', () => {
    publishNavCollapsed(true)
    expect(navCollapsedNow()).toBe(true)
    publishNavCollapsed(false)
    expect(navCollapsedNow()).toBe(false)
  })

  it('境の数字を SkyView・useNarrow に重ねて書かない（CSS は .sky-ws.is-sheet を受け、判定は sheetMaxWidth）', () => {
    expect(skyViewSource).not.toMatch(/max-width/)
    expect(skyViewSource).toMatch(/sky-ws[^\n]*is-sheet/)
    expect(narrowSource).toMatch(/sheetMaxWidth\(useNavCollapsed\(\)\)/)
    expect(narrowSource).toMatch(/sheetMaxWidth\(navCollapsedNow\(\)\)/)
    vi.restoreAllMocks()
  })
})
