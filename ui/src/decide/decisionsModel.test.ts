import { beforeAll, describe, expect, it } from 'vitest'
import type { Alignment } from '../crosswalkApi'
import i18n from '../i18n'
import {
  DECISION_FILTERS,
  decisionSentence,
  decisionsHash,
  decisionsQuery,
  endName,
  filterCounts,
  matchesFilter,
  newestFirst,
  parseDecisionsQuery,
  questionCount,
} from './decisionsModel'

beforeAll(async () => {
  await i18n.changeLanguage('ja')
})
const t = (key: string, opts?: Record<string, unknown>) => i18n.t(key, opts) as string

const RDFS = 'http://www.w3.org/2000/01/rdf-schema#'
const OWL = 'http://www.w3.org/2002/07/owl#'
let n = 0
function row(over: Partial<Alignment> = {}): Alignment {
  n += 1
  return {
    alignment_iri: `urn:a:${n}`,
    source: 'urn:s',
    target: 'urn:t',
    relation: `${RDFS}subClassOf`,
    from_perspective: '',
    to_perspective: '',
    at: '2026-10-09T00:00:00+00:00',
    ...over,
  }
}

describe('絞り込み', () => {
  const list = [
    row({ relation: `${RDFS}subClassOf`, source_kind: 'dataset', target_kind: 'shared' }),
    row({ relation: 'subPropertyOf', source_kind: 'dataset', target_kind: 'shared' }),
    row({ relation: `${OWL}equivalentClass`, source_kind: 'dataset', target_kind: 'standard' }),
    row({ relation: 'equivalentProperty' }),
    row({ relation: 'http://qudt.org/schema/qudt/hasQuantityKind', target_kind: 'standard' }),
    row({ relation: 'subClassOf', broken: true }),
  ]

  it('一種・同じもの・量・標準用語・切れている', () => {
    const ids = (f: (typeof DECISION_FILTERS)[number]) =>
      list.filter((a) => matchesFilter(a, f)).map((a) => a.alignment_iri)
    expect(ids('all')).toHaveLength(6)
    expect(ids('sub')).toHaveLength(3)
    expect(ids('same')).toHaveLength(2)
    expect(ids('quantity')).toHaveLength(1)
    expect(ids('standard')).toHaveLength(2)
    expect(ids('broken')).toHaveLength(1)
    // つながり同士は一覧ではなく別の画面（一覧の判定には使わない）
    expect(ids('perspective')).toHaveLength(0)
  })

  it('件数は pill に添える', () => {
    const c = filterCounts(list)
    expect([c.all, c.sub, c.same, c.quantity, c.standard, c.broken]).toEqual([6, 3, 2, 1, 2, 1])
  })

  it('新しい順（日付なしは後ろ・同日は後から決めたものが先）', () => {
    const a = row({ at: '2026-10-01T00:00:00+00:00' })
    const b = row({ at: '2026-10-14T00:00:00+00:00' })
    const c = row({ at: '' })
    const d = row({ at: '2026-10-14T00:00:00+00:00' })
    expect(newestFirst([a, b, c, d]).map((x) => x.alignment_iri)).toEqual([
      d.alignment_iri,
      b.alignment_iri,
      a.alignment_iri,
      c.alignment_iri,
    ])
  })
})

describe('1 行 1 文', () => {
  it('データセットの端は「名前（データセット名）」・標準用語は接頭辞つき', () => {
    const a = row({
      source: 'urn:s:Sample',
      source_kind: 'dataset',
      source_label: 'Sample',
      source_dataset: 'starrydata',
      target: 'urn:sv:sample',
      target_kind: 'shared',
      target_label: '試料',
    })
    expect(endName(a, 'source', { starrydata: 'Starrydata' })).toBe('Sample（Starrydata）')
    expect(endName(a, 'target', {})).toBe('試料')
    expect(decisionSentence(a, { starrydata: 'Starrydata' }, t)).toBe(
      '「Sample（Starrydata）」は「試料」の一種',
    )
    const std = row({
      source: 'urn:p:u',
      source_label: '単位',
      relation: 'equivalentProperty',
      target: 'http://qudt.org/schema/qudt/hasUnit',
      target_kind: 'standard',
    })
    expect(decisionSentence(std, {}, t)).toBe('「単位」の値は「qudt:hasUnit」と同じ')
  })

  it('閉集合に無い関係は関係名のまま・「線」は出さない', () => {
    const a = row({ relation: 'http://example.org/other#likes', source_label: 'A', target_label: 'B' })
    const s = decisionSentence(a, {}, t)
    expect(s).toBe('「A」と「B」（likes）')
    expect(s).not.toContain('線')
  })

  it('問いの数は、端にある用語の問いの合計', () => {
    const a = row({ source: 'urn:sv:x', target: 'urn:k' })
    const terms = [
      { iri: 'urn:sv:x', cqs: [{}, {}] },
      { iri: 'urn:sv:y', cqs: [{}] },
    ]
    expect(questionCount(a, terms)).toBe(2)
    expect(questionCount(row(), terms)).toBe(0)
  })
})

describe('hash', () => {
  it('タブと絞り込みを往復する', () => {
    expect(parseDecisionsQuery('')).toEqual({ tab: 'decisions', filter: 'all' })
    expect(parseDecisionsQuery('tab=activity')).toEqual({ tab: 'activity', filter: 'all' })
    expect(parseDecisionsQuery('filter=perspective')).toEqual({ tab: 'decisions', filter: 'perspective' })
    expect(parseDecisionsQuery('filter=bogus').filter).toBe('all')
    expect(decisionsQuery('decisions', 'all')).toBe('')
    expect(decisionsQuery('activity', 'broken')).toBe('?tab=activity')
    expect(decisionsHash('perspective')).toBe('#/decisions?filter=perspective')
    expect(decisionsHash()).toBe('#/decisions')
  })
})
