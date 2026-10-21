import { describe, expect, it } from 'vitest'
import { canSwitch, conversationPath, dataDest, stageMark } from './conversationContext'

const h = (recipe: 1 | 2 | 3 | 4 | 5 | 6 | 7 | null, bindReady = false) => ({ recipe, bindReady })

describe('データの道の送り先は段で決まる', () => {
  it('③で設計ができていれば列の結びつき（bind）', () => {
    expect(dataDest(h(3, true), null)).toBe('bind')
  })
  it('③でも設計がまだ無ければ設計の相談（会話は設計ができてから）', () => {
    expect(dataDest(h(3, false), null)).toBe('consult')
  })
  it('③以外は設計の相談。会話が出ていても（bindReady）③でなければ bind にしない', () => {
    for (const r of [1, 2, 4, 5, 6, 7] as const) expect(dataDest(h(r, true), null)).toBe('consult')
    expect(dataDest(h(null), null)).toBe('consult')
  })
  it('人の選びは、選んだ段のあいだだけ効く', () => {
    expect(dataDest(h(3, true), { recipe: 3, dest: 'consult' })).toBe('consult')
    expect(dataDest(h(3, true), { recipe: 4, dest: 'consult' })).toBe('bind')
  })
  it('切り替えられるのは③で設計ができているときだけ', () => {
    expect(canSwitch(h(3, true))).toBe(true)
    expect(canSwitch(h(3, false))).toBe(false)
    expect(canSwitch(h(5, true))).toBe(false)
  })
})

describe('左の会話の道', () => {
  const base = { adding: false, hosted: h(null), pick: null, draftOpen: false, starSelected: false }
  it('星図: 下書きが先・次に星・どちらも無ければ問い', () => {
    expect(conversationPath(base)).toBe('ask')
    expect(conversationPath({ ...base, starSelected: true })).toBe('star')
    expect(conversationPath({ ...base, starSelected: true, draftOpen: true })).toBe('draft')
  })
  it('データを置いている間は、星・下書きより段が先', () => {
    expect(conversationPath({ ...base, adding: true, draftOpen: true, starSelected: true, hosted: h(3, true) })).toBe('bind')
    expect(conversationPath({ ...base, adding: true, draftOpen: true, hosted: h(5, true) })).toBe('consult')
  })
})

describe('段の印', () => {
  it('①〜⑦', () => {
    expect([1, 2, 3, 4, 5, 6, 7].map((n) => stageMark(n as 1))).toEqual(['①', '②', '③', '④', '⑤', '⑥', '⑦'])
  })
})
