import { describe, expect, it } from 'vitest'
import { applyPresentation } from './applyPresentation'
import type { TableSpec, ViewSpec, VegaLiteSpec } from './viewSpec'

function vegaLiteView(mark: string): ViewSpec {
  return { lang: 'vega-lite', spec: { mark, encoding: {} } as unknown as VegaLiteSpec }
}

describe('applyPresentation', () => {
  it('presentation が無ければそのまま返す', () => {
    const view = vegaLiteView('line')
    expect(applyPresentation(view, null)).toBe(view)
    expect(applyPresentation(view, undefined)).toBe(view)
  })

  it('vega-lite でなければそのまま返す（table）', () => {
    const view: ViewSpec = { lang: 'table', spec: { columns: [] } as unknown as TableSpec }
    expect(applyPresentation(view, { mark: 'bar' })).toBe(view)
  })

  it('許す値（line/bar/point）だけ mark を上書きする', () => {
    const view = vegaLiteView('line')
    const bar = applyPresentation(view, { mark: 'bar' })
    expect(bar.lang).toBe('vega-lite')
    expect((bar.spec as VegaLiteSpec).mark).toBe('bar')
    // 入力を書き換えない
    expect((view.spec as VegaLiteSpec).mark).toBe('line')
  })

  it('許可外の値はそのまま返す', () => {
    const view = vegaLiteView('line')
    expect(applyPresentation(view, { mark: 'area' })).toBe(view)
    expect(applyPresentation(view, { mark: 123 })).toBe(view)
    expect(applyPresentation(view, {})).toBe(view)
  })
})
