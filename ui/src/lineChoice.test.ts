import { describe, expect, it } from 'vitest'
import type { Alignment } from './crosswalkApi'
import {
  httpStatusOf,
  isDirectedRelation,
  lineChoice,
  orientPicks,
  QUANTITY_KIND_NS,
  relationKey,
  relationsFor,
  relationSymbol,
  sharedLines,
  standardTermKind,
  SV_NS,
  type PickEnd,
} from './lineChoice'
import type { SharedTerm } from './vocabApi'

const end = (over: Partial<PickEnd> & Pick<PickEnd, 'role' | 'termKind'>): PickEnd => ({
  id: over.iri ?? 'x',
  iri: 'https://example.org/x',
  label: 'x',
  ...over,
})

describe('relationsFor — 関係は閉集合から両端の kind で絞る', () => {
  it('種類どうし → equivalentClass / subClassOf', () => {
    expect(relationsFor('class', 'class')).toEqual(['equivalentClass', 'subClassOf'])
  })
  it('項目どうし → equivalentProperty / subPropertyOf', () => {
    expect(relationsFor('property', 'property')).toEqual(['equivalentProperty', 'subPropertyOf'])
  })
  it('項目 → 量の種類 → hasQuantityKind だけ', () => {
    expect(relationsFor('property', 'quantity')).toEqual(['hasQuantityKind'])
  })
  it('種類と項目の混在・量の種類 → 項目は引けない', () => {
    expect(relationsFor('class', 'property')).toEqual([])
    expect(relationsFor('property', 'class')).toEqual([])
    expect(relationsFor('quantity', 'property')).toEqual([])
  })
  it('量の種類は種類の一種（種類どうしの関係は引ける）', () => {
    expect(relationsFor('class', 'quantity')).toEqual(['equivalentClass', 'subClassOf'])
  })
  it('見分けられない標準の語は、分かっている方の kind に合わせる', () => {
    expect(relationsFor('class', 'unknown')).toEqual(['equivalentClass', 'subClassOf'])
    expect(relationsFor('property', 'unknown')).toContain('subPropertyOf')
  })
})

describe('lineChoice — 2 つ選んだときの向きと候補', () => {
  const kind = end({ iri: 'https://example.org/Record', role: 'dataset', termKind: 'class' })
  const shared = end({ iri: `${SV_NS}diffraction_point`, role: 'shared', termKind: 'class' })
  const std = end({ iri: 'https://schema.org/Thing', role: 'standard', termKind: 'class' })
  const item = end({ iri: 'https://example.org/mass', role: 'dataset', termKind: 'property' })

  it('既定の向きは 狭い方 → 広い方（データセット → 共有 → 標準）。選んだ順に依らない', () => {
    for (const [a, b] of [
      [shared, kind],
      [kind, shared],
    ]) {
      const c = lineChoice(a, b)
      expect(c.source.role).toBe('dataset')
      expect(c.target.role).toBe('shared')
    }
    expect(orientPicks(std, shared).source.role).toBe('shared')
  })

  it('種類どうしなら種類の関係だけ・種類と項目の混在は空', () => {
    expect(lineChoice(kind, shared).relations).toEqual(['equivalentClass', 'subClassOf'])
    expect(lineChoice(kind, item).relations).toEqual([])
  })

  it('項目 → 量の種類は向きが固定され hasQuantityKind だけ', () => {
    const qk = end({
      iri: `${QUANTITY_KIND_NS}Mass`,
      role: 'standard',
      termKind: standardTermKind(`${QUANTITY_KIND_NS}Mass`),
    })
    const c = lineChoice(qk, item)
    expect(c.source.termKind).toBe('property')
    expect(c.target.termKind).toBe('quantity')
    expect(c.relations).toEqual(['hasQuantityKind'])
    expect(c.canSwap).toBe(false)
  })

  it('同じ素性どうしは選んだ順・入れ替えられるか', () => {
    const k2 = end({ iri: 'https://example.org/Other', role: 'dataset', termKind: 'class' })
    const c = lineChoice(kind, k2)
    expect(c.source).toBe(kind)
    expect(c.canSwap).toBe(true)
  })
})

describe('関係の読み方', () => {
  it('短い名前・IRI・CURIE のどれでも同じ', () => {
    expect(relationKey('subClassOf')).toBe('subClassOf')
    expect(relationKey('http://www.w3.org/2000/01/rdf-schema#subClassOf')).toBe('subClassOf')
    expect(relationKey('rdfs:subPropertyOf')).toBe('subPropertyOf')
    expect(relationKey(undefined)).toBe('')
  })
  it('⊂ は向きあり・≡ と未知の対応は向きなし', () => {
    expect(isDirectedRelation('subClassOf')).toBe(true)
    expect(isDirectedRelation('subPropertyOf')).toBe(true)
    expect(isDirectedRelation('equivalentClass')).toBe(false)
    expect(isDirectedRelation('skos:exactMatch')).toBe(false)
    expect(relationSymbol('subClassOf')).toBe('⊂')
    expect(relationSymbol('equivalentProperty')).toBe('≡')
  })
  it('httpStatusOf は .status か "HTTP 409" の文言から取る', () => {
    expect(httpStatusOf(Object.assign(new Error('x'), { status: 422 }))).toBe(422)
    expect(httpStatusOf(new Error('線を引く失敗 (HTTP 409): cycle'))).toBe(409)
    expect(httpStatusOf(new Error('network'))).toBeNull()
  })
})

describe('sharedLines — 共有のことばに掛かる線', () => {
  const sv = (slug: string) => `${SV_NS}${slug}`
  const term = (slug: string, over: Partial<SharedTerm> = {}): SharedTerm => ({
    iri: sv(slug),
    slug,
    kind: 'class',
    label: slug,
    label_en: null,
    comment: null,
    created_at: '2026-10-09T00:00:00Z',
    wired: true,
    answering_datasets: 1,
    narrower: [],
    standards: [],
    cqs: [],
    ...over,
  })
  const al = (source: string, target: string, relation: string): Alignment => ({
    alignment_iri: `a:${source}${target}`,
    source,
    target,
    relation,
    from_perspective: '',
    to_perspective: '',
    at: '',
  })

  it('alignments を正とし、narrower／standards は無い対だけ足す', () => {
    const t = term('dp', {
      narrower: [
        { iri: 'https://example.org/Record', kind: 'dataset', dataset_id: 'd1', label: 'Record' },
        { iri: 'https://example.org/Peak', kind: 'dataset', dataset_id: 'd2', label: 'Peak' },
      ],
      standards: [{ iri: 'https://schema.org/Thing', relation: 'equivalentClass' }],
    })
    const lines = sharedLines(
      [t],
      [al('https://example.org/Record', sv('dp'), 'equivalentClass'), al('https://a/x', 'https://a/y', 'subClassOf')],
    )
    // Record は alignments の ≡ が正（narrower の吊るしで ⊂ を重ねない）。共有語と無関係の線は含めない。
    expect(lines).toEqual([
      { from: 'https://example.org/Record', to: sv('dp'), relation: 'equivalentClass' },
      { from: 'https://example.org/Peak', to: sv('dp'), relation: 'subClassOf' },
      { from: sv('dp'), to: 'https://schema.org/Thing', relation: 'equivalentClass' },
    ])
  })

  it('項目の語の narrower は subPropertyOf', () => {
    const t = term('mass', {
      kind: 'property',
      narrower: [{ iri: 'https://example.org/m', kind: 'dataset', dataset_id: 'd1', label: 'm' }],
    })
    expect(sharedLines([t], [])[0].relation).toBe('subPropertyOf')
  })
})
