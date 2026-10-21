import { describe, expect, it } from 'vitest'
import { homeHash, parseHomeQuery, startQuestionHash } from './skyRoute'

describe('星図の hash のクエリ', () => {
  it('素の #/home', () => {
    expect(homeHash()).toBe('#/home')
    expect(parseHomeQuery('')).toEqual({ deep: false, newIris: [], write: null, add: false, dataset: '' })
  })
  it('中身まで見る', () => {
    expect(homeHash({ deep: true })).toBe('#/home?deep=1')
    expect(parseHomeQuery('deep=1')).toEqual({ deep: true, newIris: [], write: null, add: false, dataset: '' })
    expect(parseHomeQuery('deep=0').deep).toBe(false)
  })
  it('生まれた輪: IRI（# や / や , を含む）が往復する', () => {
    const iris = ['https://example.org/a#K1', 'https://example.org/b,c/K2']
    const h = homeHash({ newIris: iris })
    expect(h.startsWith('#/home?new=')).toBe(true)
    // 区切りの , 以外の生の , は残らない
    expect(h.split('?')[1].replace(/^new=/, '').split(',')).toHaveLength(2)
    expect(parseHomeQuery(h.split('?')[1]).newIris).toEqual(iris)
  })
  it('deep と new が同居しても往復する', () => {
    const h = homeHash({ deep: true, newIris: ['x:y'] })
    expect(h).toBe('#/home?deep=1&new=x%3Ay')
    expect(parseHomeQuery(h.split('?')[1])).toEqual({ deep: true, newIris: ['x:y'], write: null, add: false, dataset: '' })
  })
  it('問いを書く: 題・続きの下書き・問いを足すテーマが往復する', () => {
    expect(homeHash({ write: { q: '', resume: '', extend: null } })).toBe('#/home?write=1')
    const w = { q: '熱伝導率を下げる要素は？', resume: 'd-1', extend: { id: 'first', label: 'はじめの問い' } }
    const h = homeHash({ write: w })
    expect(parseHomeQuery(h.split('?')[1]).write).toEqual(w)
    // write=1 が無ければ、q や resume があっても会話は開かない
    expect(parseHomeQuery('q=abc').write).toBeNull()
  })
  it('データを置く: add=1（と見直すデータセット）が往復する', () => {
    expect(homeHash({ add: true })).toBe('#/home?add=1')
    expect(parseHomeQuery('add=1')).toMatchObject({ add: true, dataset: '' })
    const h = homeHash({ add: true, dataset: 'ds/中央 42' })
    expect(h).toBe(`#/home?add=1&dataset=${encodeURIComponent('ds/中央 42')}`)
    expect(parseHomeQuery(h.split('?')[1])).toMatchObject({ add: true, dataset: 'ds/中央 42' })
  })
  it('add=1 が無ければ dataset だけでは何も開かない', () => {
    expect(parseHomeQuery('dataset=ds-1')).toMatchObject({ add: false, dataset: '' })
    expect(homeHash({ dataset: 'ds-1' })).toBe('#/home')
  })
  it('壊れた % があっても落ちない', () => {
    expect(parseHomeQuery('new=%E0%A4%A').newIris).toEqual(['%E0%A4%A'])
  })
})

describe('「この問いで始める」（問いの提案）', () => {
  it('#/home?write=1&q=<問い> で、問いを書く道に問いが入る形で往復する', () => {
    const q = '食材ごとに 値段の記録は何件ある？ (A&B)'
    const h = startQuestionHash(q)
    expect(h).toBe(`#/home?write=1&q=${encodeURIComponent(q)}`)
    expect(parseHomeQuery(h.split('?')[1]).write).toEqual({ q, resume: '', extend: null })
  })
})
