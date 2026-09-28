import { describe, expect, it } from 'vitest'
import type { ClassSchemaLike } from './setFormFields'
import { buildSetFormSpec, SET_FORM_DEFAULT_LIMIT, SET_FORM_MAX_LIMIT } from './setFormFields'
import type { SchemaProperty } from './cardsApi'

// 架空の 2 分野（図書館の貸出／気象観測）で確かめる。buildSetFormSpec は
// kind だけを見て組み立てる決定論関数 — 分野語の辞書は持たない。

function prop(over: Partial<SchemaProperty> & Pick<SchemaProperty, 'iri' | 'label' | 'kind'>): SchemaProperty {
  return { datatype: null, unit: null, quantity_kind: null, column: null, distinct_count: null, ...over }
}

const LIBRARY_SCHEMA: ClassSchemaLike = {
  class_iri: 'https://example.org/onto/Checkout',
  label: '貸出',
  properties: [
    prop({ iri: 'https://example.org/onto/pages', label: 'ページ数', kind: 'quantity', unit: null }),
    prop({ iri: 'https://example.org/onto/loanDays', label: '貸出日数', kind: 'quantity', unit: 'unit:DAY' }),
    prop({ iri: 'https://example.org/onto/genre', label: 'ジャンル', kind: 'category', distinct_count: 4 }),
    prop({ iri: 'https://example.org/onto/summary', label: 'あらすじ', kind: 'text', distinct_count: 900 }),
    prop({ iri: 'https://example.org/onto/isbn', label: 'ISBN', kind: 'identifier' }),
    prop({ iri: 'https://example.org/onto/branch', label: '貸出館', kind: 'link' }),
  ],
}

const WEATHER_SCHEMA: ClassSchemaLike = {
  class_iri: 'https://example.org/onto/Observation',
  label: '観測記録',
  properties: [
    prop({ iri: 'https://example.org/onto/rainfall', label: '降水量', kind: 'quantity', unit: 'unit:MilliM' }),
    prop({ iri: 'https://example.org/onto/station', label: '観測地点', kind: 'category', distinct_count: 12 }),
  ],
}

describe('buildSetFormSpec', () => {
  it('quantity は filters と orderOptions の両方に入る（gt/lt/between）', () => {
    const spec = buildSetFormSpec(LIBRARY_SCHEMA)
    const pages = spec.filters.find((f) => f.property === 'https://example.org/onto/pages')
    expect(pages).toEqual({
      kind: 'quantity',
      property: 'https://example.org/onto/pages',
      label: 'ページ数',
      unit: null,
      ops: ['gt', 'lt', 'between'],
    })
    const loanDays = spec.filters.find((f) => f.property === 'https://example.org/onto/loanDays')
    expect(loanDays).toEqual({
      kind: 'quantity',
      property: 'https://example.org/onto/loanDays',
      label: '貸出日数',
      unit: 'unit:DAY',
      ops: ['gt', 'lt', 'between'],
    })
    expect(spec.orderOptions).toEqual([
      { property: 'https://example.org/onto/pages', label: 'ページ数', unit: null },
      { property: 'https://example.org/onto/loanDays', label: '貸出日数', unit: 'unit:DAY' },
    ])
  })

  it('category は filters に入るが orderOptions には入らない（並べ方は quantity のみ）', () => {
    const spec = buildSetFormSpec(LIBRARY_SCHEMA)
    const genre = spec.filters.find((f) => f.property === 'https://example.org/onto/genre')
    expect(genre).toEqual({ kind: 'category', property: 'https://example.org/onto/genre', label: 'ジャンル' })
    expect(spec.orderOptions.some((o) => o.property === 'https://example.org/onto/genre')).toBe(false)
  })

  it('identifier / text / link は Phase 2 — filters にも orderOptions にも出ない', () => {
    const spec = buildSetFormSpec(LIBRARY_SCHEMA)
    const excludedIris = [
      'https://example.org/onto/summary',
      'https://example.org/onto/isbn',
      'https://example.org/onto/branch',
    ]
    for (const iri of excludedIris) {
      expect(spec.filters.some((f) => f.property === iri)).toBe(false)
      expect(spec.orderOptions.some((o) => o.property === iri)).toBe(false)
    }
    expect(spec.filters).toHaveLength(3) // pages, loanDays, genre
  })

  it('filters は schema.properties の並び順のまま（決定論・並べ替えない）', () => {
    const spec = buildSetFormSpec(LIBRARY_SCHEMA)
    expect(spec.filters.map((f) => f.property)).toEqual([
      'https://example.org/onto/pages',
      'https://example.org/onto/loanDays',
      'https://example.org/onto/genre',
    ])
  })

  it('既定の件数と上限を持つ（契約メモ §6.3「上限 20」）', () => {
    const spec = buildSetFormSpec(LIBRARY_SCHEMA)
    expect(spec.defaultLimit).toBe(SET_FORM_DEFAULT_LIMIT)
    expect(spec.maxLimit).toBe(SET_FORM_MAX_LIMIT)
    expect(spec.defaultLimit).toBe(20)
    expect(spec.maxLimit).toBe(20)
  })

  it('2 つ目の分野（気象観測）でも同じ規則で組める', () => {
    const spec = buildSetFormSpec(WEATHER_SCHEMA)
    expect(spec.filters).toEqual([
      {
        kind: 'quantity',
        property: 'https://example.org/onto/rainfall',
        label: '降水量',
        unit: 'unit:MilliM',
        ops: ['gt', 'lt', 'between'],
      },
      { kind: 'category', property: 'https://example.org/onto/station', label: '観測地点' },
    ])
    expect(spec.orderOptions).toEqual([
      { property: 'https://example.org/onto/rainfall', label: '降水量', unit: 'unit:MilliM' },
    ])
  })

  it('properties が空なら filters も orderOptions も空（同じ入力→同じ出力・入力を書き換えない）', () => {
    const empty: ClassSchemaLike = { class_iri: 'https://example.org/onto/Empty', label: '空', properties: [] }
    const spec1 = buildSetFormSpec(empty)
    const spec2 = buildSetFormSpec(empty)
    expect(spec1).toEqual({ filters: [], orderOptions: [], defaultLimit: 20, maxLimit: 20 })
    expect(spec1).toEqual(spec2)
    expect(empty.properties).toEqual([])
  })
})
