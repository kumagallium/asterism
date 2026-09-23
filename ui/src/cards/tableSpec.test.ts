import { describe, expect, it } from 'vitest'
import { applyTableSpec, highlightStyleFor, matchesHighlight, type Row, type TableColumn, type TableSpec } from './tableSpec'

// 架空データ（2 分野）: 図書館の貸出件数、気象観測の月間降水量。分野固有名詞は書かない。

const loanRows: Row[] = [
  { branch: '北', loans: 120 },
  { branch: '東', loans: 340 },
  { branch: '南', loans: 210 },
]

const rainRows: Row[] = [
  { station: 'あ', mm: 88.5 },
  { station: 'い', mm: 12.0 },
  { station: 'う', mm: 210.3 },
]

describe('applyTableSpec — sort', () => {
  it('sorts numeric fields ascending', () => {
    const spec: TableSpec = { columns: [], sort: { field: 'loans', dir: 'asc' } }
    const out = applyTableSpec(spec, loanRows)
    expect(out.map((r) => r.branch)).toEqual(['北', '南', '東'])
  })

  it('sorts numeric fields descending', () => {
    const spec: TableSpec = { columns: [], sort: { field: 'mm', dir: 'desc' } }
    const out = applyTableSpec(spec, rainRows)
    expect(out.map((r) => r.station)).toEqual(['う', 'あ', 'い'])
  })

  it('sorts string fields with localeCompare', () => {
    const rows: Row[] = [{ name: 'う' }, { name: 'あ' }, { name: 'い' }]
    const spec: TableSpec = { columns: [], sort: { field: 'name', dir: 'asc' } }
    const out = applyTableSpec(spec, rows)
    expect(out.map((r) => r.name)).toEqual(['あ', 'い', 'う'])
  })
})

describe('applyTableSpec — limit', () => {
  it('truncates to the given limit', () => {
    const spec: TableSpec = { columns: [], limit: 2 }
    const out = applyTableSpec(spec, loanRows)
    expect(out).toHaveLength(2)
  })

  it('leaves rows untouched when limit exceeds row count', () => {
    const spec: TableSpec = { columns: [], limit: 100 }
    const out = applyTableSpec(spec, loanRows)
    expect(out).toHaveLength(3)
  })
})

describe('applyTableSpec — does not mutate input', () => {
  it('leaves the original rows array and spec unchanged', () => {
    const rowsCopy = loanRows.map((r) => ({ ...r }))
    const spec: TableSpec = { columns: [], sort: { field: 'loans', dir: 'desc' }, limit: 2 }
    const specCopy = JSON.parse(JSON.stringify(spec))
    applyTableSpec(spec, loanRows)
    expect(loanRows).toEqual(rowsCopy)
    expect(spec).toEqual(specCopy)
  })
})

describe('applyTableSpec — href_field を素通しする（sort/limit の対象にしない）', () => {
  it('列に href_field があっても行データはそのまま渡る', () => {
    const columns: TableColumn[] = [
      { field: 'value', label: '値', format: 'text', href_field: 'value_iri' },
    ]
    const rows: Row[] = [{ value: 'ZEM', value_iri: 'https://example.org/zem' }]
    const spec: TableSpec = { columns, variant: 'grid' }
    const out = applyTableSpec(spec, rows)
    expect(out).toEqual(rows)
    expect(spec.columns[0].href_field).toBe('value_iri')
  })
})

describe('matchesHighlight / highlightStyleFor', () => {
  it('matches gt/lt/eq operators', () => {
    expect(matchesHighlight({ v: 5 }, { when: { field: 'v', op: 'gt', value: 3 }, style: 'accent' })).toBe(true)
    expect(matchesHighlight({ v: 5 }, { when: { field: 'v', op: 'lt', value: 3 }, style: 'accent' })).toBe(false)
    expect(matchesHighlight({ v: 'ok' }, { when: { field: 'v', op: 'eq', value: 'ok' }, style: 'muted' })).toBe(true)
  })

  it('returns the first matching highlight style, or undefined', () => {
    const highlights = [
      { when: { field: 'v', op: 'gt' as const, value: 100 }, style: 'accent' as const },
      { when: { field: 'v', op: 'gt' as const, value: 0 }, style: 'muted' as const },
    ]
    expect(highlightStyleFor({ v: 50 }, highlights)).toBe('muted')
    expect(highlightStyleFor({ v: 500 }, highlights)).toBe('accent')
    expect(highlightStyleFor({ v: -1 }, highlights)).toBeUndefined()
    expect(highlightStyleFor({ v: 1 }, undefined)).toBeUndefined()
  })
})
