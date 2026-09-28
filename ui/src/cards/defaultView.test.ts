import { describe, expect, it } from 'vitest'
import { defaultViewFor } from './defaultView'
import type { ItemSpec, ToolContract, VegaLiteSpec, TableSpec, Row } from './viewSpec'

// 架空の 2 分野（図書館の貸出／気象観測）で 7 種の既定ビューを確かめる。
// defaultViewFor は分野語の辞書を持たない — 見出しはキーの機械整形だけで作る。

const tool = (output_kind: ToolContract['output_kind'], item: Record<string, ItemSpec>): ToolContract => ({
  name: 't',
  title: 'test',
  output_kind,
  item,
})

describe('defaultViewFor', () => {
  it('quantity: 図書館の貸出 — value を先頭に、at → label → subject の順で並べる', () => {
    const t = tool('quantity', {
      checkout_count: { var: 'checkout_count', role: 'value', number: true, unit: 'unit:CT' },
      checked_out_at: { var: 'checked_out_at', role: 'at' },
      book_title: { var: 'book_title', role: 'label' },
      branch_iri: { var: 'branch_iri', role: 'subject' },
    })
    const view = defaultViewFor(t, [])
    expect(view.lang).toBe('table')
    const spec = view.spec as TableSpec
    expect(spec.variant).toBe('figure')
    expect(spec.columns).toEqual([
      { field: 'checkout_count', label: 'checkout count', unit: 'CT', format: 'number', align: 'right' },
      { field: 'checked_out_at', label: 'checked out at', format: 'text' },
      { field: 'book_title', label: 'book title', format: 'text' },
      { field: 'branch_iri', label: 'branch', format: 'iri' },
    ])
    expect(view.custom).toBeUndefined()
  })

  it('series: 気象観測 — x/y/series の役割から折れ線の encoding を作る', () => {
    const t = tool('series', {
      observed_at: { var: 'observed_at', role: 'x', number: false },
      humidity_pct: { var: 'humidity_pct', role: 'y', number: true, unit: 'unit:PERCENT' },
      station_name: { var: 'station_name', role: 'series' },
    })
    const rows: Row[] = [
      { observed_at: '2020-01-01', humidity_pct: 40, station_name: 'north' },
      { observed_at: '2020-01-02', humidity_pct: 55, station_name: 'south' },
    ]
    const view = defaultViewFor(t, rows)
    expect(view.lang).toBe('vega-lite')
    const spec = view.spec as VegaLiteSpec
    expect(spec.mark).toEqual({ type: 'line', point: true })
    expect(spec.encoding).toEqual({
      x: { field: 'observed_at', type: 'ordinal', title: 'observed at' },
      y: { field: 'humidity_pct', type: 'quantitative', title: 'humidity pct [PERCENT]' },
      color: { field: 'station_name', type: 'nominal', title: 'station name' },
    })
    expect((spec.data as { values: Row[] }).values).toEqual(rows)
    expect(view.custom).toBeUndefined()
  })

  it('series: y に number:false を渡しても quantitative のまま（ordinal 化するのは x だけ）', () => {
    const t = tool('series', {
      observed_at: { var: 'observed_at', role: 'x', number: true },
      station_name: { var: 'station_name', role: 'y', number: false },
    })
    const rows: Row[] = [{ observed_at: 1, station_name: 'north' }]
    const view = defaultViewFor(t, rows)
    const spec = view.spec as VegaLiteSpec
    expect(spec.encoding).toEqual({
      x: { field: 'observed_at', type: 'quantitative', title: 'observed at' },
      y: { field: 'station_name', type: 'quantitative', title: 'station name' },
    })
  })

  it('pairs: 図書館の貸出 — x/y はどちらも quantitative', () => {
    const t = tool('pairs', {
      pages: { var: 'pages', role: 'x', number: true },
      loan_days: { var: 'loan_days', role: 'y', number: true },
    })
    const rows: Row[] = [{ pages: 100, loan_days: 14 }]
    const view = defaultViewFor(t, rows)
    expect(view.lang).toBe('vega-lite')
    const spec = view.spec as VegaLiteSpec
    expect(spec.mark).toBe('point')
    expect(spec.encoding).toEqual({
      x: { field: 'pages', type: 'quantitative', title: 'pages' },
      y: { field: 'loan_days', type: 'quantitative', title: 'loan days' },
    })
  })

  it('ranked: 気象観測 — 見えるのは label, value だけ（subject は subject_field へ・K4）・sort は value desc', () => {
    const t = tool('ranked', {
      station_name: { var: 'station_name', role: 'label' },
      rainfall_mm: { var: 'rainfall_mm', role: 'value', number: true, unit: 'unit:MM' },
      station_iri: { var: 'station_iri', role: 'subject' },
    })
    const view = defaultViewFor(t, [])
    expect(view.lang).toBe('table')
    const spec = view.spec as TableSpec
    expect(spec.variant).toBe('ranked')
    expect(spec.columns).toEqual([
      { field: 'station_name', label: 'station name', format: 'text' },
      { field: 'rainfall_mm', label: 'rainfall mm', unit: 'MM', format: 'number', align: 'right' },
    ])
    expect(spec.columns.some((c) => c.format === 'iri')).toBe(false)
    expect(spec.subject_field).toBe('station_iri')
    expect(spec.sort).toEqual({ field: 'rainfall_mm', dir: 'desc' })
  })

  it('breakdown: 図書館の貸出 — category(nominal, sort -x) と count(quantitative) の横棒', () => {
    const t = tool('breakdown', {
      genre: { var: 'genre', role: 'category' },
      title_count: { var: 'title_count', role: 'count', number: true },
    })
    const rows: Row[] = [{ genre: 'fiction', title_count: 5 }]
    const view = defaultViewFor(t, rows)
    expect(view.lang).toBe('vega-lite')
    const spec = view.spec as VegaLiteSpec
    expect(spec.mark).toBe('bar')
    expect(spec.encoding).toEqual({
      y: { field: 'genre', type: 'nominal', sort: '-x', title: 'genre' },
      x: { field: 'title_count', type: 'quantitative', title: 'title count' },
    })
  })

  it('facts: 気象観測 — _iri で終わる列が末尾へ移る（元の並びは途中に iri がある）', () => {
    const t = tool('facts', {
      station_iri: { var: 'station_iri' },
      station_name: { var: 'station_name' },
      humidity_pct: { var: 'humidity_pct', number: true },
    })
    const view = defaultViewFor(t, [])
    expect(view.lang).toBe('table')
    const spec = view.spec as TableSpec
    expect(spec.variant).toBe('grid')
    expect(spec.columns.map((c) => c.field)).toEqual(['station_name', 'humidity_pct', 'station_iri'])
    expect(spec.columns.find((c) => c.field === 'station_iri')?.format).toBe('iri')
  })

  it('flow: rows を使わず、空の graph を返す（呼び側が差し替える）', () => {
    const t = tool('flow', {})
    const view = defaultViewFor(t, [{ anything: 'ignored' }])
    expect(view).toEqual({ lang: 'graph', spec: { nodes: [], edges: [] } })
  })

  it('決定論: 同じ入力には同じ出力を返し、rows を書き換えない', () => {
    const t = tool('series', {
      observed_at: { var: 'observed_at', role: 'x', number: false },
      humidity_pct: { var: 'humidity_pct', role: 'y', number: true },
    })
    const rows: Row[] = [{ observed_at: '2020-01-01', humidity_pct: 40 }]
    const before = JSON.parse(JSON.stringify(rows))
    const a = defaultViewFor(t, rows)
    const b = defaultViewFor(t, rows)
    expect(a).toEqual(b)
    expect(rows).toEqual(before)
  })
})
