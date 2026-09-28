import { describe, expect, it } from 'vitest'
import { parseHash, routeToHash, type Route } from './App'

// parseHash/routeToHash の往復（契約メモ contract_pr_c.md §6.2・§6.4）。cards の
// route（subjectKey/cardId/place）を中心に、既存の route も壊していないことを
// 確認する。テストデータは架空（分野語ゼロ）。

function roundTrip(hash: string): string {
  return routeToHash(parseHash(hash))
}

describe('parseHash / routeToHash — 既定ルート（契約メモ contract_pr_e.md §3）', () => {
  it('空文字は cards タブ（見る）', () => {
    expect(parseHash('')).toEqual<Route>({ tab: 'cards' })
  })

  it('#/ も cards タブ', () => {
    expect(parseHash('#/')).toEqual<Route>({ tab: 'cards' })
  })

  it('# だけ（末尾スラッシュ無し）も cards タブ', () => {
    expect(parseHash('#')).toEqual<Route>({ tab: 'cards' })
  })
})

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

describe('parseHash / routeToHash — cards/d（データセットのページ・契約メモ contract_pr_f2.md §2.2）', () => {
  it('#/cards/d/<id> はデータセットのページ', () => {
    const hash = '#/cards/d/ds-1'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'cards', datasetPageId: 'ds-1' })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('id に記号が入っていても往復する', () => {
    const hash = `#/cards/d/${encodeURIComponent('ds/中央 42')}`
    expect(parseHash(hash)).toEqual<Route>({ tab: 'cards', datasetPageId: 'ds/中央 42' })
    expect(roundTrip(hash)).toBe(hash)
  })
})

describe('parseHash / routeToHash — cards/d/<id>/define,details（見るの枠の中で開く子ルート・契約メモ contract_pr_f5.md §1.1）', () => {
  it('#/cards/d/<id>/define はウィザードを見るの枠の中で開く', () => {
    const hash = '#/cards/d/ds-1/define'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'cards', datasetPageId: 'ds-1', datasetSub: 'define' })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('#/cards/d/<id>/details は詳しい情報を見るの枠の中で開く', () => {
    const hash = '#/cards/d/ds-1/details'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'cards', datasetPageId: 'ds-1', datasetSub: 'details' })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('#/cards/d/<id>/details/<detailTab> は詳細内タブも往復する', () => {
    const hash = '#/cards/d/ds-1/details/tools'
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      datasetPageId: 'ds-1',
      datasetSub: 'details',
      detailTab: 'tools',
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('details/structure（既定タブ）は短い形に丸まる', () => {
    expect(roundTrip('#/cards/d/ds-1/details/structure')).toBe('#/cards/d/ds-1/details')
  })

  it('既存の #/cards/d/<id>（子ルート無し）は今までどおり', () => {
    const hash = '#/cards/d/ds-1'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'cards', datasetPageId: 'ds-1' })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('id に記号が入っていても /define が往復する', () => {
    const hash = `#/cards/d/${encodeURIComponent('ds/中央 42')}/define`
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      datasetPageId: 'ds/中央 42',
      datasetSub: 'define',
    })
    expect(roundTrip(hash)).toBe(hash)
  })
})

describe('parseHash / routeToHash — cards/s/new（条件で集める・新規作成・契約メモ §2.4）', () => {
  it('#/cards/s/new?dataset=<id> は setNew + setDatasetId', () => {
    const hash = '#/cards/s/new?dataset=ds-1'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'cards', setNew: true, setDatasetId: 'ds-1' })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('#/cards/s/new?dataset=<id>&class=<iri> は setClassIri も持つ', () => {
    const iri = 'https://example.org/class/loan'
    const hash = `#/cards/s/new?dataset=ds-1&class=${encodeURIComponent(iri)}`
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      setNew: true,
      setDatasetId: 'ds-1',
      setClassIri: iri,
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('#/cards/s/new（クエリ無し）も setNew だけは立つ', () => {
    const route = parseHash('#/cards/s/new')
    expect(route).toEqual<Route>({ tab: 'cards', setNew: true, setDatasetId: undefined, setClassIri: undefined })
  })

  it('`new` で始まらない set_id は従来どおり subjectKey（誤検出しない）', () => {
    const hash = '#/cards/s/newton-station-9'
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      subjectKey: 's:newton-station-9',
      cardId: undefined,
    })
    expect(roundTrip(hash)).toBe(hash)
  })
})

describe('parseHash / routeToHash — workbench の returnTo（契約メモ §2.6）', () => {
  it('#/workbench はクエリ無しのまま往復する', () => {
    expect(parseHash('#/workbench')).toEqual<Route>({ tab: 'workbench' })
    expect(roundTrip('#/workbench')).toBe('#/workbench')
  })

  it('#/workbench?returnTo=<encoded hash> が往復する', () => {
    const hash = `#/workbench?returnTo=${encodeURIComponent('#/cards/d/ds-1')}`
    expect(parseHash(hash)).toEqual<Route>({ tab: 'workbench', returnTo: '#/cards/d/ds-1' })
    expect(roundTrip(hash)).toBe(hash)
  })
})

describe('parseHash / routeToHash — cards/add（データを追加・唯一の入口・契約メモ contract_pr_f8.md §1.1）', () => {
  it('#/datasets/add は gallery タブ + add', () => {
    expect(parseHash('#/datasets/add')).toEqual<Route>({ tab: 'gallery', add: true })
    expect(roundTrip('#/datasets/add')).toBe('#/datasets/add')
  })

  it('#/datasets/add?dataset=<id> は placeDatasetId も持つ', () => {
    const hash = '#/datasets/add?dataset=ds-42'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'gallery', add: true, placeDatasetId: 'ds-42' })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('id に記号が入っていても往復する', () => {
    const hash = `#/datasets/add?dataset=${encodeURIComponent('ds/中央 42')}`
    expect(parseHash(hash)).toEqual<Route>({ tab: 'gallery', add: true, placeDatasetId: 'ds/中央 42' })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('`add` は予約語 — 実在のデータセット id と誤検出しない', () => {
    const hash = '#/datasets/addendum-42'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'gallery', datasetId: 'addendum-42', detailTab: undefined })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('#/cards/place は旧 URL のまま往復する（置き換えは CardsView が担う）', () => {
    expect(parseHash('#/cards/place')).toEqual<Route>({ tab: 'cards', place: true })
    expect(roundTrip('#/cards/place')).toBe('#/cards/place')
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
