// ⑤ 種類の当てはめ提案の純関数（候補の絞り込み・受けた当てはめと upper.json の往復）。
import { describe, expect, it } from 'vitest'
import type { ColumnMeaning, MappingSkeleton, SkeletonMap, UpperItem } from '../api'
import type { FitCandidate } from '../vocabApi'
import { columnKey } from './settledStore'
import {
  expandCurie,
  itemFitCandidates,
  itemFitsToUpper,
  kindFitCandidates,
  kindIriOf,
  picksToUpper,
  upperToPicks,
} from './kindFit'

const ONTO = 'https://example.org/datasets/xrd/ontology#'
const SV_PEAK = 'https://example.org/sv/peak'

const map = (name: string, cls: string | undefined): SkeletonMap => ({
  name,
  source: `${name}.csv`,
  subject: { template: `${name}/{id}`, classes: cls ? [cls] : [] },
})

const skeleton: MappingSkeleton = {
  version: 1,
  prefixes: { xrd: ONTO },
  maps: [map('peak', 'xrd:Peak'), map('sample', 'xrd:Sample'), map('nameless', undefined)],
}

const cand = (
  term: string,
  kind: FitCandidate['kind'],
  termKind: FitCandidate['term_kind'] = 'class',
): FitCandidate => ({
  term,
  kind,
  term_kind: termKind,
  label: term.slice(-4),
  matched_by: 'label',
})

describe('expandCurie / kindIriOf', () => {
  it('接頭辞を展開する。IRI はそのまま。展開できなければ null', () => {
    expect(expandCurie('xrd:Peak', skeleton.prefixes)).toBe(`${ONTO}Peak`)
    expect(expandCurie('https://a.example/x', {})).toBe('https://a.example/x')
    expect(expandCurie('nope:Peak', skeleton.prefixes)).toBeNull()
    expect(expandCurie('Peak', skeleton.prefixes)).toBeNull()
  })

  it('種類の名前がまだ無い map は null', () => {
    expect(kindIriOf(skeleton.maps[0], skeleton)).toBe(`${ONTO}Peak`)
    expect(kindIriOf(skeleton.maps[2], skeleton)).toBeNull()
  })
})

describe('kindFitCandidates', () => {
  const classes = new Set([SV_PEAK, 'https://other.example/ontology#Peak', `${ONTO}Peak`])

  it('共有・他データの「種類」だけ。標準の語・項目・自分自身は出さない', () => {
    const got = kindFitCandidates(
      [
        cand(SV_PEAK, 'shared'),
        cand('https://other.example/ontology#Peak', 'dataset'),
        cand('https://std.example/Peak', 'standard'),
        cand('https://other.example/ontology#hasPeak', 'dataset', 'property'), // 項目
        cand(`${ONTO}Peak`, 'dataset'), // 自分自身
      ],
      classes,
      `${ONTO}Peak`,
    )
    expect(got.map((c) => c.term)).toEqual([SV_PEAK, 'https://other.example/ontology#Peak'])
  })
})

describe('当てはめ提案の種類／項目の区別（term_kind）', () => {
  const list = [
    cand('https://sv.example/peak', 'shared', 'class'),
    cand('https://sv.example/intensity', 'shared', 'property'),
    cand('https://std.example/temperature', 'standard', 'property'),
    cand('https://std.example/Peak', 'standard', 'class'),
  ]

  it('③は property だけ・⑤は class だけ（種類の集合に入っていても property は出さない）', () => {
    expect(itemFitCandidates(list).map((c) => c.term)).toEqual([
      'https://sv.example/intensity',
      'https://std.example/temperature',
    ])
    const all = new Set(list.map((c) => c.term))
    expect(kindFitCandidates(list, all, null).map((c) => c.term)).toEqual([
      'https://sv.example/peak',
    ])
  })
})

describe('picksToUpper / upperToPicks', () => {
  it('受けた当てはめを upper.json の項目にする（subject は骨格の種類の IRI）', () => {
    const out = picksToUpper(
      { peak: { term: SV_PEAK, relation: 'subClassOf', label: '回折点' } },
      skeleton,
      [],
    )
    expect(out).toEqual([{ subject: `${ONTO}Peak`, term: SV_PEAK, relation: 'subClassOf' }])
  })

  it('種類の名前が無い map の当てはめは書かない。項目の当てはめ（other）は持ち越す', () => {
    const other: UpperItem[] = [
      { subject: `${ONTO}intensity`, term: 'https://example.org/sv/i', relation: 'subPropertyOf' },
    ]
    const out = picksToUpper(
      { nameless: { term: SV_PEAK, relation: 'subClassOf', label: 'x' } },
      skeleton,
      other,
    )
    expect(out).toEqual(other)
  })

  it('同じ線は 1 本だけ（共有の受け口が同じ種類を指しても重ならない）', () => {
    const shared: MappingSkeleton = {
      ...skeleton,
      maps: [map('a', 'xrd:Peak'), map('b', 'xrd:Peak')],
    }
    const pick = { term: SV_PEAK, relation: 'equivalentClass' as const, label: 'x' }
    expect(picksToUpper({ a: pick, b: pick }, shared, [])).toHaveLength(1)
  })

  it('読み戻し: 種類の関係で骨格の種類に当たるものだけが当てはめになる', () => {
    const items: UpperItem[] = [
      { subject: `${ONTO}Peak`, term: SV_PEAK, relation: 'equivalentClass', applied_at: 'T' },
      { subject: `${ONTO}Gone`, term: SV_PEAK, relation: 'subClassOf' },
      { subject: `${ONTO}Peak`, term: SV_PEAK, relation: 'subPropertyOf' },
    ]
    const { picks, other } = upperToPicks(items, skeleton, () => '回折点')
    expect(picks).toEqual({
      peak: { term: SV_PEAK, relation: 'equivalentClass', label: '回折点' },
    })
    // 当たらない・種類の関係でないものは触らずに残る（applied_at は送らない）
    expect(other).toEqual([
      { subject: `${ONTO}Gone`, term: SV_PEAK, relation: 'subClassOf' },
      { subject: `${ONTO}Peak`, term: SV_PEAK, relation: 'subPropertyOf' },
    ])
  })

  it('往復: 読み戻して送り直しても同じ項目になる', () => {
    const items: UpperItem[] = [
      { subject: `${ONTO}Peak`, term: SV_PEAK, relation: 'subClassOf' },
      { subject: `${ONTO}x`, term: 'https://example.org/sv/x', relation: 'subPropertyOf' },
    ]
    const { picks, other } = upperToPicks(items, skeleton, () => 'l')
    expect(picksToUpper(picks, skeleton, other)).toEqual(
      expect.arrayContaining(items.map((i) => expect.objectContaining(i))),
    )
    expect(picksToUpper(picks, skeleton, other)).toHaveLength(2)
  })
})

describe('項目の当てはめ → upper（property:<map>/<列>）', () => {
  const STD = 'https://std.example/temperature'
  const SV = 'https://example.org/sv/temp'
  const fit = (term: string, kind: 'shared' | 'standard' | 'dataset') => ({
    term,
    kind,
    matched_by: 'label' as const,
  })
  const meaning = (source: string, column: string, f?: ColumnMeaning['fit']): ColumnMeaning => ({
    source,
    column,
    label: column,
    fit: f,
  })
  const sk: MappingSkeleton = {
    version: 1,
    prefixes: { xrd: ONTO },
    maps: [
      { ...map('peak', 'xrd:Peak'), source: 'peak.csv' },
      { ...map('sample', 'xrd:Sample'), source: 'sample.csv', owns: ['温度'] },
      { ...map('sample2', 'xrd:Sample2'), source: 'sample.csv' },
    ],
  }

  it('標準の語は equivalentProperty、共有の語・他データの項目は subPropertyOf', () => {
    const got = itemFitsToUpper(
      [
        meaning('peak.csv', '温度', fit(STD, 'standard')),
        meaning('peak.csv', '角度', fit(SV, 'shared')),
        meaning('peak.csv', '強度', fit('https://o/ds#i', 'dataset')),
      ],
      [],
      sk,
    )
    expect(got).toEqual([
      { subject: 'property:peak/温度', term: STD, relation: 'equivalentProperty' },
      { subject: 'property:peak/角度', term: SV, relation: 'subPropertyOf' },
      { subject: 'property:peak/強度', term: 'https://o/ds#i', relation: 'subPropertyOf' },
    ])
  })

  it('列を持つ map は owns が先、無ければそのファイルの最初の map', () => {
    const got = itemFitsToUpper(
      [
        meaning('sample.csv', '温度', fit(STD, 'standard')),
        meaning('sample.csv', '重さ', fit(SV, 'shared')),
      ],
      [],
      sk,
    )
    expect(got.map((u) => u.subject)).toEqual(['property:sample/温度', 'property:sample/重さ'])
  })

  it('当てはめの無い列・取り込まない列・map の無いファイルは出さない', () => {
    const got = itemFitsToUpper(
      [
        meaning('peak.csv', '温度'),
        meaning('peak.csv', '角度', fit(SV, 'shared')),
        meaning('other.csv', '列', fit(SV, 'shared')),
      ],
      [columnKey('peak.csv', '角度')],
      sk,
    )
    expect(got).toEqual([])
  })

  it('ファイルが 1 つだけなら名前が食い違っても落とさない', () => {
    const one: MappingSkeleton = { ...sk, maps: [{ ...map('peak', 'xrd:Peak'), source: 'source-1a.csv' }] }
    const got = itemFitsToUpper([meaning('測定.csv', '温度', fit(STD, 'standard'))], [], one)
    expect(got.map((u) => u.subject)).toEqual(['property:peak/温度'])
  })

  it('picksToUpper: 項目の当てはめが加わり、外せば保管済みからも消える', () => {
    const stored: UpperItem[] = [
      { subject: 'property:peak/温度', term: STD, relation: 'equivalentProperty', applied_at: 't' },
      { subject: 'property:peak/角度', term: SV, relation: 'subPropertyOf' },
      { subject: 'https://x/other', term: SV, relation: 'subClassOf' },
    ]
    const fitsNow = itemFitsToUpper([meaning('peak.csv', '温度', fit(STD, 'standard'))], [], sk)
    const got = picksToUpper({}, sk, stored, fitsNow)
    // 今も受けている温度は消費済みの印ごと残り、外した角度は消え、項目ではない線は残る
    expect(got.map((u) => u.subject)).toEqual(['property:peak/温度', 'https://x/other'])
    expect(got[0].applied_at).toBe('t')
  })

  it('picksToUpper: 当てはめを全部外せば property: の項目は空になる', () => {
    const stored: UpperItem[] = [{ subject: 'property:peak/温度', term: STD, relation: 'equivalentProperty' }]
    expect(picksToUpper({}, sk, stored, [])).toEqual([])
  })

  it('picksToUpper: itemFits が null／省略なら保管済みの項目の当てはめには触らない', () => {
    const stored: UpperItem[] = [{ subject: 'property:peak/温度', term: STD, relation: 'equivalentProperty' }]
    expect(picksToUpper({}, sk, stored, null)).toEqual(stored)
    expect(picksToUpper({}, sk, stored)).toEqual(stored)
  })

  it('picksToUpper: 種類の当てはめと項目の当てはめは並んで出る（重複しない）', () => {
    const fitsNow = itemFitsToUpper([meaning('peak.csv', '温度', fit(STD, 'standard'))], [], sk)
    const picks = { peak: { term: SV_PEAK, relation: 'subClassOf' as const, label: 'Peak' } }
    const got = picksToUpper(picks, sk, [], [...fitsNow, ...fitsNow])
    expect(got).toHaveLength(2)
  })
})

describe('③ 標準の語は語彙の接頭辞つきで言う', () => {
  it('qudt と cmso の has unit を見分ける', async () => {
    const { fitSentence } = await import('./kindFit')
    const t = (k: string, o?: Record<string, unknown>) => `${k}:${String(o?.label)}`
    const std = (term: string) =>
      ({ term, kind: 'standard', term_kind: 'property', label: 'has unit', matched_by: 'column' }) as const
    expect(fitSentence(t, std('http://qudt.org/schema/qudt/hasUnit'))).toBe(
      'kantan:meanings.fit.sameStandard:qudt:hasUnit',
    )
    expect(fitSentence(t, std('http://purls.helmholtz-metadaten.de/cmso/hasUnit'))).toBe(
      'kantan:meanings.fit.sameStandard:cmso:hasUnit',
    )
    expect(
      fitSentence(t, { term: 'x', kind: 'shared', term_kind: 'property', label: '食材の名前', matched_by: 'label' }),
    ).toBe('kantan:meanings.fit.same:食材の名前')
  })
})

describe('③ 項目の当てはめの持ち主（ID のテンプレートで探す）', () => {
  it('owns に無い列は、その列を ID に持つ種類（1 件ごとの行）に付く', async () => {
    const { itemFitsToUpper } = await import('./kindFit')
    const skeleton = {
      maps: [
        { name: 'card', source: 'recipe.txt', subject: { template: 'card/{card_no}' } },
        { name: 'record', source: 'recipe.txt', subject: { template: 'record/{card_no}/{food}' } },
      ],
    } as unknown as Parameters<typeof itemFitsToUpper>[2]
    const meanings = [
      {
        source: 'recipe.txt',
        column: 'food',
        label: '食材の名前',
        fit: { term: 'https://kumagallium.github.io/asterism/vocab/shared#food_name', kind: 'shared', matched_by: 'label' },
      },
    ] as unknown as Parameters<typeof itemFitsToUpper>[0]
    const out = itemFitsToUpper(meanings, [], skeleton)
    expect(out.map((u) => u.subject)).toEqual(['property:record/food'])
  })
})
