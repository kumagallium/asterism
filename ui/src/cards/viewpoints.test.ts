import { describe, expect, it } from 'vitest'
import type { CardSpec, ClassSchema, LinkingKind, MeasureCardParams } from './cardsApi'
import { buildMeasureCard, cardId } from './measureCardFields'
import {
  applicableViewpoints,
  declaredViewpoints,
  paramsForPage,
  pathLabel,
  viewpointId,
  viewpointsFrom,
  viewpointTitle,
  type ViewpointPage,
} from './viewpoints'

// 架空の分野（観測記録・観測所）で確かめる。viewpoints.ts は params の形と
// class_iri しか見ない決定論関数 — 分野語の辞書は持たない。

const OBS_CLASS = 'https://example.org/onto/Observation'
const STATION_CLASS = 'https://example.org/onto/Station'
const YEAR = 'https://example.org/onto/year'
const RAINFALL = 'https://example.org/onto/rainfall'
const ELEVATION = 'https://example.org/onto/elevation'
const STATION_PROP = 'https://example.org/onto/station'

const t = (key: string, options?: Record<string, unknown>) => (options?.defaultValue as string | undefined) ?? key

function seriesParams(where: MeasureCardParams['where']): MeasureCardParams {
  return buildMeasureCard({ classIri: OBS_CLASS, shape: 'series', where, x: YEAR, y: RAINFALL }).params
}

function card(over: Partial<CardSpec> & Pick<CardSpec, 'subject_key' | 'params' | 'title'>): CardSpec {
  return {
    card_id: cardId(over.params),
    tool: 'set_measure',
    output_kind: 'series',
    created_at: '2026-01-01T00:00:00.000Z',
    ...over,
  }
}

describe('viewpointId', () => {
  it('where だけが違う params は同じ id になる', () => {
    const a = seriesParams([{ property: STATION_PROP, op: 'eq', value: '1' }])
    const b = seriesParams([{ property: STATION_PROP, op: 'eq', value: '2' }])
    expect(viewpointId(a)).toBe(viewpointId(b))
  })

  it('shape/項目が違えば id も違う', () => {
    const a = seriesParams([])
    const b = buildMeasureCard({ classIri: OBS_CLASS, shape: 'series', where: [], x: YEAR, y: ELEVATION }).params
    expect(viewpointId(a)).not.toBe(viewpointId(b))
  })
})

describe('viewpointsFrom', () => {
  const paramsA = seriesParams([{ property: STATION_PROP, op: 'eq', value: '1' }])
  const paramsB = seriesParams([{ property: STATION_PROP, op: 'eq', value: '2' }])
  const cardOnPage1 = card({ subject_key: 's:page-1', params: paramsA, title: '降水量の推移' })
  const cardOnPage2 = card({ subject_key: 's:page-2', params: paramsB, title: '降水量の推移' })

  it('where 違いの 2 枚のカードは 1 つの観点にまとまり、使用ページ数は 2', () => {
    const viewpoints = viewpointsFrom([cardOnPage1, cardOnPage2])
    expect(viewpoints).toHaveLength(1)
    expect(viewpoints[0].usedOn).toBe(2)
    expect(viewpoints[0].class).toBe(OBS_CLASS)
    expect(viewpoints[0].title).toBe('降水量の推移')
    // params には where が含まれない（ページごとに条件付けし直すため）。
    expect(viewpoints[0].params.where).toBeUndefined()
  })

  it('入力の並び順に依存しない（決定論）', () => {
    const forward = viewpointsFrom([cardOnPage1, cardOnPage2])
    const backward = viewpointsFrom([cardOnPage2, cardOnPage1])
    expect(forward).toEqual(backward)
  })

  it('同じページ（subject_key）で 2 度観測されても使用ページ数は増えない', () => {
    const dup = card({ subject_key: 's:page-1', params: paramsA, title: '降水量の推移' })
    const viewpoints = viewpointsFrom([cardOnPage1, dup])
    expect(viewpoints[0].usedOn).toBe(1)
  })

  it('順は使用数の多い順→題名順（決定論）', () => {
    const popular = card({ subject_key: 's:page-1', params: paramsA, title: 'あ' })
    const popular2 = card({ subject_key: 's:page-2', params: paramsA, title: 'あ' })
    const rareA = card({
      subject_key: 's:page-3',
      params: buildMeasureCard({ classIri: OBS_CLASS, shape: 'facts', where: [], items: [YEAR] }).params,
      title: 'い',
    })
    const rareB = card({
      subject_key: 's:page-3',
      params: buildMeasureCard({ classIri: OBS_CLASS, shape: 'quantity', where: [], item: ELEVATION, agg: 'avg' }).params,
      title: 'う',
    })
    const viewpoints = viewpointsFrom([rareB, rareA, popular2, popular])
    expect(viewpoints.map((v) => v.title)).toEqual(['あ', 'い', 'う'])
  })
})

describe('applicableViewpoints', () => {
  const seriesVp = viewpointsFrom([card({ subject_key: 's:x', params: seriesParams([]), title: '降水量の推移' })])[0]
  const quantityVp = viewpointsFrom([
    card({
      subject_key: 's:x',
      params: buildMeasureCard({ classIri: OBS_CLASS, shape: 'quantity', where: [], item: ELEVATION, agg: 'avg' }).params,
      title: '標高の平均',
      output_kind: 'quantity',
    }),
  ])[0]
  const factsVp = viewpointsFrom([
    card({
      subject_key: 's:x',
      params: buildMeasureCard({ classIri: OBS_CLASS, shape: 'facts', where: [], items: [YEAR, RAINFALL] }).params,
      title: '年・降水量',
      output_kind: 'facts',
    }),
  ])[0]

  it('一覧のページ: spec.class と同じ観点だけを返す', () => {
    const page: ViewpointPage = { kind: 'set', classIri: OBS_CLASS, existingViewpointIds: [] }
    expect(applicableViewpoints([seriesVp, quantityVp], page).map((v) => v.id)).toEqual([seriesVp.id, quantityVp.id])
    const other: ViewpointPage = { kind: 'set', classIri: STATION_CLASS, existingViewpointIds: [] }
    expect(applicableViewpoints([seriesVp], other)).toEqual([])
  })

  it('一覧のページ: 既にある観点は出さない', () => {
    const page: ViewpointPage = { kind: 'set', classIri: OBS_CLASS, existingViewpointIds: [seriesVp.id] }
    expect(applicableViewpoints([seriesVp, quantityVp], page).map((v) => v.id)).toEqual([quantityVp.id])
  })

  it('1 件のページ: 自身の種類と一致する観点は「数字 1 つ」「表」だけ通す', () => {
    const page: ViewpointPage = { kind: 'individual', classIri: OBS_CLASS, existingViewpointIds: [] }
    const result = applicableViewpoints([seriesVp, quantityVp, factsVp], page)
    expect(result.map((v) => v.id).sort()).toEqual([quantityVp.id, factsVp.id].sort())
  })

  it('1 件のページ: linkingKinds に含まれる種類は見せ方を問わず通す', () => {
    const linking: LinkingKind[] = [
      {
        class_iri: OBS_CLASS,
        class_label: '観測記録',
        property: STATION_PROP,
        property_label: '観測所',
        count: 3,
        where: [{ property: STATION_PROP, iri: 'https://example.org/data/station-1' }],
      },
    ]
    const page: ViewpointPage = { kind: 'individual', classIri: STATION_CLASS, linkingKinds: linking, existingViewpointIds: [] }
    expect(applicableViewpoints([seriesVp, quantityVp, factsVp], page).map((v) => v.id).sort()).toEqual(
      [seriesVp.id, quantityVp.id, factsVp.id].sort(),
    )
  })

  it('1 件のページ: 自身の種類とも linkingKinds とも一致しなければ出さない', () => {
    const page: ViewpointPage = { kind: 'individual', classIri: STATION_CLASS, existingViewpointIds: [] }
    expect(applicableViewpoints([seriesVp], page)).toEqual([])
  })
})

describe('paramsForPage', () => {
  const seriesVp = viewpointsFrom([card({ subject_key: 's:x', params: seriesParams([]), title: '降水量の推移' })])[0]

  it('一覧のページ: このページの spec.where と source_scope を付ける', () => {
    const where: MeasureCardParams['where'] = [{ property: STATION_PROP, op: 'eq', value: '9' }]
    const page: ViewpointPage = { kind: 'set', classIri: OBS_CLASS, where, sourceScope: 'own', existingViewpointIds: [] }
    const params = paramsForPage(seriesVp, page)
    expect(params?.where).toEqual(where)
    expect(params?.source_scope).toBe('own')
  })

  it('1 件のページ: linkingKinds の該当する種類の where（完成形）をそのまま付ける', () => {
    const linking: LinkingKind[] = [
      {
        class_iri: OBS_CLASS,
        class_label: '観測記録',
        property: STATION_PROP,
        property_label: '観測所',
        count: 3,
        where: [{ property: STATION_PROP, iri: 'https://example.org/data/station-1' }],
      },
    ]
    const page: ViewpointPage = {
      kind: 'individual',
      classIri: STATION_CLASS,
      linkingKinds: linking,
      iri: 'https://example.org/data/station-1',
      existingViewpointIds: [],
    }
    const params = paramsForPage(seriesVp, page)
    expect(params?.where).toEqual([{ property: STATION_PROP, iri: 'https://example.org/data/station-1' }])
  })

  it('1 件のページ: 2 段の where（via）もそのまま付ける', () => {
    const linking: LinkingKind[] = [
      {
        class_iri: OBS_CLASS,
        class_label: '観測記録',
        property: 'https://example.org/onto/measuredBy',
        property_label: '計測',
        count: 4,
        path_kind: 'sibling_child',
        where: [
          {
            property: 'https://example.org/onto/measuredBy',
            via: { property: STATION_PROP, iri: 'https://example.org/data/station-1' },
          },
        ],
      },
    ]
    const page: ViewpointPage = {
      kind: 'individual',
      classIri: STATION_CLASS,
      linkingKinds: linking,
      iri: 'https://example.org/data/station-1',
      existingViewpointIds: [],
    }
    const params = paramsForPage(seriesVp, page)
    expect(params?.where).toEqual([
      {
        property: 'https://example.org/onto/measuredBy',
        via: { property: STATION_PROP, iri: 'https://example.org/data/station-1' },
      },
    ])
  })

  it('1 件のページ: 観点の class が自身の種類と一致し linkingKinds に候補が無いときは null（無条件の全体集計を装わない）', () => {
    const quantityVp = viewpointsFrom([
      card({
        subject_key: 's:x',
        params: buildMeasureCard({ classIri: OBS_CLASS, shape: 'quantity', where: [], item: ELEVATION, agg: 'avg' }).params,
        title: '標高の平均',
        output_kind: 'quantity',
      }),
    ])[0]
    const page: ViewpointPage = { kind: 'individual', classIri: OBS_CLASS, iri: 'https://example.org/data/obs-1', existingViewpointIds: [] }
    expect(paramsForPage(quantityVp, page)).toBeNull()
  })

  it('1 件のページ: 候補が複数あるときは最初の 1 つ', () => {
    const linking: LinkingKind[] = [
      {
        class_iri: OBS_CLASS,
        class_label: '観測記録',
        property: 'https://example.org/onto/observedAt',
        property_label: '観測日',
        count: 5,
        where: [{ property: 'https://example.org/onto/observedAt', iri: 'https://example.org/data/station-1' }],
      },
      {
        class_iri: OBS_CLASS,
        class_label: '観測記録',
        property: STATION_PROP,
        property_label: '観測所',
        count: 3,
        where: [{ property: STATION_PROP, iri: 'https://example.org/data/station-1' }],
      },
    ]
    const page: ViewpointPage = {
      kind: 'individual',
      classIri: STATION_CLASS,
      linkingKinds: linking,
      iri: 'https://example.org/data/station-1',
      existingViewpointIds: [],
    }
    const params = paramsForPage(seriesVp, page)
    expect(params?.where).toEqual([{ property: 'https://example.org/onto/observedAt', iri: 'https://example.org/data/station-1' }])
  })
})

describe('viewpointTitle', () => {
  it('カードの題名そのまま', () => {
    const vp = viewpointsFrom([card({ subject_key: 's:x', params: seriesParams([]), title: '降水量の推移' })])[0]
    expect(viewpointTitle(vp, t)).toBe('降水量の推移')
  })
})

function schema(tools: unknown[]): ClassSchema {
  return {
    class_iri: OBS_CLASS,
    label: '観測記録',
    dataset_id: 'ds-1',
    snapshot: null,
    properties: [],
    tools,
  }
}

describe('declaredViewpoints', () => {
  it('name/title/output_kind から同梱の観点を作る（宣言順のまま）', () => {
    const s = schema([
      { name: 'rainfall_by_year', title: '降水量の推移', output_kind: 'series' },
      { name: 'counts_by_kind', title: '種類別の件数', output_kind: 'breakdown' },
    ])
    expect(declaredViewpoints(s)).toEqual([
      { id: 'rainfall_by_year', title: '降水量の推移', shape: 'series', bundled: true },
      { id: 'counts_by_kind', title: '種類別の件数', shape: 'breakdown', bundled: true },
    ])
  })

  it('name/title が文字列でない項目は落とす', () => {
    const s = schema([{ name: 'ok_tool', title: '使える', output_kind: 'facts' }, { title: '名前なし' }, { name: 123, title: '数字の名前' }])
    expect(declaredViewpoints(s).map((v) => v.id)).toEqual(['ok_tool'])
  })

  it('output_kind が既知の見せ方でなければ shape を省く', () => {
    const s = schema([{ name: 'flow_tool', title: '手順', output_kind: 'flow' }])
    expect(declaredViewpoints(s)[0].shape).toBeUndefined()
  })
})

describe('cardId の一致（F4 のフォームと同じ規則）', () => {
  it('一覧のページ: paramsForPage の結果から作る card_id が、同じ選択を NewCardForm でしたときと同じになる', () => {
    const seriesVp = viewpointsFrom([card({ subject_key: 's:x', params: seriesParams([]), title: '降水量の推移' })])[0]
    const where: MeasureCardParams['where'] = [{ property: STATION_PROP, op: 'eq', value: '9' }]
    const page: ViewpointPage = { kind: 'set', classIri: OBS_CLASS, where, existingViewpointIds: [] }
    const viaViewpoint = paramsForPage(seriesVp, page)
    const viaForm = buildMeasureCard({ classIri: OBS_CLASS, shape: 'series', where, x: YEAR, y: RAINFALL }).params
    expect(viaViewpoint).not.toBeNull()
    expect(cardId(viaViewpoint as MeasureCardParams)).toBe(cardId(viaForm))
  })

  it('1 件のページ: linkingKinds 条件付けの card_id が NewCardForm の chosenKind 選択と同じになる', () => {
    const factsVp = viewpointsFrom([
      card({
        subject_key: 's:x',
        params: buildMeasureCard({ classIri: OBS_CLASS, shape: 'facts', where: [], items: [YEAR, RAINFALL] }).params,
        title: '年・降水量',
      }),
    ])[0]
    const linking: LinkingKind[] = [
      {
        class_iri: OBS_CLASS,
        class_label: '観測記録',
        property: STATION_PROP,
        property_label: '観測所',
        count: 3,
        where: [{ property: STATION_PROP, iri: 'https://example.org/data/station-1' }],
      },
    ]
    const page: ViewpointPage = {
      kind: 'individual',
      classIri: STATION_CLASS,
      linkingKinds: linking,
      iri: 'https://example.org/data/station-1',
      existingViewpointIds: [],
    }
    const viaViewpoint = paramsForPage(factsVp, page)
    const viaForm = buildMeasureCard({
      classIri: OBS_CLASS,
      shape: 'facts',
      where: [{ property: STATION_PROP, iri: 'https://example.org/data/station-1' }],
      items: [YEAR, RAINFALL],
    }).params
    expect(viaViewpoint).not.toBeNull()
    expect(cardId(viaViewpoint as MeasureCardParams)).toBe(cardId(viaForm))
  })
})

describe('pathLabel', () => {
  // 呼ばれた i18n キーと変数をそのまま確かめられるよう、翻訳文そのものではなく
  // `key:{"var":"val"}` の形で返すだけの t（describe 冒頭の `t` は defaultValue
  // をそのまま通すだけで、ここでの検証には使えない）。
  const echoT = (key: string, options?: Record<string, unknown>) => `${key}:${JSON.stringify(options ?? {})}`

  function kind(over: Partial<LinkingKind>): LinkingKind {
    return {
      class_iri: OBS_CLASS,
      class_label: '観測記録',
      property: STATION_PROP,
      property_label: '観測所',
      count: 1,
      where: [{ property: STATION_PROP, iri: 'https://example.org/data/station-1' }],
      ...over,
    }
  }

  it('path_kind が無ければ何も添えない', () => {
    expect(pathLabel(kind({}), echoT)).toBeUndefined()
  })

  it('direct: 変数なしのキー', () => {
    expect(pathLabel(kind({ path_kind: 'direct' }), echoT)).toBe('cards:newcard.path_direct:{}')
  })

  it('child_child: via.class_label を渡す', () => {
    const k = kind({
      path_kind: 'child_child',
      via: { property: 'https://example.org/onto/detail', property_label: '詳細', class_label: '観測明細' },
    })
    expect(pathLabel(k, echoT)).toBe('cards:newcard.path_child_child:{"via":"観測明細"}')
  })

  it('sibling: anchor_label があればそれを使う', () => {
    const k = kind({ path_kind: 'sibling', anchor_label: '観測所A', anchor_class_label: '観測所' })
    expect(pathLabel(k, echoT)).toBe('cards:newcard.path_sibling:{"anchor":"観測所A"}')
  })

  it('sibling: anchor_label が無ければ anchor_class_label に落ちる', () => {
    const k = kind({ path_kind: 'sibling', anchor_label: null, anchor_class_label: '観測所' })
    expect(pathLabel(k, echoT)).toBe('cards:newcard.path_sibling:{"anchor":"観測所"}')
  })

  it('sibling_child: anchor と via の両方を渡す', () => {
    const k = kind({
      path_kind: 'sibling_child',
      anchor_label: '観測所A',
      via: { property: 'https://example.org/onto/detail', property_label: '詳細', class_label: '観測明細' },
    })
    expect(pathLabel(k, echoT)).toBe('cards:newcard.path_sibling_child:{"anchor":"観測所A","via":"観測明細"}')
  })
})
