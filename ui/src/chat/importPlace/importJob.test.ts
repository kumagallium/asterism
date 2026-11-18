import { describe, expect, it } from 'vitest'
import { barPercent, idle, importJobReducer as red, lostCount, questionRows, skipKey, stepsOf, type ImportJobState } from './importJob'
import { result } from './testData'

describe('importJobReducer', () => {
  it('start → progress（段と n/N）→ finished', () => {
    let s: ImportJobState = red(idle, { type: 'start', source: 'lab' })
    expect(s).toEqual({ phase: 'running', source: 'lab', stage: null, done: null, total: null })
    s = red(s, { type: 'progress', stage: 'copy' })
    s = red(s, { type: 'progress', stage: 'pairs', done: 0, total: 289 })
    s = red(s, { type: 'progress', stage: 'pairs', done: 132, total: 289 })
    expect(s).toMatchObject({ stage: 'pairs', done: 132, total: 289 })
    expect(barPercent(s as Extract<ImportJobState, { phase: 'running' }>)).toBe(46)
    const r = result()
    expect(red(s, { type: 'finished', result: r })).toEqual({ phase: 'done', result: r })
  })
  it('段は戻らない・数の無い枠は前の数を残す・知らない段は無視', () => {
    let s = red(idle, { type: 'start', source: 'a' })
    s = red(s, { type: 'progress', stage: 'pairs', done: 3, total: 10 })
    s = red(s, { type: 'progress', stage: 'copy' })
    s = red(s, { type: 'progress', stage: 'zzz', done: 'x' })
    expect(s).toMatchObject({ stage: 'pairs', done: 3, total: 10 })
  })
  it('走っていないときの枠・終わりは無視する', () => {
    expect(red(idle, { type: 'progress', stage: 'copy' })).toBe(idle)
    expect(red(idle, { type: 'finished', result: result() })).toBe(idle)
    expect(red(idle, { type: 'cancelled' })).toBe(idle)
  })
  it('取り消し・失敗・閉じる', () => {
    const run = red(idle, { type: 'start', source: 'lab' })
    expect(red(run, { type: 'cancelled' })).toEqual({ phase: 'cancelled', source: 'lab' })
    expect(red(run, { type: 'failed', error: '409' })).toEqual({ phase: 'failed', source: 'lab', error: '409' })
    expect(red(red(run, { type: 'cancelled' }), { type: 'dismiss' })).toBe(idle)
  })
  it('stepsOf: fetch/copy は「写す」・pairs・done', () => {
    const at = (st: Parameters<typeof stepsOf>[0]) => stepsOf(st).map((x) => x.state)
    expect(at(null)).toEqual(['cur', 'todo', 'todo'])
    expect(at('fetch')).toEqual(['cur', 'todo', 'todo'])
    expect(at('pairs')).toEqual(['done', 'cur', 'todo'])
    expect(at('done')).toEqual(['done', 'done', 'cur'])
  })
  it('barPercent: pairs で数が取れるときだけ・total 0 は null', () => {
    const run = (o: object) => ({ phase: 'running', source: 'a', stage: 'pairs', done: 1, total: 4, ...o }) as Extract<ImportJobState, { phase: 'running' }>
    expect(barPercent(run({}))).toBe(25)
    expect(barPercent(run({ stage: 'copy' }))).toBeNull()
    expect(barPercent(run({ total: 0 }))).toBeNull()
    expect(barPercent(run({ total: null }))).toBeNull()
  })
})

describe('結果の 1 枚の整え', () => {
  it('skipKey: skipped ・取り消し・打ち切り', () => {
    expect(skipKey(result())).toBeNull()
    expect(skipKey(result({}, { skipped: 'ai_unset' }))).toBe('ai_unset')
    expect(skipKey(result({}, { skipped: 'off' }))).toBe('off')
    expect(skipKey(result({}, { skipped: 'error' }))).toBe('error')
    expect(skipKey(result({}, { cancelled: true }))).toBe('cancelled')
    expect(skipKey(result({}, { aborted: true }))).toBe('aborted')
  })
  it('lostCount: 落とした＋関係の失敗', () => {
    expect(lostCount(result())).toBe(0)
    expect(lostCount(result({ copied: { claims: 1, topics: 1, sources: 1, terms: 1, dropped: { a: 2, b: 1 } } }, { failed: 3 }))).toBe(6)
  })
  it('questionRows: 先頭 5・残りは全体の件数から', () => {
    const q = (i: number) => ({ op: 'o', term: { id: `t${i}`, name: `語${i}` }, text: `問い${i}`, count: 1 })
    const r = result({ questions: Array.from({ length: 20 }, (_, i) => q(i)), questions_total: 38 })
    const { shown, more } = questionRows(r)
    expect(shown).toHaveLength(5)
    expect(more).toBe(33)
    expect(questionRows(result({ questions: [q(1)], questions_total: 1 })).more).toBe(0)
  })
})
