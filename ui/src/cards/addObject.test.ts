import { describe, expect, it } from 'vitest'
import type { ClassEntry } from './cardsApi'
import { normalizeSearchQuery, resolveInitialClassIri, sortClassEntries } from './AddObjectView'

// 架空の分野（図書館の貸出記録）で確かめる。AddObjectView.tsx の純関数は
// class_iri/件数/名前の形しか見ない — 分野語の辞書は持たない。

function entry(over: Partial<ClassEntry>): ClassEntry {
  return {
    class_iri: 'https://example.org/onto/Book',
    label: '本',
    count: 0,
    dataset_id: 'ds-1',
    dataset_label: '貸出記録',
    is_demo: false,
    properties: 3,
    with_label: 3,
    with_unit: 0,
    ...over,
  }
}

describe('sortClassEntries', () => {
  it('件数の多い順に並べる', () => {
    const items = [entry({ class_iri: 'a', label: 'あ', count: 3 }), entry({ class_iri: 'b', label: 'い', count: 10 })]
    expect(sortClassEntries(items).map((e) => e.class_iri)).toEqual(['b', 'a'])
  })

  it('件数が同じなら名前順', () => {
    const items = [entry({ class_iri: 'a', label: 'ろ', count: 5 }), entry({ class_iri: 'b', label: 'い', count: 5 })]
    expect(sortClassEntries(items).map((e) => e.class_iri)).toEqual(['b', 'a'])
  })

  it('入力を書き換えない', () => {
    const items = [entry({ class_iri: 'a', count: 1 }), entry({ class_iri: 'b', count: 2 })]
    const original = [...items]
    sortClassEntries(items)
    expect(items).toEqual(original)
  })
})

describe('normalizeSearchQuery', () => {
  it('空でも block しない（空文字のまま返す — 呼び出し側はこれで検索を続ける）', () => {
    expect(normalizeSearchQuery('')).toBe('')
  })

  it('前後の空白を落とす', () => {
    expect(normalizeSearchQuery('  夏目漱石  ')).toBe('夏目漱石')
  })
})

describe('resolveInitialClassIri', () => {
  it('明示の initialClassIri があればそれを優先する', () => {
    expect(resolveInitialClassIri('https://example.org/onto/Book', 'https://example.org/onto/Author')).toBe(
      'https://example.org/onto/Book',
    )
  })

  it('明示が無ければ sessionStorage に控えられた値を使う（種類のページの「＋ 追加」引き継ぎ）', () => {
    expect(resolveInitialClassIri(undefined, 'https://example.org/onto/Book')).toBe('https://example.org/onto/Book')
  })

  it('どちらも無ければ undefined（種類は選び直し）', () => {
    expect(resolveInitialClassIri(undefined, null)).toBeUndefined()
  })
})
