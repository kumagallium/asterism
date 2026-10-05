import { describe, expect, it } from 'vitest'
import { placedSourceNames } from './galleryApi'

describe('placedSourceNames — 利用者が置いたファイル名で見せる', () => {
  it('保存名を置いた名前に直す。対応が無いものは保存名のまま', () => {
    expect(
      placedSourceNames({
        source_files: ['source-3637d45e.csv', 'prices.csv'],
        source_names: { 'source-3637d45e.csv': '価格表.csv' },
      }),
    ).toEqual(['価格表.csv', 'prices.csv'])
  })

  it('同じ名前は 1 回だけ（複数シートのブックなど）。順序は保つ', () => {
    expect(
      placedSourceNames({
        source_files: ['a.csv', 'b.csv', 'c.csv'],
        source_names: { 'a.csv': 'book.xlsx', 'b.csv': 'book.xlsx' },
      }),
    ).toEqual(['book.xlsx', 'c.csv'])
  })

  it('置き直しの案内では、変換してできたファイルは保存名のまま出す', () => {
    const meta = {
      source_files: ['source-3637d45e.csv', 'source-a5e26419.csv'],
      source_names: {
        'source-3637d45e.csv': '価格表.csv', // 同じ名前で置き直せる
        'source-a5e26419.csv': '在庫.xlsx', // ブックの名前では CSV を置き直せない
      },
    }
    expect(placedSourceNames(meta)).toEqual(['価格表.csv', '在庫.xlsx'])
    expect(placedSourceNames(meta, true)).toEqual(['価格表.csv', 'source-a5e26419.csv'])
  })

  it('source_names が無い・source_files が無いときも壊れない', () => {
    expect(placedSourceNames({ source_files: ['x.csv'] })).toEqual(['x.csv'])
    expect(placedSourceNames({})).toEqual([])
  })
})
