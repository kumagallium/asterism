import { describe, expect, it } from 'vitest'
import type { SchemaProperty } from './cardsApi'
import type { ConverseProposal } from './cardsApi'
import { cardId, toCardSpec, type MeasureSchemaLike } from './measureCardFields'
import {
  buildPageSummary,
  labelsFromSchema,
  latestDraft,
  proposalCardSpec,
  summarizeCardRows,
  type PageCardForSummary,
  type PageChatTurn,
} from './pageChatThreads'

// 架空の分野（観測記録）で確かめる — measureCardFields.test.ts と同じ流儀。
const t = (key: string, options?: Record<string, unknown>) =>
  typeof options?.defaultValue === 'string' ? options.defaultValue : key

function prop(over: Partial<SchemaProperty> & Pick<SchemaProperty, 'iri' | 'label' | 'kind'>): SchemaProperty {
  return { datatype: null, unit: null, quantity_kind: null, column: null, distinct_count: null, ...over }
}

const OBSERVATION_SCHEMA: MeasureSchemaLike = {
  class_iri: 'https://example.org/onto/Observation',
  label: '観測記録',
  properties: [
    prop({ iri: 'https://example.org/onto/year', label: '年', kind: 'quantity', distinct_count: 40 }),
    prop({ iri: 'https://example.org/onto/count', label: '件数', kind: 'quantity', distinct_count: 40 }),
  ],
}

// ---------------------------------------------------------------------------
// pageSummary の作り方（先頭 20 行・series は先頭と末尾）
// ---------------------------------------------------------------------------

describe('summarizeCardRows', () => {
  it('series は先頭と末尾の 2 行だけ', () => {
    const rows = [{ x: 1 }, { x: 2 }, { x: 3 }, { x: 4 }]
    expect(summarizeCardRows('series', rows)).toEqual([{ x: 1 }, { x: 4 }])
  })

  it('series が 1 行しかなければその 1 行だけ', () => {
    expect(summarizeCardRows('series', [{ x: 1 }])).toEqual([{ x: 1 }])
  })

  it('series 以外は先頭 20 行に切る', () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({ i }))
    const out = summarizeCardRows('facts', rows)
    expect(out).toHaveLength(20)
    expect(out[0]).toEqual({ i: 0 })
    expect(out[19]).toEqual({ i: 19 })
  })

  it('空なら空のまま', () => {
    expect(summarizeCardRows('series', [])).toEqual([])
    expect(summarizeCardRows('facts', [])).toEqual([])
  })
})

describe('buildPageSummary', () => {
  it('facts はそのまま通し、カードごとに rows だけ間引く', () => {
    const facts = [{ label: '名前', value: 'あ' }]
    const cards: PageCardForSummary[] = [
      { title: '件数の推移', output_kind: 'series', rows: [{ y: 1 }, { y: 2 }, { y: 3 }] },
      { title: '一覧', output_kind: 'facts', rows: Array.from({ length: 25 }, (_, i) => ({ i })) },
    ]
    const summary = buildPageSummary(facts, cards)
    expect(summary.facts).toBe(facts)
    expect(summary.cards[0]).toEqual({ title: '件数の推移', output_kind: 'series', rows: [{ y: 1 }, { y: 3 }] })
    expect(summary.cards[1].rows).toHaveLength(20)
  })
})

// ---------------------------------------------------------------------------
// 提案の取り込み → 下書きの更新
// ---------------------------------------------------------------------------

function assistantTurn(id: string, proposal: ConverseProposal | null): PageChatTurn {
  return { id, role: 'assistant', at: 0, result: { reply: '', proposal } }
}

function userTurn(id: string, text: string): PageChatTurn {
  return { id, role: 'user', text, at: 0 }
}

const PROPOSAL_A: ConverseProposal = {
  params: { class: OBSERVATION_SCHEMA.class_iri, where: [], shape: 'series', x: 'https://example.org/onto/year', y: 'https://example.org/onto/count' },
  presentation: null,
  output_kind: 'series',
  title: '件数の推移',
}

describe('latestDraft', () => {
  it('一番あたらしい提案を下書きとして返す', () => {
    const turns: PageChatTurn[] = [userTurn('u1', '推移を出して'), assistantTurn('a1', PROPOSAL_A)]
    expect(latestDraft(turns, new Set())).toBe(PROPOSAL_A)
  })

  it('提案の無い応答は無視して、その前の提案を返す', () => {
    const turns: PageChatTurn[] = [
      userTurn('u1', '推移を出して'),
      assistantTurn('a1', PROPOSAL_A),
      userTurn('u2', 'いい感じ'),
      assistantTurn('a2', null),
    ]
    expect(latestDraft(turns, new Set())).toBe(PROPOSAL_A)
  })

  it('足した／やめた提案は下書きに数えない', () => {
    const turns: PageChatTurn[] = [userTurn('u1', '推移を出して'), assistantTurn('a1', PROPOSAL_A)]
    expect(latestDraft(turns, new Set(['a1']))).toBeNull()
  })

  it('提案が 1 つも無ければ null', () => {
    const turns: PageChatTurn[] = [userTurn('u1', 'こんにちは'), assistantTurn('a1', null)]
    expect(latestDraft(turns, new Set())).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 足すときの CardSpec が F4（NewCardForm）と同じ id になる
// ---------------------------------------------------------------------------

describe('labelsFromSchema', () => {
  it('property IRI を schema の label に置き換える', () => {
    const labels = labelsFromSchema(OBSERVATION_SCHEMA, PROPOSAL_A.params)
    expect(labels).toEqual({ x: '年', y: '件数', item: undefined, category: undefined, items: undefined, agg: undefined })
  })

  it('schema が無ければ IRI をそのまま安全側で返す', () => {
    const labels = labelsFromSchema(null, PROPOSAL_A.params)
    expect(labels.x).toBe('https://example.org/onto/year')
  })
})

describe('proposalCardSpec', () => {
  it('F4 の toCardSpec と同じ card_id になる（同じ params を渡すので）', () => {
    const labels = labelsFromSchema(OBSERVATION_SCHEMA, PROPOSAL_A.params)
    const subjectKey = 'k:https://example.org/onto/Observation'
    const viaChat = proposalCardSpec(PROPOSAL_A, labels, subjectKey, t)
    const viaForm = toCardSpec(
      { classIri: OBSERVATION_SCHEMA.class_iri, shape: 'series', where: [], x: PROPOSAL_A.params.x as string, y: PROPOSAL_A.params.y as string },
      labels,
      subjectKey,
      t,
    )
    expect(viaChat).not.toBeNull()
    expect(viaChat?.card_id).toBe(viaForm.card_id)
    expect(viaChat?.card_id).toBe(cardId(PROPOSAL_A.params as Parameters<typeof cardId>[0]))
    expect(viaChat?.title).toBe(viaForm.title)
  })

  it('サーバの語彙に無い output_kind は null（「足す」を出さない）', () => {
    const bogus: ConverseProposal = { ...PROPOSAL_A, output_kind: 'not-a-shape' }
    expect(proposalCardSpec(bogus, {}, 'k:x', t)).toBeNull()
  })
})
