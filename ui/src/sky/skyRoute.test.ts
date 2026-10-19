import { describe, expect, it } from 'vitest'
import { homeHash, parseHomeQuery } from './skyRoute'

describe('星図の hash のクエリ', () => {
  it('素の #/home', () => {
    expect(homeHash()).toBe('#/home')
    expect(parseHomeQuery('')).toEqual({ deep: false, newIris: [] })
  })
  it('中身まで見る', () => {
    expect(homeHash({ deep: true })).toBe('#/home?deep=1')
    expect(parseHomeQuery('deep=1')).toEqual({ deep: true, newIris: [] })
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
    expect(parseHomeQuery(h.split('?')[1])).toEqual({ deep: true, newIris: ['x:y'] })
  })
  it('壊れた % があっても落ちない', () => {
    expect(parseHomeQuery('new=%E0%A4%A').newIris).toEqual(['%E0%A4%A'])
  })
})
