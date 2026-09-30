import { describe, expect, it } from 'vitest'
import {
  clearCardPresentation,
  parseStoredPresentation,
  readCardPresentation,
  serializePresentation,
  writeCardPresentation,
} from './cardPresentation'

describe('parseStoredPresentation: localStorage が無い／壊れているときに既定（undefined）に戻る', () => {
  it('null（キーが無い）は既定', () => {
    expect(parseStoredPresentation(null)).toBeUndefined()
  })

  it('空文字は既定', () => {
    expect(parseStoredPresentation('')).toBeUndefined()
  })

  it('壊れた JSON は既定', () => {
    expect(parseStoredPresentation('{not json')).toBeUndefined()
  })

  it('オブジェクトでない値（配列・プリミティブ）は既定', () => {
    expect(parseStoredPresentation('[1,2,3]')).toBeUndefined()
    expect(parseStoredPresentation('"line"')).toBeUndefined()
    expect(parseStoredPresentation('42')).toBeUndefined()
  })

  it('往復する（serializePresentation と対）', () => {
    const p = { mark: 'bar' as const, swapXY: false, colorBy: null }
    expect(parseStoredPresentation(serializePresentation(p))).toEqual(p)
  })
})

describe('readCardPresentation: localStorage 自体が無い環境（このテストランタイム）では既定に戻る', () => {
  it('typeof localStorage === "undefined"（テスト環境の前提）', () => {
    expect(typeof localStorage).toBe('undefined')
  })

  it('例外を投げず undefined を返す', () => {
    expect(readCardPresentation('any-card-id')).toBeUndefined()
  })
})

describe('localStorage が無くても、このタブの中では選んだ見せ方が効く', () => {
  it('書いたものを読める・消すと既定に戻る', () => {
    writeCardPresentation('card-in-session', { mark: 'bar', swapXY: false, colorBy: null })
    expect(readCardPresentation('card-in-session')).toEqual({ mark: 'bar', swapXY: false, colorBy: null })
    clearCardPresentation('card-in-session')
    expect(readCardPresentation('card-in-session')).toBeUndefined()
  })

  it('別のカードには及ばない', () => {
    writeCardPresentation('card-a', { mark: 'point' })
    expect(readCardPresentation('card-b')).toBeUndefined()
    clearCardPresentation('card-a')
  })
})
