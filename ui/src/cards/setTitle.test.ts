import { describe, expect, it } from 'vitest'
import { formatClause, formatSetSubtitle, formatSetTitle } from './setTitle'
import type { SetResolveClause, SetResolveResult } from './cardsApi'

// 架空の 2 分野（図書館の貸出／気象観測）で確かめる。テンプレート文言は
// `t()` スタブが素通しする（実際の文言は cards.json 側でテストしない —
// ここでは組み立て・数値整形・単位の空白だけを確かめる）。

const t = (key: string, options?: Record<string, unknown>): string => {
  const opts = options ? JSON.stringify(options) : ''
  return `${key}${opts}`
}

describe('formatClause', () => {
  it('gt: 数値は桁区切り、数値と単位の間に半角空白を入れる', () => {
    const clause: SetResolveClause = { property_label: '貸出日数', op: 'gt', value: 10000, unit: 'u' }
    const out = formatClause(clause, t)
    expect(out).toContain('cards:set.op.gt')
    expect(out).toContain('"value":"10,000 u"')
    expect(out).toContain('"property":"貸出日数"')
  })

  it('lt/eq も同じ形で桁区切り・単位の空白を持つ', () => {
    expect(formatClause({ property_label: 'ページ数', op: 'lt', value: 100, unit: null }, t)).toContain('"value":"100"')
    expect(formatClause({ property_label: '降水量', op: 'eq', value: 5, unit: 'mm' }, t)).toContain('"value":"5 mm"')
  })

  it('between: min〜max の範囲を組み、単位は末尾に 1 回だけ半角空白を入れて付く', () => {
    const clause: SetResolveClause = { property_label: '降水量', op: 'between', value: { min: 10, max: 2000 }, unit: 'mm' }
    const out = formatClause(clause, t)
    expect(out).toContain('cards:set.op.between')
    expect(out).toContain('"range":"10〜2,000 mm"')
  })

  it('in: 値を「・」でつなぐ', () => {
    const clause: SetResolveClause = { property_label: 'ジャンル', op: 'in', value: ['fiction', 'poetry'] }
    const out = formatClause(clause, t)
    expect(out).toContain('cards:set.op.in')
    expect(out).toContain('"values":"fiction・poetry"')
  })

  it('単位が無ければ数値の直後に何も足さない', () => {
    const out = formatClause({ property_label: 'ページ数', op: 'gt', value: 100, unit: null }, t)
    expect(out).toContain('"value":"100"')
  })
})

describe('formatSetTitle', () => {
  it('条件が無ければ class_label のみ', () => {
    const title: SetResolveResult['title'] = { class_label: '貸出', clauses: [] }
    expect(formatSetTitle(title, t)).toBe('貸出')
  })

  it('条件があれば class_label: 条件1、条件2… の形', () => {
    const title: SetResolveResult['title'] = {
      class_label: '観測記録',
      clauses: [
        { property_label: '降水量', op: 'gt', value: 100, unit: 'mm' },
        { property_label: '観測地点', op: 'in', value: ['north'] },
      ],
    }
    const out = formatSetTitle(title, t)
    expect(out.startsWith('観測記録: ')).toBe(true)
    expect(out).toContain('cards:set.op.gt')
    expect(out).toContain('cards:set.op.in')
  })

  it('同じ class_label でも clauses が違えば区別できる（レールの項目 label に使う値）', () => {
    const a: SetResolveResult['title'] = {
      class_label: '観測記録',
      clauses: [{ property_label: '降水量', op: 'gt', value: 100, unit: 'mm' }],
    }
    const b: SetResolveResult['title'] = {
      class_label: '観測記録',
      clauses: [{ property_label: '降水量', op: 'lt', value: 10, unit: 'mm' }],
    }
    expect(formatSetTitle(a, t)).not.toBe(formatSetTitle(b, t))
  })
})

describe('formatSetSubtitle', () => {
  it('n が null の間は loading 用のキーを使う', () => {
    const out = formatSetSubtitle('観測記録', null, t)
    expect(out).toContain('cards:set.subtitle_loading')
    expect(out).toContain('"className":"観測記録"')
  })

  it('n が届けば桁区切りして subtitle キーを使う', () => {
    const out = formatSetSubtitle('観測記録', 12345, t)
    expect(out).toContain('cards:set.subtitle')
    expect(out).not.toContain('subtitle_loading')
    expect(out).toContain('"n":"12,345"')
  })

  it('決定論: 同じ入力には同じ出力', () => {
    const title: SetResolveResult['title'] = {
      class_label: '貸出',
      clauses: [{ property_label: 'ページ数', op: 'gt', value: 100, unit: null }],
    }
    expect(formatSetTitle(title, t)).toBe(formatSetTitle(title, t))
  })
})
