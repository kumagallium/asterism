import { describe, expect, it } from 'vitest'
import type { CardSpec, CardView, SchemaProperty } from './cardsApi'
import type { ConverseProposal } from './cardsApi'
import { normalizeCardView, normalizeConverseProposal, normalizeConverseProposalView } from './cardsApi'
import { cardId, toCardSpec, type MeasureSchemaLike } from './measureCardFields'
import { injectVegaLiteData, resolveViewSourceCard, viewCardId, type PageChatPageSummary } from './PageChatDrawer'
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

// ---------------------------------------------------------------------------
// PR F13 §1: AI が書いた見せ方（kind: 'view'）
// ---------------------------------------------------------------------------

const VEGA_VIEW: CardView = {
  lang: 'vega-lite',
  spec: { mark: 'bar', encoding: { x: { field: 'year', type: 'ordinal' } } },
  source_card_id: 'card-abc0000000000001',
  custom: true,
}

describe('viewCardId', () => {
  it('同じ元カード・同じ view からは常に同じ id（決定論）', () => {
    const a = viewCardId('card-abc0000000000001', VEGA_VIEW)
    const b = viewCardId('card-abc0000000000001', { ...VEGA_VIEW })
    expect(a).toBe(b)
  })

  it('view が違えば id も違う', () => {
    const other: CardView = { ...VEGA_VIEW, spec: { ...(VEGA_VIEW.spec as Record<string, unknown>), mark: 'line' } }
    expect(viewCardId('card-abc0000000000001', VEGA_VIEW)).not.toBe(viewCardId('card-abc0000000000001', other))
  })

  it('元カードが違えば id も違う', () => {
    expect(viewCardId('card-abc0000000000001', VEGA_VIEW)).not.toBe(viewCardId('card-other0000000001', VEGA_VIEW))
  })
})

describe('injectVegaLiteData', () => {
  it('data.values だけを差し込み、他のキーは変えない', () => {
    const rows = [{ year: 2020, count: 3 }]
    const spec = { mark: 'bar', encoding: { x: { field: 'year' } } }
    const injected = injectVegaLiteData(spec, rows)
    expect(injected.mark).toBe('bar')
    expect(injected.encoding).toBe(spec.encoding)
    expect(injected.data).toEqual({ values: rows })
  })

  it('AI が data に他のキー（url 等）を書いていても values で上書きする', () => {
    const rows = [{ x: 1 }]
    const spec = { mark: 'point', data: { url: 'https://evil.example/data.json' } }
    const injected = injectVegaLiteData(spec, rows)
    expect(injected.data).toEqual({ url: 'https://evil.example/data.json', values: rows })
    // values はサーバ／UI が差し込んだ rows そのもの — AI の JSON からは来ない。
    expect((injected.data as { values: unknown[] }).values).toBe(rows)
  })
})

// ---------------------------------------------------------------------------
// PR F13 穴埋め: 既定カード（まだ「足す」を押していないカード）を元にした
// view 提案の解決（`resolveViewSourceCard`）
// ---------------------------------------------------------------------------

describe('resolveViewSourceCard', () => {
  const pageCards: PageChatPageSummary['cards'] = [
    {
      card_id: 'card-default-0001',
      title: '観測記録の推移',
      output_kind: 'series',
      rows: [{ year: 2020, count: 3 }],
      tool: 'set_measure',
      params: { class: 'https://example.org/onto/Observation', where: [], shape: 'series' },
    },
  ]

  it('pageSummary.cards にあれば、まだ足していない既定カードでも解決できる', () => {
    const resolved = resolveViewSourceCard('card-default-0001', pageCards, [])
    expect(resolved).toEqual({
      card_id: 'card-default-0001',
      title: '観測記録の推移',
      tool: 'set_measure',
      params: { class: 'https://example.org/onto/Observation', where: [], shape: 'series' },
      output_kind: 'series',
    })
  })

  it('pageSummary.cards に無くても cardStore の足したカードから解決する', () => {
    const added: CardSpec[] = [
      {
        card_id: 'card-added-0001',
        subject_key: 'k:https://example.org/onto/Observation',
        tool: 'set_measure',
        params: { class: 'https://example.org/onto/Observation', where: [], shape: 'ranked' },
        title: '足したカード',
        output_kind: 'ranked',
        created_at: '2026-01-01T00:00:00Z',
      },
    ]
    const resolved = resolveViewSourceCard('card-added-0001', [], added)
    expect(resolved).toEqual({
      card_id: 'card-added-0001',
      title: '足したカード',
      tool: 'set_measure',
      params: { class: 'https://example.org/onto/Observation', where: [], shape: 'ranked' },
      output_kind: 'ranked',
    })
  })

  it('pageSummary.cards の params が省かれていれば（2KB 超で送信元が落とした） cardStore へ探しにいく', () => {
    const pageCardsNoParams: PageChatPageSummary['cards'] = [
      { card_id: 'card-x', title: 'x', output_kind: 'series', rows: [], tool: 'set_measure' },
    ]
    const added: CardSpec[] = [
      {
        card_id: 'card-x',
        subject_key: 'k:x',
        tool: 'set_measure',
        params: { class: 'https://example.org/onto/Observation', where: [], shape: 'series' },
        title: 'x',
        output_kind: 'series',
        created_at: '2026-01-01T00:00:00Z',
      },
    ]
    expect(resolveViewSourceCard('card-x', pageCardsNoParams, added)?.params).toEqual({
      class: 'https://example.org/onto/Observation',
      where: [],
      shape: 'series',
    })
  })

  it('pageSummary.cards にも cardStore にも無ければ見つからない', () => {
    expect(resolveViewSourceCard('card-nowhere', pageCards, [])).toBeUndefined()
  })
})

describe('normalizeCardView', () => {
  it('3 言語のどれかで、source_card_id が文字列で、custom が true なら通す', () => {
    expect(normalizeCardView(VEGA_VIEW)).toEqual(VEGA_VIEW)
  })

  it('mermaid は text が文字列であることを要求する（spec ではない）', () => {
    const mermaid: CardView = { lang: 'mermaid', text: 'graph LR\n  A --> B', source_card_id: 'card-x', custom: true }
    expect(normalizeCardView(mermaid)).toEqual(mermaid)
    expect(normalizeCardView({ ...mermaid, text: undefined })).toBeUndefined()
    expect(normalizeCardView({ lang: 'mermaid', spec: { not: 'text' }, source_card_id: 'card-x', custom: true })).toBeUndefined()
  })

  it('不正な lang は捨てる', () => {
    expect(normalizeCardView({ ...VEGA_VIEW, lang: 'sql' })).toBeUndefined()
  })

  it('source_card_id が文字列でなければ捨てる', () => {
    expect(normalizeCardView({ ...VEGA_VIEW, source_card_id: 42 })).toBeUndefined()
  })

  it('custom が true でなければ捨てる', () => {
    expect(normalizeCardView({ ...VEGA_VIEW, custom: false })).toBeUndefined()
  })

  it('形そのものが違えば捨てる', () => {
    expect(normalizeCardView(null)).toBeUndefined()
    expect(normalizeCardView('not an object')).toBeUndefined()
  })
})

describe('normalizeConverseProposalView', () => {
  it('サーバのワイヤ形（custom を持たない）も通す', () => {
    const wire = { lang: 'vega-lite', spec: VEGA_VIEW.spec, source_card_id: VEGA_VIEW.source_card_id }
    expect(normalizeConverseProposalView(wire)).toEqual(wire)
  })

  it('mermaid は text（サーバは spec を返さない）', () => {
    const wire = { lang: 'mermaid', text: 'graph LR\n  A --> B', source_card_id: 'card-x' }
    expect(normalizeConverseProposalView(wire)).toEqual(wire)
  })

  it('不正な lang は捨てる', () => {
    expect(normalizeConverseProposalView({ lang: 'sql', spec: {}, source_card_id: 'card-x' })).toBeUndefined()
  })
})

describe('normalizeConverseProposal', () => {
  it('kind: view はサーバの実際の応答形（params/output_kind/title 無し）でも null に潰れない', () => {
    // converse_prompt.py の _validate_view_proposal は `{kind, view}` だけを返す
    // （params/output_kind/title を持たない）。ここでこの形を落とすと F13 の
    // 「書く」提案が UI 上で一度も発火しない（checker 指摘の再現）。
    const raw = {
      kind: 'view',
      view: { lang: 'vega-lite', spec: { mark: 'bar', encoding: {} }, source_card_id: 'card-x' },
    }
    const proposal = normalizeConverseProposal(raw)
    expect(proposal).not.toBeNull()
    expect(proposal?.kind).toBe('view')
    expect(proposal?.view).toEqual(raw.view)
  })

  it('kind: view で view の形が崩れていれば null', () => {
    expect(normalizeConverseProposal({ kind: 'view', view: { lang: 'sql', spec: {}, source_card_id: 'x' } })).toBeNull()
    expect(normalizeConverseProposal({ kind: 'view' })).toBeNull()
  })

  it('kind 省略（measure）は従来どおり params/output_kind/title が必須', () => {
    expect(normalizeConverseProposal({ params: { class: 'x' }, output_kind: 'measure', title: 'a' })?.kind).toBe('measure')
    expect(normalizeConverseProposal({ output_kind: 'measure', title: 'a' })).toBeNull()
  })
})
