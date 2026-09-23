import { describe, expect, it } from 'vitest'
import { parseHash, routeToHash, type Route } from './App'

// parseHash/routeToHash の往復（契約メモ contract_pr_c.md §6.2・§6.4）。cards の
// route（subjectKey/cardId/place）を中心に、既存の route も壊していないことを
// 確認する。テストデータは架空（分野語ゼロ）。

function roundTrip(hash: string): string {
  return routeToHash(parseHash(hash))
}

describe('parseHash / routeToHash — cards（object-cards-ui）', () => {
  it('#/cards は素の cards タブ', () => {
    expect(parseHash('#/cards')).toEqual<Route>({ tab: 'cards' })
    expect(roundTrip('#/cards')).toBe('#/cards')
  })

  it('#/cards/place は「データを置く」', () => {
    expect(parseHash('#/cards/place')).toEqual<Route>({ tab: 'cards', place: true })
    expect(roundTrip('#/cards/place')).toBe('#/cards/place')
  })

  it('#/cards/place?dataset=<id> はかんたんウィザードから戻ってきたデータを置く画面', () => {
    const hash = '#/cards/place?dataset=ds-42'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'cards', place: true, placeDatasetId: 'ds-42' })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('dataset の id に記号が入っていても往復する', () => {
    const hash = `#/cards/place?dataset=${encodeURIComponent('ds/中央 42')}`
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      place: true,
      placeDatasetId: 'ds/中央 42',
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('#/cards/i/<iri> は 1 件のページ（subjectKey は i: 接頭辞つき）', () => {
    const hash = `#/cards/i/${encodeURIComponent('https://example.org/loan/42')}`
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      subjectKey: 'i:https://example.org/loan/42',
      cardId: undefined,
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('#/cards/s/<set_id> は絞り込みのページ（subjectKey は s: 接頭辞つき）', () => {
    const hash = '#/cards/s/set-abc123def456'
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      subjectKey: 's:set-abc123def456',
      cardId: undefined,
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('.../c/<card_id> はカード詳細（1 件）', () => {
    const hash = `#/cards/i/${encodeURIComponent('https://example.org/station/north')}/c/card-deadbeef0000`
    const route = parseHash(hash)
    expect(route).toEqual<Route>({
      tab: 'cards',
      subjectKey: 'i:https://example.org/station/north',
      cardId: 'card-deadbeef0000',
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('.../c/<card_id> はカード詳細（絞り込み）', () => {
    const hash = '#/cards/s/set-abc123def456/c/card-deadbeef0000'
    const route = parseHash(hash)
    expect(route).toEqual<Route>({
      tab: 'cards',
      subjectKey: 's:set-abc123def456',
      cardId: 'card-deadbeef0000',
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('IRI に含まれる記号（? & # 相当）も往復する', () => {
    const iri = 'https://example.org/loan?id=42&branch=中央'
    const hash = `#/cards/i/${encodeURIComponent(iri)}`
    expect(parseHash(hash).subjectKey).toBe(`i:${iri}`)
    expect(roundTrip(hash)).toBe(hash)
  })
})

describe('parseHash / routeToHash — 既存の route を壊さない', () => {
  it('#/home', () => {
    expect(roundTrip('#/home')).toBe('#/home')
  })

  it('#/datasets/<id> は gallery タブ + datasetId', () => {
    const hash = '#/datasets/ds-1'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'gallery', datasetId: 'ds-1', detailTab: undefined })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('#/datasets/<id>/tools は detailTab も往復する', () => {
    const hash = '#/datasets/ds-1/tools'
    expect(roundTrip(hash)).toBe(hash)
  })

  it('#/crosswalk/new', () => {
    expect(roundTrip('#/crosswalk/new')).toBe('#/crosswalk/new')
  })

  it('#/ask/<threadId>', () => {
    const hash = '#/ask/thread-1'
    expect(roundTrip(hash)).toBe(hash)
  })

  it('不明な hash は home に倒す', () => {
    expect(parseHash('#/nonexistent')).toEqual<Route>({ tab: 'home' })
  })
})
