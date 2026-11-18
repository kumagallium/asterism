import { describe, expect, it } from 'vitest'
import { A, B, C, ledger, row } from './fixtures'
import { DEFAULT_FILTER, effectiveFilter, filterCandidates, relKinds } from './ledgerView'
import { withoutCopyDatasets } from './ledgerDrafts'

describe('候補は組（pair）ごとに 1 行', () => {
  const none = row({ from: A, to: B, state: 'none', rel: null, key: 'k-ab' })
  const origin = row({ from: B, to: A, state: 'none', rel: 'sub', key: 'o:t-b|sub|t-a', by: 'origin', sentence: '焼結 は ゲルマ の一種' })
  it('AI の「言えない」と取り込み元の食い違いを 1 行にまとめ、取り込み元の言い分を添える', () => {
    const c = filterCandidates([ledger('s', [none, origin])], { source: 'all' })
    expect(c).toHaveLength(1)
    expect(c[0].key).toBe('k-ab')
    expect(c[0].originSentence).toBe('焼結 は ゲルマ の一種')
  })
  it('取り込み元の食い違いだけでも 1 行（その言い分つき）。別の組は別の行', () => {
    const c = filterCandidates([ledger('s', [origin, row({ from: A, to: C, state: 'none', rel: null })])], { source: 'all' })
    expect(c).toHaveLength(2)
    expect(c.find((r) => r.pair === 't-a|t-b')?.originSentence).toBe('焼結 は ゲルマ の一種')
  })
})

describe('絞り込みの選択肢が消えたとき', () => {
  const L = [ledger('s', [row({ from: A, to: B, rel: 'makes' }), row({ from: A, to: C, rel: 'sub', state: 'dropped', rejected: true })])]
  it('選んでいる取り込み元・関係が無くなったら「すべて」に戻す', () => {
    expect(effectiveFilter(L, { ...DEFAULT_FILTER, source: 'gone' }).source).toBe('all')
    expect(effectiveFilter(L, { ...DEFAULT_FILTER, rel: 'raises' }).rel).toBe('all')
    const same = { ...DEFAULT_FILTER, source: 's', rel: 'makes' }
    expect(effectiveFilter(L, same)).toBe(same)
  })
  it('状態が「違うとしたもの」のときは、関係の種類を「違う」とした行から作る', () => {
    expect(relKinds(L)).toEqual(['makes'])
    expect(relKinds(L, 'rejected')).toEqual(['sub'])
    expect(effectiveFilter(L, { ...DEFAULT_FILTER, status: 'rejected', rel: 'makes' }).rel).toBe('all')
  })
})

describe('写しのデータセットを図から除く', () => {
  it('取り込み元の datasetId のものだけ除く', () => {
    const L = [{ ...ledger('s', []), source: { source: 's', title: 't', datasetId: 'copy', copiedAt: null, running: false } }]
    expect(withoutCopyDatasets([{ id: 'copy' }, { id: 'world' }], L)).toEqual([{ id: 'world' }])
    expect(withoutCopyDatasets([{ id: 'copy' }], [])).toEqual([{ id: 'copy' }])
  })
})
