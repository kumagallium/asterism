import { describe, expect, it } from 'vitest'
import { footerSummary, pillFor, readHeading, readSummary, type PlaceSubjectItem, type ShapeMatch } from './placeShape'

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
