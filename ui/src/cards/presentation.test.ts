import { describe, expect, it } from 'vitest'
import { allowedPresentations, viewFor } from './presentation'
import { defaultViewFor } from './defaultView'
import type { ItemSpec, Row, TableSpec, ToolContract, VegaLiteSpec } from './viewSpec'
import defaultViewCases from './fixtures/default_view_cases.json'

// 架空の 2 分野（図書館の貸出／気象観測）で presentation の固定表を確かめる。
// `presentation.ts` は分野語の辞書を持たない — defaultView.test.ts と同じ流儀。

const tool = (output_kind: ToolContract['output_kind'], item: Record<string, ItemSpec>): ToolContract => ({
  name: 't',
  title: 'test',
  output_kind,
  item,
})

describe('allowedPresentations: 固定表どおりの候補', () => {
  it('series: line/bar/point/table・swapXY 不可・colorBy は series 役があるときだけ', () => {
    const t = tool('series', {
      observed_at: { var: 'observed_at', role: 'x', number: false },
      humidity_pct: { var: 'humidity_pct', role: 'y', number: true },
      station_name: { var: 'station_name', role: 'series' },
    })
    const options = allowedPresentations(t, [])
    expect(options.marks).toEqual(['line', 'bar', 'point', 'table'])
    expect(options.swapXY).toBe(false)
    expect(options.colorBy).toEqual({ key: 'station_name', label: 'station name' })
  })

  it('pairs: point/line/table・swapXY 可・colorBy は category 役があるときだけ', () => {
    const t = tool('pairs', {
      pages: { var: 'pages', role: 'x', number: true },
      loan_days: { var: 'loan_days', role: 'y', number: true },
      genre: { var: 'genre', role: 'category' },
    })
    const options = allowedPresentations(t, [])
    expect(options.marks).toEqual(['point', 'line', 'table'])
    expect(options.swapXY).toBe(true)
    expect(options.colorBy).toEqual({ key: 'genre', label: 'genre' })
  })

  it('ranked: table/bar（表が既定）・swapXY 不可・colorBy 不可', () => {
    const t = tool('ranked', {
      name: { var: 'name', role: 'label' },
      rainfall: { var: 'rainfall', role: 'value', number: true },
    })
    const options = allowedPresentations(t, [])
    expect(options.marks).toEqual(['table', 'bar'])
    expect(options.swapXY).toBe(false)
    expect(options.colorBy).toBeNull()
  })

  it('breakdown: bar/table・swapXY 不可・colorBy 不可', () => {
    const t = tool('breakdown', {
      genre: { var: 'genre', role: 'category' },
      title_count: { var: 'title_count', role: 'count', number: true },
    })
    const options = allowedPresentations(t, [])
    expect(options.marks).toEqual(['bar', 'table'])
    expect(options.swapXY).toBe(false)
    expect(options.colorBy).toBeNull()
  })

  it('quantity/facts/flow: 切替 UI 自体を出さない（候補が空）', () => {
    expect(allowedPresentations(tool('quantity', {}), []).marks).toEqual([])
    expect(allowedPresentations(tool('facts', {}), []).marks).toEqual([])
    expect(allowedPresentations(tool('flow', {}), []).marks).toEqual([])
  })

  it('colorBy の列が無いときは候補に出ない', () => {
    const series = tool('series', {
      observed_at: { var: 'observed_at', role: 'x', number: true },
      humidity_pct: { var: 'humidity_pct', role: 'y', number: true },
    })
    expect(allowedPresentations(series, []).colorBy).toBeNull()
    const pairs = tool('pairs', {
      pages: { var: 'pages', role: 'x', number: true },
      loan_days: { var: 'loan_days', role: 'y', number: true },
    })
    expect(allowedPresentations(pairs, []).colorBy).toBeNull()
  })
})

describe('viewFor: mark を変えても役割は同じ', () => {
  const seriesTool = tool('series', {
    observed_at: { var: 'observed_at', role: 'x', number: true },
    humidity_pct: { var: 'humidity_pct', role: 'y', number: true },
  })
  const rows: Row[] = [{ observed_at: 1, humidity_pct: 40 }]

  it('line/bar/point はどれも x=observed_at, y=humidity_pct の同じ encoding', () => {
    for (const mark of ['line', 'bar', 'point'] as const) {
      const view = viewFor(seriesTool, rows, { mark })
      const spec = view.spec as VegaLiteSpec
      expect((spec.encoding as Record<string, { field: string }>).x.field).toBe('observed_at')
      expect((spec.encoding as Record<string, { field: string }>).y.field).toBe('humidity_pct')
    }
  })

  it('table は同じ 2 列（x, y）を持つ表になる', () => {
    const view = viewFor(seriesTool, rows, { mark: 'table' })
    expect(view.lang).toBe('table')
    const spec = view.spec as TableSpec
    expect(spec.columns.map((c) => c.field)).toEqual(['observed_at', 'humidity_pct'])
  })
})

describe('viewFor: swapXY で x と y の field が入れ替わる（pairs）', () => {
  const pairsTool = tool('pairs', {
    pages: { var: 'pages', role: 'x', number: true },
    loan_days: { var: 'loan_days', role: 'y', number: true },
  })
  const rows: Row[] = [{ pages: 100, loan_days: 14 }]

  it('swapXY: false（既定）は x=pages, y=loan_days', () => {
    const view = viewFor(pairsTool, rows, { mark: 'point', swapXY: false })
    const spec = view.spec as VegaLiteSpec
    expect((spec.encoding as Record<string, { field: string; title: string }>).x).toEqual({
      field: 'pages',
      type: 'quantitative',
      title: 'pages',
    })
    expect((spec.encoding as Record<string, { field: string; title: string }>).y).toEqual({
      field: 'loan_days',
      type: 'quantitative',
      title: 'loan days',
    })
  })

  it('swapXY: true は x=loan_days, y=pages に入れ替わる', () => {
    const view = viewFor(pairsTool, rows, { mark: 'point', swapXY: true })
    const spec = view.spec as VegaLiteSpec
    expect((spec.encoding as Record<string, { field: string }>).x.field).toBe('loan_days')
    expect((spec.encoding as Record<string, { field: string }>).y.field).toBe('pages')
  })
})

describe('viewFor: 無効な presentation は既定と同じ ViewSpec', () => {
  it('series: 表に無い mark は既定（line）に戻る', () => {
    const t = tool('series', {
      observed_at: { var: 'observed_at', role: 'x', number: true },
      humidity_pct: { var: 'humidity_pct', role: 'y', number: true },
    })
    const rows: Row[] = [{ observed_at: 1, humidity_pct: 40 }]
    const invalid = viewFor(t, rows, { mark: 'pie' as never })
    expect(invalid).toEqual(defaultViewFor(t, rows))
  })

  it('series: 許されない swapXY は既定に戻る', () => {
    const t = tool('series', {
      observed_at: { var: 'observed_at', role: 'x', number: true },
      humidity_pct: { var: 'humidity_pct', role: 'y', number: true },
    })
    const rows: Row[] = [{ observed_at: 1, humidity_pct: 40 }]
    expect(viewFor(t, rows, { swapXY: true })).toEqual(defaultViewFor(t, rows))
  })

  it('series: 候補に無い colorBy は既定に戻る', () => {
    const t = tool('series', {
      observed_at: { var: 'observed_at', role: 'x', number: true },
      humidity_pct: { var: 'humidity_pct', role: 'y', number: true },
    })
    const rows: Row[] = [{ observed_at: 1, humidity_pct: 40 }]
    expect(viewFor(t, rows, { colorBy: 'no_such_column' })).toEqual(defaultViewFor(t, rows))
  })

  it('quantity/facts/flow: presentation を渡しても無視して既定になる', () => {
    const t = tool('facts', { a: { var: 'a' } })
    const rows: Row[] = [{ a: 1 }]
    expect(viewFor(t, rows, { mark: 'bar' })).toEqual(defaultViewFor(t, rows))
  })
})

describe('defaultViewFor と viewFor(…, undefined) が deepEqual（既存 fixtures 全ケース）', () => {
  interface DefaultViewCase {
    name: string
    tool: ToolContract
    rows: Row[]
    expected: { lang: string; spec: unknown }
  }
  const cases = defaultViewCases as unknown as DefaultViewCase[]

  it('フィクスチャが空でない（取り違え防止）', () => {
    expect(cases.length).toBeGreaterThan(0)
  })

  it.each(cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    expect(viewFor(c.tool, c.rows, undefined)).toEqual(defaultViewFor(c.tool, c.rows))
  })
})
