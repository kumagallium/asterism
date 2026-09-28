import { describe, expect, it } from 'vitest'
import { BUILTIN_TITLE_PREFIX, builtinTitleKey, resolveCardTitle } from './cardTitle'

// 架空の 2 分野（図書館の貸出／気象観測）のツール名で確かめる。分野語は
// 判定条件に無関係（判定は文字列の接頭辞だけ）なのが cardTitle.ts の要点。

describe('builtinTitleKey', () => {
  it('組み込みツール名から cards:builtin.<tool> を組み立てる', () => {
    expect(builtinTitleKey('subject_facts')).toBe('cards:builtin.subject_facts')
    expect(builtinTitleKey('set_breakdown')).toBe('cards:builtin.set_breakdown')
  })
})

describe('resolveCardTitle', () => {
  it('cards:builtin. で始まる title は i18n キーと判定する', () => {
    expect(resolveCardTitle('cards:builtin.subject_facts')).toEqual({
      isKey: true,
      value: 'cards:builtin.subject_facts',
    })
    expect(resolveCardTitle('cards:builtin.set_members')).toEqual({
      isKey: true,
      value: 'cards:builtin.set_members',
    })
  })

  it('宣言ツールの人が読める title はそのまま表示文字列と判定する（分野語を含んでいてもよい）', () => {
    expect(resolveCardTitle('今月の貸出冊数')).toEqual({ isKey: false, value: '今月の貸出冊数' })
    expect(resolveCardTitle('観測地点別の月別降水量')).toEqual({
      isKey: false,
      value: '観測地点別の月別降水量',
    })
  })

  it('別の namespace の i18n キー（cards:builtin. で始まらない）はそのまま表示文字列と判定する', () => {
    expect(resolveCardTitle('gallery:tab.structure')).toEqual({
      isKey: false,
      value: 'gallery:tab.structure',
    })
  })

  it('空文字列はそのまま表示文字列（isKey: false）', () => {
    expect(resolveCardTitle('')).toEqual({ isKey: false, value: '' })
  })

  it('builtinTitleKey の往復: 組み立てたキーは必ず isKey: true と判定される', () => {
    for (const tool of ['subject_facts', 'subject_sources', 'subject_flow', 'set_members', 'set_breakdown', 'set_count']) {
      const key = builtinTitleKey(tool)
      expect(key.startsWith(BUILTIN_TITLE_PREFIX)).toBe(true)
      expect(resolveCardTitle(key)).toEqual({ isKey: true, value: key })
    }
  })
})
