import { describe, expect, it } from 'vitest'
import type { ClassEntry, SubjectSearchItem } from './cardsApi'
import {
  appendResultsPage,
  countLineParams,
  moreButtonParams,
  normalizeSearchQuery,
  resolveInitialClassIri,
  sortClassEntries,
} from './AddObjectView'

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

// 契約メモ contract_pr_f10.md §1.1: 一覧の上の件数（全部／一部／検索中の 3 態）。

describe('countLineParams', () => {
  it('検索中は q が空でも count_search（committedQuery が空文字でない場合のみ呼ばれる前提だが、関数自体は判定しない）', () => {
    expect(countLineParams('本', 3, 10)).toEqual({ key: 'add.count_search', params: { q: '本', shown: 3 } })
  })

  it('検索していなくて全部出ていれば count_all', () => {
    expect(countLineParams('', 10, 10)).toEqual({ key: 'add.count_all', params: { total: 10 } })
  })

  it('検索していなくて一部だけなら count_partial', () => {
    expect(countLineParams('', 60, 200)).toEqual({ key: 'add.count_partial', params: { total: 200, shown: 60 } })
  })
})

describe('moreButtonParams', () => {
  it('残りが無ければ null（ボタンを出さない）', () => {
    expect(moreButtonParams(10, 10, 60)).toBeNull()
    expect(moreButtonParams(10, 5, 60)).toBeNull()
  })

  it('残りがページより多ければ n はページ分', () => {
    expect(moreButtonParams(60, 200, 60)).toEqual({ n: 60, rest: 140 })
  })

  it('残りがページより少なければ n は残り分そのまま', () => {
    expect(moreButtonParams(180, 200, 60)).toEqual({ n: 20, rest: 20 })
  })
})

describe('appendResultsPage', () => {
  function item(iri: string): SubjectSearchItem {
    return { iri, label: iri, class_iri: null, class_label: null, dataset_id: null }
  }

  it('新しい主語だけ足す', () => {
    const existing = [item('a'), item('b')]
    const page = [item('c'), item('d')]
    expect(appendResultsPage(existing, page).map((i) => i.iri)).toEqual(['a', 'b', 'c', 'd'])
  })

  it('同じ主語が来たら二重に足さない（offset のずれの安全側）', () => {
    const existing = [item('a'), item('b')]
    const page = [item('b'), item('c')]
    expect(appendResultsPage(existing, page).map((i) => i.iri)).toEqual(['a', 'b', 'c'])
  })

  it('既存を書き換えない', () => {
    const existing = [item('a')]
    const original = [...existing]
    appendResultsPage(existing, [item('b')])
    expect(existing).toEqual(original)
  })
})
