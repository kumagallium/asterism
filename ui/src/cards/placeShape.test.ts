import { describe, expect, it } from 'vitest'
import {
  buildShapePreview,
  footerSummary,
  isDefinitionGapValue,
  pillFor,
  readHeading,
  readSummary,
  type PlaceSubjectItem,
  type ShapeMatch,
} from './placeShape'

describe('pillFor', () => {
  it('linked → ok', () => {
    expect(pillFor('linked')).toEqual({ tone: 'ok', match: 'linked' })
  })
  it('ambiguous → warn', () => {
    expect(pillFor('ambiguous')).toEqual({ tone: 'warn', match: 'ambiguous' })
  })
  it('own_only → mute', () => {
    expect(pillFor('own_only')).toEqual({ tone: 'mute', match: 'own_only' })
  })
})

describe('readHeading', () => {
  it('returns the reading key with the file name only (K23: one sentence, no literal text here)', () => {
    expect(readHeading('loans_2026.csv')).toEqual({ key: 'place.reading', vars: { name: 'loans_2026.csv' } })
  })

  it('prefers the dataset label over the file name when returning from the wizard (dataset route)', () => {
    expect(readHeading('loans_2026.csv', '貸出の記録')).toEqual({
      key: 'place.reading',
      vars: { name: '貸出の記録' },
    })
  })

  it('falls back to the file name when no dataset label is given', () => {
    expect(readHeading('loans_2026.csv', null)).toEqual({
      key: 'place.reading',
      vars: { name: 'loans_2026.csv' },
    })
  })
})

describe('readSummary', () => {
  const matched: ShapeMatch = {
    type_id: 'type-loan',
    dialect: 'csv',
    matched_columns: ['title', 'branch'],
    unmatched_columns: ['note'],
    confidence: 0.67,
  }
  const unmatched: ShapeMatch = {
    type_id: null,
    dialect: 'csv',
    matched_columns: [],
    unmatched_columns: ['title', 'branch', 'note'],
    confidence: 0,
  }

  it('returns rows/cols + match fragments (no literal text) when a shape matched', () => {
    expect(readSummary({ rows: 28, columnCount: 3 }, matched, '貸出の記録')).toEqual([
      { key: 'place.rows_cols', vars: { rows: 28, cols: 3 } },
      { key: 'place.matched_type', vars: { type: '貸出の記録', matched: 2, total: 3 } },
      { key: 'place.no_design_needed' },
      { key: 'place.not_shelved_yet' },
    ])
  })

  it('returns only the no-match fragment when type_id is null (no invented match claim)', () => {
    expect(readSummary({ rows: 12, columnCount: 3 }, unmatched, null)).toEqual([
      { key: 'place.rows_cols', vars: { rows: 12, cols: 3 } },
      { key: 'place.no_match_title' },
    ])
  })
})

describe('footerSummary', () => {
  it('counts total / linked / summed rows deterministically', () => {
    const items: PlaceSubjectItem[] = [
      { value: 'a', rows: 16, match: 'linked' },
      { value: 'b', rows: 8, match: 'ambiguous' },
      { value: 'c', rows: 4, match: 'own_only' },
      { value: 'd', rows: 41191, match: 'linked' },
    ]
    expect(footerSummary(items)).toEqual({
      key: 'place.footer_summary',
      vars: { total: 4, linked: 2, facts: 41207 },
    })
  })

  it('is 0/0/0 for an empty list', () => {
    expect(footerSummary([])).toEqual({
      key: 'place.footer_summary',
      vars: { total: 0, linked: 0, facts: 0 },
    })
  })
})

describe('buildShapePreview (PR E §4: shape-mismatch preview)', () => {
  it('splits a comma header + first N rows, trimming cells', () => {
    const text = 'country, year, population\nJapan, 2005, 127773000\nBrazil, 2005, 186830000\n'
    expect(buildShapePreview(text)).toEqual({
      columns: ['country', 'year', 'population'],
      rows: [
        ['Japan', '2005', '127773000'],
        ['Brazil', '2005', '186830000'],
      ],
    })
  })

  it('caps at maxRows even when the file has more data rows', () => {
    const text = 'a,b\n1,2\n3,4\n5,6\n7,8\n'
    const preview = buildShapePreview(text, 2)
    expect(preview.columns).toEqual(['a', 'b'])
    expect(preview.rows).toEqual([
      ['1', '2'],
      ['3', '4'],
    ])
  })

  it('detects tab as the delimiter when it dominates the header', () => {
    const text = 'name\tcity\nAlice\tKyoto\n'
    expect(buildShapePreview(text)).toEqual({ columns: ['name', 'city'], rows: [['Alice', 'Kyoto']] })
  })

  it('ignores blank lines', () => {
    const text = 'a,b\n\n1,2\n\n'
    expect(buildShapePreview(text)).toEqual({ columns: ['a', 'b'], rows: [['1', '2']] })
  })

  it('returns empty columns/rows for empty text', () => {
    expect(buildShapePreview('')).toEqual({ columns: [], rows: [] })
  })

  it('returns an empty rows array when only a header line exists', () => {
    expect(buildShapePreview('a,b,c')).toEqual({ columns: ['a', 'b', 'c'], rows: [] })
  })
})

describe('isDefinitionGapValue (PR E §4: 事実の表の値なし判定)', () => {
  it('is true when value_iri equals property_iri (a definition gap constant)', () => {
    expect(isDefinitionGapValue('https://example.org/onto/rainfall', 'https://example.org/onto/rainfall')).toBe(
      true,
    )
  })

  it('is false when value_iri differs from property_iri (a real linked value)', () => {
    expect(isDefinitionGapValue('https://example.org/loanBranch/kyoto', 'https://example.org/onto/branch')).toBe(
      false,
    )
  })

  it('is false when value_iri is absent (a plain literal value, not an IRI)', () => {
    expect(isDefinitionGapValue(undefined, 'https://example.org/onto/rainfall')).toBe(false)
  })

  it('is false when value_iri is an empty string', () => {
    expect(isDefinitionGapValue('', '')).toBe(false)
  })

  it('is false when value_iri is not a string (defensive: unexpected shapes never mask real data)', () => {
    expect(isDefinitionGapValue(42, 42)).toBe(false)
  })
})
