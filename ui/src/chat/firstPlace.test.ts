import { describe, expect, it } from 'vitest'
import { firstPlace, parseLastPlace } from './firstPlace'

describe('firstPlace', () => {
  it('チャットもデータセットも 0 → 取り込み（覚えがあっても）', () => {
    expect(firstPlace({ last: null, sessions: 0, datasets: 0 })).toEqual({ kind: 'import' })
    expect(firstPlace({ last: { place: 'graph' }, sessions: 0, datasets: 0 })).toEqual({ kind: 'import' })
  })
  it('覚えている場所へ', () => {
    expect(firstPlace({ last: { place: 'import' }, sessions: 3, datasets: 2 })).toEqual({ kind: 'import' })
    expect(firstPlace({ last: { place: 'graph' }, sessions: 3, datasets: 2 })).toEqual({ kind: 'graph' })
    expect(firstPlace({ last: { place: 'chat', chat: 'cabc12345x' }, sessions: 3, datasets: 2, chatExists: true })).toEqual({ kind: 'session', id: 'cabc12345x' })
    expect(firstPlace({ last: { place: 'chat' }, sessions: 3, datasets: 2 })).toEqual({ kind: 'new' })
  })
  it('覚えたチャットが一覧に無ければ新しいチャット', () => {
    expect(firstPlace({ last: { place: 'chat', chat: 'cabc12345x' }, sessions: 3, datasets: 2, chatExists: false })).toEqual({ kind: 'new' })
  })
  it('覚え無し: チャットがあればチャット・データセットだけなら取り込み', () => {
    expect(firstPlace({ last: null, sessions: 2, datasets: 0 })).toEqual({ kind: 'new' })
    expect(firstPlace({ last: null, sessions: 2, datasets: 5 })).toEqual({ kind: 'new' })
    expect(firstPlace({ last: null, sessions: 0, datasets: 1 })).toEqual({ kind: 'import' })
  })
  it('読み込み失敗（null）: 覚えがあればそこ・無ければ取り込み', () => {
    expect(firstPlace({ last: { place: 'graph' }, sessions: null, datasets: null })).toEqual({ kind: 'graph' })
    expect(firstPlace({ last: null, sessions: null, datasets: null })).toEqual({ kind: 'import' })
    expect(firstPlace({ last: null, sessions: null, datasets: 3 })).toEqual({ kind: 'import' })
  })
})

describe('parseLastPlace', () => {
  it('正しい覚えを読む', () => {
    expect(parseLastPlace('{"place":"graph"}')).toEqual({ place: 'graph' })
    expect(parseLastPlace('{"place":"chat","chat":"cabc12345x"}')).toEqual({ place: 'chat', chat: 'cabc12345x' })
  })
  it('壊れた JSON・空・知らない place は null', () => {
    expect(parseLastPlace(null)).toBeNull()
    expect(parseLastPlace('')).toBeNull()
    expect(parseLastPlace('{oops')).toBeNull()
    expect(parseLastPlace('"graph"')).toBeNull()
    expect(parseLastPlace('{"place":"home"}')).toBeNull()
  })
  it('壊れた id は捨てて場所だけ残す', () => {
    expect(parseLastPlace('{"place":"chat","chat":"XYZ"}')).toEqual({ place: 'chat' })
    expect(parseLastPlace('{"place":"chat","chat":5}')).toEqual({ place: 'chat' })
  })
})
