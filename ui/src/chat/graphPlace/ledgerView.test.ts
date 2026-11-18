import { describe, expect, it } from 'vitest'
import { A, B, C, D, ev, ledger, row } from './fixtures'
import { DEFAULT_FILTER, filterCandidates, filterRelations, firstRows, PAGE_SIZE, relKinds, summarizeTerms, unconfirmedKeys } from './ledgerView'

const L = [
  ledger('src-a', [
    row({ from: B, to: A, rel: 'makes', evidence: ev(2) }),
    row({ from: B, to: C, rel: 'affects', evidence: ev(5), confirmed: true }),
    row({ from: A, to: C, rel: 'has_property', evidence: ev(9) }),
    row({ from: A, to: D, rel: 'sub', state: 'dropped', reason: 'rejected', rejected: true }),
    row({ from: C, to: D, state: 'none', rel: null, evidence: ev(3) }),
  ]),
  ledger('src-b', [row({ from: D, to: A, rel: 'makes', evidence: ev(1), key: 'k-b' })]),
]

describe('絞り込みと並び', () => {
  it('既定: 未確認 → 確認済み、根拠の多い順。違うとした行・候補は出ない', () => {
    const r = filterRelations(L, DEFAULT_FILTER)
    expect(r.map((x) => `${x.source}:${x.rel}:${x.confirmed}`)).toEqual(['src-a:has_property:false', 'src-a:makes:false', 'src-b:makes:false', 'src-a:affects:true'])
  })
  it('取り込み元・状態・関係の種類が効く', () => {
    expect(filterRelations(L, { ...DEFAULT_FILTER, source: 'src-b' })).toHaveLength(1)
    expect(filterRelations(L, { ...DEFAULT_FILTER, status: 'confirmed' }).map((x) => x.rel)).toEqual(['affects'])
    expect(filterRelations(L, { ...DEFAULT_FILTER, status: 'unconfirmed' })).toHaveLength(3)
    expect(filterRelations(L, { ...DEFAULT_FILTER, rel: 'makes' })).toHaveLength(2)
  })
  it('「違うとしたもの」だけが rejected の行を返す', () => {
    const r = filterRelations(L, { ...DEFAULT_FILTER, status: 'rejected' })
    expect(r).toHaveLength(1)
    expect(r[0].rejected).toBe(true)
  })
  it('候補は取り込み元だけで絞る', () => {
    expect(filterCandidates(L, { source: 'all' })).toHaveLength(1)
    expect(filterCandidates(L, { source: 'src-b' })).toHaveLength(0)
  })
  it('同じ入力なら並びは入力順に依らない', () => {
    const rev = [{ ...L[0], rows: [...L[0].rows].reverse() }, L[1]]
    expect(filterRelations(rev, DEFAULT_FILTER).map((x) => x.key)).toEqual(filterRelations(L, DEFAULT_FILTER).map((x) => x.key))
  })
  it('関係の種類の選択肢は、見せている関係にあるものだけ', () => {
    expect(relKinds(L)).toEqual(['affects', 'has_property', 'makes'])
  })
  it('1 度に 50 行', () => {
    expect(PAGE_SIZE).toBe(50)
    expect(firstRows(Array.from({ length: 120 }, (_, i) => i), PAGE_SIZE)).toHaveLength(50)
  })
})

describe('語ごとのまとめ', () => {
  it('両端の語に数える。未確認の多い順', () => {
    const t = summarizeTerms(filterRelations(L, DEFAULT_FILTER))
    const a = t.find((x) => x.termId === 't-a' && x.source === 'src-a')!
    expect(a).toMatchObject({ relations: 2, unconfirmed: 2, tone: 'm' })
    const c = t.find((x) => x.termId === 't-c')!
    expect(c).toMatchObject({ relations: 2, unconfirmed: 1, tone: 'p' })
    expect(t[0].unconfirmed).toBeGreaterThanOrEqual(t[t.length - 1].unconfirmed)
  })
  it('語の未確認の鍵だけを返す（確認済み・鍵なしは含まない）', () => {
    const t = summarizeTerms([row({ from: A, to: B, key: 'k1' }), row({ from: A, to: C, key: 'k2', confirmed: true }), row({ from: A, to: D, key: null })])
    const a = t.find((x) => x.termId === 't-a')!
    expect(unconfirmedKeys(a)).toEqual(['k1'])
  })
})
