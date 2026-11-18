import { describe, expect, it } from 'vitest'
import { EMPTY_SKY, type SkyLine, type SkyTerm } from '../../sky/skyApi'
import { ledger, row, term } from './fixtures'
import { DEFAULT_FILTER, type LedgerFilter } from './ledgerView'
import { filterStoreSky } from './storeFilter'

const P = (id: string, iri: string, role = 'material') => ({ ...term(id, id, role), termIri: iri })
const st = (iri: string): SkyTerm => ({ iri, slug: iri, label: iri, label_en: iri, kind: 'class', members: [], standard: null, answering: 0, questions: [] })
const line = (from: string, to: string, relation: string): SkyLine => ({ from, to, relation, reason: '' }) as SkyLine

// a→b lowers（確認済み・置いた）／a-c competes（確認済み・置いた）／b→d raises（未確認・置いていない）／x→y（チャットで作った線）
const a = P('t-a', 'u:a'), b = P('t-b', 'u:b'), c = P('t-c', 'u:c')
const rows = [
  row({ from: a, to: b, rel: 'lowers', confirmed: true, key: 'k1' }),
  row({ from: c, to: a, rel: 'competes', confirmed: true, key: 'k2' }),
  row({ from: b, to: term('t-d', 'd'), rel: 'raises', key: 'k3' }),
]
const sky = {
  ...EMPTY_SKY,
  terms: ['u:a', 'u:b', 'u:c', 'u:x', 'u:y', 'u:z'].map(st),
  edges: [line('u:a', 'u:b', 'lowers'), line('u:a', 'u:c', 'competesWith'), line('u:x', 'u:y', 'raises')],
}
const L = [ledger('s1', rows), ledger('s2', [row({ from: P('t-q', 'u:q'), to: P('t-r', 'u:r'), rel: 'makes', confirmed: true, key: 'k9' })])]
const f = (over: Partial<LedgerFilter>): LedgerFilter => ({ ...DEFAULT_FILTER, ...over })
const out = (over: Partial<LedgerFilter>) => {
  const s = filterStoreSky({ ...sky, terms: [...sky.terms, st('u:q'), st('u:r')] }, L, f(over))
  return { edges: s.edges!.map((e) => `${e.from}>${e.to}`), terms: s.terms.map((t) => t.iri) }
}

describe('絞り込みを置いた語と線にも効かせる', () => {
  it('全部「すべて」なら何もしない（同じ物を返す）', () => {
    expect(filterStoreSky(sky, L, DEFAULT_FILTER)).toBe(sky)
  })
  it('状態＝未確認: 確認済みで置いた線は消える。当たる行が無い線（チャットで作った）も消える。語も絡むものだけ', () => {
    const r = out({ status: 'unconfirmed' })
    expect(r.edges).toEqual([])
    expect(r.terms).toEqual(['u:b']) // 未確認の行 b→d の端（置いてある b）だけ
  })
  it('状態＝確認済み: 行に当たる線は残り、当たらないチャットの線も残る', () => {
    const r = out({ status: 'confirmed' })
    expect(r.edges).toEqual(['u:a>u:b', 'u:a>u:c', 'u:x>u:y'])
    expect(r.terms.sort()).toEqual(['u:a', 'u:b', 'u:c', 'u:q', 'u:r', 'u:x', 'u:y'])
  })
  it('取り込み元: その取り込み元の行に当たる線だけ。チャットの線は出ない', () => {
    expect(out({ source: 's1' }).edges).toEqual(['u:a>u:b', 'u:a>u:c'])
    const s2 = out({ source: 's2' })
    expect(s2.edges).toEqual([])
    expect(s2.terms.sort()).toEqual(['u:q', 'u:r'])
  })
  it('関係の種類: 合う行の線だけ。チャットの線は種類が合うときだけ（向きの無い競合は向きを問わない）', () => {
    expect(out({ rel: 'lowers' }).edges).toEqual(['u:a>u:b'])
    expect(out({ rel: 'competes' }).edges).toEqual(['u:a>u:c']) // 行は c→a・線は a→c
    expect(out({ rel: 'raises' }).edges).toEqual(['u:x>u:y']) // 行 b→d は置いていない。チャットの raises は取り込み元「すべて」・種類が合うので出る
    expect(out({ rel: 'raises', source: 's1' }).edges).toEqual([])
  })
  it('「違うとしたもの」: 確認済みの線は出ない（外れている）', () => {
    const rej = [ledger('s1', [row({ from: a, to: b, rel: 'lowers', state: 'dropped', rejected: true, key: 'k1' })])]
    const s = filterStoreSky(sky, rej, f({ status: 'rejected' }))
    expect(s.edges).toEqual([])
  })
  it('候補を出すときは、候補の語（置いてあれば）も残る', () => {
    const cand = [ledger('s1', [row({ from: P('t-m', 'u:m'), to: P('t-n', 'u:n'), state: 'none', rel: null, key: null })])]
    const s = filterStoreSky({ ...sky, terms: [st('u:m'), st('u:n')] }, cand, f({ status: 'unconfirmed', candidates: true }))
    expect(s.terms.map((t) => t.iri)).toEqual(['u:m', 'u:n'])
    expect(filterStoreSky({ ...sky, terms: [st('u:m')] }, cand, f({ status: 'unconfirmed' })).terms).toEqual([])
  })
})
