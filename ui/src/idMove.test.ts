import { describe, expect, it } from 'vitest'
import type { IdMove } from './api'
import { idMoveNoticeView } from './idMove'
import enGallery from './i18n/locales/en/gallery.json'
import enKantan from './i18n/locales/en/kantan.json'
import jaGallery from './i18n/locales/ja/gallery.json'
import jaKantan from './i18n/locales/ja/kantan.json'

const base: IdMove = { dataset_id: 'd', changes_ids: true }

describe('idMoveNoticeView — 「ID の引っ越し」の知らせが言うこと', () => {
  it('住所が動かないとき・まだ読めていないときは、何も言わない', () => {
    expect(idMoveNoticeView(null)).toBeNull()
    expect(idMoveNoticeView(undefined)).toBeNull()
    expect(idMoveNoticeView({ dataset_id: 'd', changes_ids: false })).toBeNull()
  })

  it('全部引き継げるときは、書けた件数だけを言う（警告にしない）', () => {
    const view = idMoveNoticeView({ ...base, fully_movable: true, forwarded: 1204, blocked: [] })
    expect(view).toEqual({ broken: false, forwarded: 1204, blocked: [] })
  })

  it('引き継げないぶんは、ファイルと理由を 1 件ずつ言う', () => {
    const view = idMoveNoticeView({
      ...base,
      fully_movable: false,
      forwarded: 7,
      blocked: [
        { name: 'record', source: 'prices.csv', reason: 'missing_columns', missing_columns: ['番号'] },
        { name: 'shop', source: 'shops.csv', reason: 'no_matching_map', missing_columns: [] },
      ],
    })
    expect(view?.broken).toBe(true)
    expect(view?.forwarded).toBe(7)
    expect(view?.blocked).toEqual([
      { key: 'prices.csv:record', source: 'prices.csv', reason: 'columns', columns: ['番号'] },
      { key: 'shops.csv:shop', source: 'shops.csv', reason: 'kind', columns: [] },
    ])
  })

  it('件数が記録に無いときは 0 として扱う（件数の文を出さない側に倒す）', () => {
    expect(idMoveNoticeView({ ...base, fully_movable: false })?.forwarded).toBe(0)
  })

  // 計画の上では全部引き継げる（moved だけ）のに、台帳づくりが落ちた記録。
  // api/tests/test_id_move.py が固定している形そのまま。blocked だけを読むと、
  // 警告の枠に「次のぶんは…」と出たあと一覧が空になる。
  const entry = (name: string, source: string) => ({
    name,
    old_name: name,
    source,
    old_template: `https://e.invalid/r/${name}/{a}`,
    new_template: `https://e.invalid/r/${name}/{a}-{b}`,
  })

  it('引っ越し先を記録できなかったときは、動くはずだったファイルのぶんを言う', () => {
    const view = idMoveNoticeView({
      ...base,
      fully_movable: false,
      ledger_error: true,
      forwarded: 0,
      moved: [entry('record', 'prices.csv')],
      blocked: [],
    })
    expect(view?.broken).toBe(true)
    expect(view?.blocked).toEqual([
      { key: 'prices.csv:ledger', source: 'prices.csv', reason: 'ledger', columns: [] },
    ])
  })

  it('記録できなかったぶんは、ファイルごとに 1 回だけ言う', () => {
    const view = idMoveNoticeView({
      ...base,
      fully_movable: false,
      ledger_error: true,
      moved: [entry('record', 'prices.csv'), entry('shop', 'prices.csv'), entry('item', 'items.csv')],
      blocked: [
        { name: 'maker', source: 'makers.csv', reason: 'no_matching_map', missing_columns: [] },
      ],
    })
    expect(view?.blocked.map((b) => [b.source, b.reason])).toEqual([
      ['makers.csv', 'kind'],
      ['prices.csv', 'ledger'],
      ['items.csv', 'ledger'],
    ])
  })

  it('記録できたときは、動いたぶんを「引き継げない」に数えない', () => {
    const view = idMoveNoticeView({
      ...base,
      fully_movable: true,
      forwarded: 7,
      moved: [entry('record', 'prices.csv')],
      blocked: [],
    })
    expect(view).toEqual({ broken: false, forwarded: 7, blocked: [] })
  })
})

// 戻り道は「その画面に実在するボタンの名前」で言う。ボタンの名前が変わったのに
// 文だけ残ると、無いボタンを案内することになる（実例: 「データの数えかたに戻る」
// というボタンは無かった）。
describe('戻り道の文は、実在するボタンの名前を引く', () => {
  it.each([
    ['ja', jaKantan],
    ['en', enKantan],
  ])('かんたんモードの「公開する」（%s）', (_lang, kantan) => {
    const exit = kantan.s8.idMoveBrokenExit
    expect(exit).toContain(kantan.s8.back)
    expect(exit).toContain(kantan.s6.backToGate)
  })

  it.each([
    ['ja', jaGallery],
    ['en', enGallery],
  ])('データセットの詳細の公開確認（%s）', (_lang, gallery) => {
    const exit = gallery.promote.idMoveBrokenExit
    expect(exit).toContain(gallery.promote.cancel)
    expect(exit).toContain(gallery.redesign.open)
  })
})
