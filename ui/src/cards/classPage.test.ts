import { describe, expect, it } from 'vitest'
import type { CardSpec, ClassSchema, SubjectItem } from './cardsApi'
import { buildMeasureCard, cardId } from './measureCardFields'
import { classViewpointRows, subjectsForClass } from './ClassPage'

// 架空の分野（図書館の貸出記録）で確かめる。

const BOOK = 'https://example.org/onto/Book'
const OTHER = 'https://example.org/onto/Author'

function schema(tools: unknown[]): ClassSchema {
  return { class_iri: BOOK, label: '本', dataset_id: 'ds-1', snapshot: null, properties: [], tools }
}

function card(over: Partial<CardSpec> & Pick<CardSpec, 'subject_key' | 'params' | 'title'>): CardSpec {
  return { card_id: cardId(over.params), tool: 'set_measure', output_kind: 'series', created_at: '2026-01-01T00:00:00.000Z', ...over }
}

describe('classViewpointRows', () => {
  it('同梱を先頭に、宣言順のまま並べる', () => {
    const s = schema([
      { name: 'by_year', title: '出版年の推移', output_kind: 'series' },
      { name: 'by_author', title: '著者ごとの冊数', output_kind: 'breakdown' },
    ])
    const rows = classViewpointRows(s, [])
    expect(rows).toEqual([
      { id: 'by_year', title: '出版年の推移', bundled: true, usedOn: 0 },
      { id: 'by_author', title: '著者ごとの冊数', bundled: true, usedOn: 0 },
    ])
  })

  it('使用中の観点は同梱のあとに、使用数の多い順→題名順で続く', () => {
    const s = schema([{ name: 'by_year', title: '出版年の推移', output_kind: 'series' }])
    const paramsA = buildMeasureCard({ classIri: BOOK, shape: 'facts', where: [], items: ['p1'] }).params
    const paramsB = buildMeasureCard({ classIri: BOOK, shape: 'quantity', where: [], item: 'p2', agg: 'avg' }).params
    const cards = [
      card({ subject_key: 's:x', params: paramsA, title: 'い' }),
      card({ subject_key: 's:x', params: paramsB, title: 'あ' }),
      card({ subject_key: 's:y', params: paramsB, title: 'あ' }),
    ]
    const rows = classViewpointRows(s, cards)
    expect(rows.map((r) => r.title)).toEqual(['出版年の推移', 'あ', 'い'])
    expect(rows[0]).toMatchObject({ bundled: true, usedOn: 0 })
    expect(rows[1]).toMatchObject({ bundled: false, usedOn: 2 })
    expect(rows[2]).toMatchObject({ bundled: false, usedOn: 1 })
  })

  it('この種類以外のカードは含めない', () => {
    const s = schema([])
    const otherParams = buildMeasureCard({ classIri: OTHER, shape: 'facts', where: [], items: ['p1'] }).params
    const rows = classViewpointRows(s, [card({ subject_key: 's:x', params: otherParams, title: '著者一覧' })])
    expect(rows).toEqual([])
  })
})

function subject(over: Partial<SubjectItem> & Pick<SubjectItem, 'subject_key' | 'id'>): SubjectItem {
  return {
    kind: 'individual',
    label: null,
    class_label: null,
    source: 'own',
    card_count: null,
    match: null,
    created_at: '2026-01-01T00:00:00.000Z',
    ...over,
  }
}

describe('subjectsForClass', () => {
  it('明示の class_iri で絞る', () => {
    const items = [
      { ...subject({ subject_key: 'i:a', id: 'a', label: 'A' }), class_iri: BOOK } as SubjectItem,
      { ...subject({ subject_key: 'i:b', id: 'b', label: 'B' }), class_iri: OTHER } as SubjectItem,
    ]
    expect(subjectsForClass(items, BOOK).map((s) => s.subject_key)).toEqual(['i:a'])
  })

  it('一覧（set）は spec.class で絞る', () => {
    const setItem = subject({
      subject_key: 's:x',
      id: 'x',
      kind: 'set',
      spec: { class: BOOK, where: [], order_by: null, limit: 20, source_scope: 'all' },
    })
    expect(subjectsForClass([setItem], BOOK).map((s) => s.subject_key)).toEqual(['s:x'])
    expect(subjectsForClass([setItem], OTHER)).toEqual([])
  })

  it('並びは入力の順のまま', () => {
    const a = { ...subject({ subject_key: 'i:a', id: 'a' }), class_iri: BOOK } as SubjectItem
    const b = { ...subject({ subject_key: 'i:b', id: 'b' }), class_iri: BOOK } as SubjectItem
    expect(subjectsForClass([b, a], BOOK).map((s) => s.subject_key)).toEqual(['i:b', 'i:a'])
  })
})
