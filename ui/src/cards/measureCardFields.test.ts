import { describe, expect, it } from 'vitest'
import type { SchemaProperty } from './cardsApi'
import {
  buildMeasureCard,
  candidates,
  cardId,
  canonicalJson,
  fieldsFor,
  needsAgg,
  MEASURE_AGGS,
  MEASURE_SHAPES,
  sha256Hex,
  shapes,
  titleFor,
  toCardSpec,
  type MeasureSchemaLike,
} from './measureCardFields'

// 架空の分野（観測記録）で確かめる。measureCardFields.ts は kind/distinct_count/
// quantity_kind/unit だけを見て組み立てる決定論関数 — 分野語の辞書は持たない。

function prop(over: Partial<SchemaProperty> & Pick<SchemaProperty, 'iri' | 'label' | 'kind'>): SchemaProperty {
  return { datatype: null, unit: null, quantity_kind: null, column: null, distinct_count: null, ...over }
}

const OBSERVATION_SCHEMA: MeasureSchemaLike = {
  class_iri: 'https://example.org/onto/Observation',
  label: '観測記録',
  properties: [
    // 時間らしい quantity: ラベルに「年」を含む・単位なし・distinct 多め
    // （x 候補の並びで最優先になるはず — measure_spec.py の _is_time_like と
    // 同じくラベル/IRI 局所名で判定する）。
    prop({
      iri: 'https://example.org/onto/year',
      label: '年',
      kind: 'quantity',
      unit: null,
      distinct_count: 40,
    }),
    // 単位ありの quantity（年より劣後するはず）。
    prop({ iri: 'https://example.org/onto/rainfall', label: '降水量', kind: 'quantity', unit: 'unit:MilliM', distinct_count: 300 }),
    // 単位なしだが時間らしくない quantity（年よりは劣後・降水量よりは distinct が
    // 多いので単位ありの降水量より優先されるはず）。
    prop({ iri: 'https://example.org/onto/index', label: '通し番号', kind: 'quantity', unit: null, distinct_count: 500 }),
    // 値が 1 つしかない quantity（1 件自身の値・契約メモ §1-3）。
    prop({ iri: 'https://example.org/onto/elevation', label: '標高', kind: 'quantity', unit: 'unit:M', distinct_count: 1 }),
    prop({ iri: 'https://example.org/onto/station', label: '観測地点', kind: 'category', distinct_count: 12 }),
    prop({ iri: 'https://example.org/onto/note', label: '備考', kind: 'text' }),
    prop({ iri: 'https://example.org/onto/instrument', label: '観測機', kind: 'link' }),
  ],
}

const t = (key: string, options?: Record<string, unknown>) => (options?.defaultValue as string | undefined) ?? key

describe('shapes/fieldsFor/needsAgg', () => {
  it('①の掲載順どおりの 6 種を返す', () => {
    expect(shapes()).toEqual(MEASURE_SHAPES)
    expect(shapes()).toEqual(['series', 'ranked', 'breakdown', 'pairs', 'quantity', 'facts'])
  })

  it('見せ方ごとに②で聞く項目が決まる', () => {
    expect(fieldsFor('series')).toEqual([
      { role: 'x', multiple: false },
      { role: 'y', multiple: false },
    ])
    expect(fieldsFor('pairs')).toEqual([
      { role: 'x', multiple: false },
      { role: 'y', multiple: false },
    ])
    expect(fieldsFor('ranked')).toEqual([{ role: 'item', multiple: false }])
    expect(fieldsFor('quantity')).toEqual([{ role: 'item', multiple: false }])
    expect(fieldsFor('breakdown')).toEqual([{ role: 'category', multiple: false }])
    expect(fieldsFor('facts')).toEqual([{ role: 'items', multiple: true }])
  })

  it('「数字 1 つ」だけ集計を聞く', () => {
    expect(needsAgg('quantity')).toBe(true)
    for (const s of MEASURE_SHAPES) if (s !== 'quantity') expect(needsAgg(s)).toBe(false)
    expect(MEASURE_AGGS).toEqual(['avg', 'max', 'min', 'sum', 'count'])
  })
})

describe('candidates', () => {
  it('推移: 横軸は時間らしい→単位なし→distinct 多い順、値が 1 つの quantity は外す', () => {
    const c = candidates(OBSERVATION_SCHEMA, 'series')
    expect(c.x.map((x) => x.property)).toEqual([
      'https://example.org/onto/year',
      'https://example.org/onto/index',
      'https://example.org/onto/rainfall',
    ])
    // 縦軸は横軸と同じ候補集合（並びの優先はしない）で、値が 1 つの quantity は
    // 同じく除く。
    expect(c.y.map((y) => y.property).sort()).toEqual(
      ['https://example.org/onto/index', 'https://example.org/onto/rainfall', 'https://example.org/onto/year'].sort(),
    )
    expect(c.category).toEqual([])
    expect(c.item).toEqual([])
    expect(c.items).toEqual([])
  })

  it('散らばり: 横軸・縦軸とも同じ quantity 候補（並び替えはしない）', () => {
    const c = candidates(OBSERVATION_SCHEMA, 'pairs')
    expect(c.x.map((x) => x.property)).not.toContain('https://example.org/onto/elevation')
    expect(c.x).toEqual(c.y)
  })

  it('比べる: 値が 1 つの quantity は外す', () => {
    const c = candidates(OBSERVATION_SCHEMA, 'ranked')
    expect(c.item.map((i) => i.property)).not.toContain('https://example.org/onto/elevation')
    expect(c.item.length).toBe(3)
  })

  it('数字 1 つ: 値が 1 つの quantity も候補に残す（契約メモ §1-3）', () => {
    const c = candidates(OBSERVATION_SCHEMA, 'quantity')
    expect(c.item.map((i) => i.property)).toContain('https://example.org/onto/elevation')
    expect(c.item.length).toBe(4)
  })

  it('内訳: category だけ', () => {
    const c = candidates(OBSERVATION_SCHEMA, 'breakdown')
    expect(c.category.map((x) => x.property)).toEqual(['https://example.org/onto/station'])
  })

  it('表: quantity/category だけ（measure_spec.py の _FIELD_KINDS と同じ絞り）', () => {
    const c = candidates(OBSERVATION_SCHEMA, 'facts')
    const props = c.items.map((i) => i.property)
    expect(props).not.toContain('https://example.org/onto/instrument') // link
    expect(props).not.toContain('https://example.org/onto/note') // text
    expect(props).toContain('https://example.org/onto/station') // category
    expect(props).toContain('https://example.org/onto/elevation') // quantity（値が 1 つでも可）
  })

  it('単位を候補に転記する', () => {
    const c = candidates(OBSERVATION_SCHEMA, 'pairs')
    const rainfall = c.x.find((x) => x.property === 'https://example.org/onto/rainfall')
    expect(rainfall?.unit).toBe('unit:MilliM')
  })
})

// 実機所見: 見本「世界の国」の「年」のような、主語テンプレートの一部
// （kind=identifier）で datatype が数値の座標。横軸だけがこれを候補に受ける。
const KEY_SCHEMA: MeasureSchemaLike = {
  class_iri: 'https://example.org/onto/Record',
  label: '記録',
  properties: [
    prop({
      iri: 'https://example.org/onto/batchNumber',
      label: '通し番号',
      kind: 'identifier',
      datatype: 'http://www.w3.org/2001/XMLSchema#integer',
    }),
    prop({
      iri: 'https://example.org/onto/code',
      label: 'コード',
      kind: 'identifier',
      datatype: 'http://www.w3.org/2001/XMLSchema#string',
    }),
    prop({ iri: 'https://example.org/onto/reading', label: '値', kind: 'quantity', unit: 'unit:M', distinct_count: 30 }),
  ],
}

describe('candidates — numeric identifier as a coordinate (x only)', () => {
  it('推移: 数値 datatype の identifier が横軸候補の先頭に出る', () => {
    const c = candidates(KEY_SCHEMA, 'series')
    expect(c.x.map((x) => x.property)[0]).toBe('https://example.org/onto/batchNumber')
    expect(c.x.map((x) => x.property)).not.toContain('https://example.org/onto/code')
  })

  it('推移: identifier は縦軸候補には出ない（y は quantity のみ）', () => {
    const c = candidates(KEY_SCHEMA, 'series')
    expect(c.y.map((y) => y.property)).not.toContain('https://example.org/onto/batchNumber')
  })
})

describe('buildMeasureCard', () => {
  it('推移: x/y をそのまま params へ', () => {
    const built = buildMeasureCard({
      classIri: 'https://example.org/onto/Observation',
      shape: 'series',
      where: [],
      x: 'https://example.org/onto/year',
      y: 'https://example.org/onto/rainfall',
    })
    expect(built).toEqual({
      tool: 'set_measure',
      params: {
        class: 'https://example.org/onto/Observation',
        where: [],
        shape: 'series',
        x: 'https://example.org/onto/year',
        y: 'https://example.org/onto/rainfall',
      },
      output_kind: 'series',
    })
  })

  it('比べる: ②の「項目」は params.item に入り、多い順（order: desc）を自動で付ける', () => {
    const built = buildMeasureCard({
      classIri: 'https://example.org/onto/Observation',
      shape: 'ranked',
      where: [],
      item: 'https://example.org/onto/rainfall',
    })
    expect(built.params.item).toBe('https://example.org/onto/rainfall')
    expect(built.params.order).toBe('desc')
    expect(built.params.category).toBeUndefined()
  })

  it('数字 1 つ: 項目 + 集計', () => {
    const built = buildMeasureCard({
      classIri: 'https://example.org/onto/Observation',
      shape: 'quantity',
      where: [],
      item: 'https://example.org/onto/rainfall',
      agg: 'avg',
    })
    expect(built.params.item).toBe('https://example.org/onto/rainfall')
    expect(built.params.agg).toBe('avg')
    expect(built.params.order).toBeUndefined()
  })

  it('表: items は複数、空なら params に含めない', () => {
    const built = buildMeasureCard({
      classIri: 'https://example.org/onto/Observation',
      shape: 'facts',
      where: [],
      items: ['https://example.org/onto/note', 'https://example.org/onto/station'],
    })
    expect(built.params.items).toEqual(['https://example.org/onto/note', 'https://example.org/onto/station'])

    const empty = buildMeasureCard({ classIri: 'https://example.org/onto/Observation', shape: 'facts', where: [], items: [] })
    expect(empty.params.items).toBeUndefined()
  })

  it('「この 1 件」の link 条件をそのまま where に運ぶ', () => {
    const built = buildMeasureCard({
      classIri: 'https://example.org/onto/Observation',
      shape: 'breakdown',
      where: [{ property: 'https://example.org/onto/observedBy', iri: 'https://example.org/place/1' }],
      category: 'https://example.org/onto/station',
    })
    expect(built.params.where).toEqual([{ property: 'https://example.org/onto/observedBy', iri: 'https://example.org/place/1' }])
  })
})

describe('canonicalJson / sha256Hex / cardId', () => {
  it('canonicalJson はキーを並べ替え、非 ASCII を \\u エスケープする（Python の json.dumps(sort_keys, ensure_ascii) と揃える）', () => {
    expect(canonicalJson({ b: 1, a: '観測' })).toBe('{"a":"\\u89b3\\u6e2c","b":1}')
    // 配列の順序は変えない。
    expect(canonicalJson({ where: [{ b: 1, a: 2 }] })).toBe('{"where":[{"a":2,"b":1}]}')
  })

  it('sha256Hex は既知のダイジェストと一致する（FIPS 180-4 のテストベクタ）', () => {
    expect(sha256Hex('')).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
    expect(sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  })

  it('cardId は同じ params から常に同じ id、違う params からは違う id', () => {
    const a = buildMeasureCard({
      classIri: 'https://example.org/onto/Observation',
      shape: 'series',
      where: [],
      x: 'https://example.org/onto/year',
      y: 'https://example.org/onto/rainfall',
    }).params
    const b = buildMeasureCard({
      classIri: 'https://example.org/onto/Observation',
      shape: 'series',
      where: [],
      y: 'https://example.org/onto/rainfall',
      x: 'https://example.org/onto/year',
    }).params
    expect(cardId(a)).toBe(cardId(b))
    expect(cardId(a)).toMatch(/^card-[0-9a-f]{16}$/)

    const c = { ...a, y: 'https://example.org/onto/index' }
    expect(cardId(c)).not.toBe(cardId(a))
  })
})

describe('titleFor', () => {
  it('§1-2 の表どおりの既定の題名を組む', () => {
    expect(titleFor('series', { y: '降水量' }, t)).toBe('降水量の推移')
    expect(titleFor('ranked', { item: '降水量' }, t)).toBe('降水量が多い順')
    expect(titleFor('breakdown', { category: '観測地点' }, t)).toBe('観測地点ごとの件数')
    expect(titleFor('pairs', { x: '年', y: '降水量' }, t)).toBe('年と降水量')
    expect(titleFor('quantity', { item: '降水量', agg: 'avg' }, t)).toBe('降水量の平均')
    expect(titleFor('facts', { items: ['備考', '観測地点'] }, t)).toBe('備考・観測地点')
  })
})

describe('toCardSpec', () => {
  it('CardSpec 一式を組み立てる', () => {
    const spec = toCardSpec(
      { classIri: 'https://example.org/onto/Observation', shape: 'series', where: [], x: 'https://example.org/onto/year', y: 'https://example.org/onto/rainfall' },
      { y: '降水量' },
      'i:https://example.org/place/1',
      t,
    )
    expect(spec.subject_key).toBe('i:https://example.org/place/1')
    expect(spec.tool).toBe('set_measure')
    expect(spec.output_kind).toBe('series')
    expect(spec.title).toBe('降水量の推移')
    expect(spec.card_id).toMatch(/^card-[0-9a-f]{16}$/)
    expect(typeof spec.created_at).toBe('string')
  })
})


describe('candidates: CURIE 形の datatype', () => {
  it("identifier の datatype が 'xsd:integer'（class_schema の実際の形）でも横軸候補に出る", () => {
    const schema: MeasureSchemaLike = {
      class_iri: 'https://example.org/onto/Record',
      label: '記録',
      properties: [
        prop({ iri: 'https://example.org/onto/seq', label: '連番', kind: 'identifier', datatype: 'xsd:integer' }),
        prop({ iri: 'https://example.org/onto/rainfall', label: '降水量', kind: 'quantity', datatype: 'xsd:double' }),
      ],
    }
    const c = candidates(schema, 'series')
    expect(c.x.map((x) => x.property)[0]).toBe('https://example.org/onto/seq')
    expect(c.y.map((y) => y.property)).not.toContain('https://example.org/onto/seq')
  })
})
