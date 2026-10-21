import { describe, expect, it } from 'vitest'
import { canSwitch, conversationPath, dataDest, keepPick, stageMark } from './conversationContext'

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
  it('段が変わったら選びは消える（戻ってきても蘇らない）', () => {
    const pick = { recipe: 3 as const, dest: 'consult' as const }
    expect(keepPick(pick, 3)).toBe(pick)
    expect(keepPick(pick, 4)).toBeNull()
    expect(keepPick(pick, null)).toBeNull()
    expect(keepPick(null, 3)).toBeNull()
    // 4 に進んで消えたあと、③に戻っても選び（consult）は無く、既定の bind
    expect(dataDest(h(3, true), keepPick(keepPick(pick, 4), 3))).toBe('bind')
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
  it('星を選んでいても、人が「問いを書く」を選んでいれば問い（既定は星について聞く）', () => {
    expect(conversationPath({ ...base, starSelected: true, writing: false })).toBe('star')
    expect(conversationPath({ ...base, starSelected: true, writing: true })).toBe('ask')
    // 下書きが開いていれば下書きの相談が先（問いを書くは下書きの外の話）
    expect(conversationPath({ ...base, starSelected: true, writing: true, draftOpen: true })).toBe('draft')
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
