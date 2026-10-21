import { describe, expect, it } from 'vitest'
import { legacyHashRewrite, parseHash, routeToHash, type Route } from './App'

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

describe('parseHash / routeToHash — cards/i/<iri>?solo=1（束ねずに単独で見る・契約メモ contract_pr_f19.md §1.3）', () => {
  it('#/cards/i/<iri>?solo=1 は solo: true', () => {
    const hash = `#/cards/i/${encodeURIComponent('https://example.org/loan/42')}?solo=1`
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      subjectKey: 'i:https://example.org/loan/42',
      cardId: undefined,
      solo: true,
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('.../c/<card_id>?solo=1 — c/<cardId> の後ろに付いても solo: true', () => {
    const hash = `#/cards/i/${encodeURIComponent('https://example.org/station/north')}/c/card-deadbeef0000?solo=1`
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      subjectKey: 'i:https://example.org/station/north',
      cardId: 'card-deadbeef0000',
      solo: true,
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('solo が無ければ従来どおり（solo は undefined）', () => {
    const hash = `#/cards/i/${encodeURIComponent('https://example.org/loan/42')}`
    expect(parseHash(hash).solo).toBeUndefined()
    expect(roundTrip(hash)).toBe(hash)
  })

  it('s:<set_id> にも solo が付けられる', () => {
    const hash = '#/cards/s/set-abc123def456?solo=1'
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      subjectKey: 's:set-abc123def456',
      cardId: undefined,
      solo: true,
    })
    expect(roundTrip(hash)).toBe(hash)
  })
})

describe('parseHash / routeToHash — cards/add（オブジェクトを追加・契約メモ contract_pr_f9.md §1-3）', () => {
  it('#/cards/add は cards タブ + add', () => {
    expect(parseHash('#/cards/add')).toEqual<Route>({ tab: 'cards', add: true })
    expect(roundTrip('#/cards/add')).toBe('#/cards/add')
  })

  it('`add` は予約語 — 実在の主語（i:/s:）とは別の接頭辞なので誤検出しない', () => {
    // `#/cards/add` は `#/cards/i/<iri>`・`#/cards/s/<set_id>` とは parts[1] の
    // 語彙が違う（add vs i/s）ため、実在の subject_key と衝突しない。
    expect(parseHash('#/cards/add').subjectKey).toBeUndefined()
  })
})

describe('parseHash / routeToHash — cards/k（種類のページ・契約メモ contract_pr_f9.md §1-4）', () => {
  it('#/cards/k/<class_iri> は cards タブ + classPageIri', () => {
    const hash = `#/cards/k/${encodeURIComponent('https://example.org/class/loan')}`
    expect(parseHash(hash)).toEqual<Route>({
      tab: 'cards',
      classPageIri: 'https://example.org/class/loan',
    })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('class_iri に記号が入っていても往復する', () => {
    const hash = `#/cards/k/${encodeURIComponent('https://example.org/class/loan?id=42&branch=中央')}`
    expect(parseHash(hash).classPageIri).toBe('https://example.org/class/loan?id=42&branch=中央')
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

describe('#/home（星図）— deep・new・旧 #/network', () => {
  it('素の #/home は何も足さない', () => {
    expect(parseHash('#/home')).toEqual<Route>({ tab: 'home' })
    expect(routeToHash({ tab: 'home' })).toBe('#/home')
  })

  it('#/home?deep=1 は「中身まで見る」（往復する）', () => {
    expect(parseHash('#/home?deep=1')).toEqual<Route>({ tab: 'home', deep: true })
    expect(routeToHash({ tab: 'home', deep: true })).toBe('#/home?deep=1')
    expect(roundTrip('#/home?deep=1')).toBe('#/home?deep=1')
  })

  it('旧 #/network は #/home?deep=1 に着く（置き換え）', () => {
    expect(parseHash('#/network')).toEqual<Route>({ tab: 'home', deep: true })
    expect(parseHash('#/network?x=1')).toEqual<Route>({ tab: 'home', deep: true })
    expect(legacyHashRewrite('#/network')).toBe('#/home?deep=1')
    expect(legacyHashRewrite('#/home?deep=1')).toBeNull()
  })

  it('#/home?new=<iri>,<iri> は生まれた輪の対象（IRI の # や / があっても往復する）', () => {
    const iris = ['https://example.org/a#K1', 'https://example.org/b/K2']
    const hash = routeToHash({ tab: 'home', newIris: iris })
    expect(parseHash(hash)).toEqual<Route>({ tab: 'home', newIris: iris })
    expect(roundTrip(hash)).toBe(hash)
  })

  it('deep と new は同居できる', () => {
    const hash = routeToHash({ tab: 'home', deep: true, newIris: ['x:y'] })
    expect(parseHash(hash)).toEqual<Route>({ tab: 'home', deep: true, newIris: ['x:y'] })
  })

  it('new だけを外した hash は deep を残す', () => {
    expect(routeToHash({ tab: 'home', deep: true })).toBe('#/home?deep=1')
    expect(routeToHash({ tab: 'home' })).toBe('#/home')
  })
})

describe('新しい綴り（UI 再設計 PR 1）— questions / data / browse', () => {
  it('#/questions は vocab タブ（routeToHash は #/questions に戻す）', () => {
    expect(parseHash('#/questions')).toEqual<Route>({ tab: 'vocab' })
    expect(routeToHash({ tab: 'vocab' })).toBe('#/questions')
  })

  it('#/questions?new=1 はホームの会話を「問いを書く」で開く（作る操作は作業場に寄せた）', () => {
    const write = { q: '', resume: '', extend: null }
    expect(parseHash('#/questions?new=1')).toEqual<Route>({ tab: 'home', write })
    expect(routeToHash({ tab: 'home', write })).toBe('#/home?write=1')
    expect(roundTrip('#/questions?new=1')).toBe('#/home?write=1')
  })

  it('#/questions?q=… ・ scope は会話を開かない（一覧のまま）', () => {
    expect(parseHash('#/questions?q=abc')).toEqual<Route>({ tab: 'vocab' })
    expect(parseHash('#/questions?scope=standard&dataset=x')).toEqual<Route>({ tab: 'vocab' })
  })

  it('旧 #/vocab・#/vocab?… も同じ結果（互換）', () => {
    expect(parseHash('#/vocab')).toEqual<Route>({ tab: 'vocab' })
    expect(parseHash('#/vocab?q=x')).toEqual<Route>({ tab: 'vocab' })
    expect(parseHash('#/vocab?new=1')).toEqual<Route>({ tab: 'home', write: { q: '', resume: '', extend: null } })
  })

  it('#/data は gallery', () => {
    expect(parseHash('#/data')).toEqual<Route>({ tab: 'gallery' })
  })

  it('#/data?tab=connections は crosswalk', () => {
    expect(parseHash('#/data?tab=connections')).toEqual<Route>({ tab: 'crosswalk' })
  })

  it('#/data?add=1 は gallery + add', () => {
    expect(parseHash('#/data?add=1')).toEqual<Route>({ tab: 'gallery', add: true })
  })

  it('#/browse は cards', () => {
    expect(parseHash('#/browse')).toEqual<Route>({ tab: 'cards' })
  })

  it('#/datasets は #/data に巻き込まれない（別の綴りのまま）', () => {
    expect(parseHash('#/datasets')).toEqual<Route>({ tab: 'gallery' })
    expect(routeToHash({ tab: 'gallery' })).toBe('#/datasets')
  })
})

describe('知りたいこと（テーマ・用語のページ）— UI 再設計 PR 4', () => {
  it('#/questions?new=1&q=<題> は会話に題を運ぶ（質問するから）', () => {
    const hash = '#/questions?new=1&q=' + encodeURIComponent('ZT が最大の試料は？ A/B')
    expect(parseHash(hash)).toEqual<Route>({ tab: 'home', write: { q: 'ZT が最大の試料は？ A/B', resume: '', extend: null } })
    expect(legacyHashRewrite(hash)).toBeNull()
  })

  it('#/questions?new=1&resume=<id> は会話に続きの下書きを開く・問いを足すテーマは extend で運ぶ', () => {
    expect(parseHash('#/questions?new=1&resume=d1')).toEqual<Route>({ tab: 'home', write: { q: '', resume: 'd1', extend: null } })
    const w = { q: '', resume: '', extend: { id: 'first', label: 'はじめの問い' } }
    const hash = routeToHash({ tab: 'home', write: w })
    expect(hash).toBe('#/home?write=1&extend=first&label=%E3%81%AF%E3%81%98%E3%82%81%E3%81%AE%E5%95%8F%E3%81%84')
    expect(parseHash(hash)).toEqual<Route>({ tab: 'home', write: w })
  })

  it('#/questions/term/<slug> は用語のページ・往復する（slug は符号化される）', () => {
    expect(parseHash('#/questions/term/sample_composition')).toEqual<Route>({
      tab: 'vocab',
      termSlug: 'sample_composition',
    })
    expect(routeToHash({ tab: 'vocab', termSlug: 'sample_composition' })).toBe('#/questions/term/sample_composition')
    expect(roundTrip('#/questions/term/sample_composition')).toBe('#/questions/term/sample_composition')
    expect(parseHash('#/questions/term/a%20b')).toEqual<Route>({ tab: 'vocab', termSlug: 'a b' })
    expect(routeToHash({ tab: 'vocab', termSlug: 'a b' })).toBe('#/questions/term/a%20b')
  })

  it('#/questions/<theme> はそのテーマのカードへ（term は予約語）', () => {
    expect(parseHash('#/questions/first')).toEqual<Route>({ tab: 'vocab', themeId: 'first' })
    expect(routeToHash({ tab: 'vocab', themeId: 'first' })).toBe('#/questions/first')
    expect(roundTrip('#/questions/first')).toBe('#/questions/first')
    expect(parseHash('#/questions/term')).toEqual<Route>({ tab: 'vocab' })
    expect(parseHash('#/questions/first?x=1')).toEqual<Route>({ tab: 'vocab', themeId: 'first' })
  })

  it('旧 #/vocab/… も同じ結果で、#/questions/… に書き換わる（互換）', () => {
    expect(parseHash('#/vocab/term/x')).toEqual<Route>({ tab: 'vocab', termSlug: 'x' })
    expect(legacyHashRewrite('#/vocab/term/x')).toBe('#/questions/term/x')
    expect(legacyHashRewrite('#/questions/term/x')).toBeNull()
    expect(legacyHashRewrite('#/questions/first')).toBeNull()
  })
})

describe('legacyHashRewrite — 旧 hash の書き換え先', () => {
  it('#/vocab → #/questions（クエリは温存）', () => {
    expect(legacyHashRewrite('#/vocab')).toBe('#/questions')
    expect(legacyHashRewrite('#/vocab?q=x')).toBe('#/questions?q=x')
    expect(legacyHashRewrite('#/vocab?scope=standard&dataset=d1')).toBe(
      '#/questions?scope=standard&dataset=d1',
    )
  })

  it('#/data / #/browse は routeToHash(parseHash(…)) の綴りへ', () => {
    expect(legacyHashRewrite('#/data')).toBe('#/datasets')
    expect(legacyHashRewrite('#/data?tab=connections')).toBe('#/crosswalk')
    expect(legacyHashRewrite('#/data?add=1')).toBe('#/datasets/add')
    expect(legacyHashRewrite('#/browse')).toBe('#/cards')
  })

  it('新しい綴り・無関係な hash は触らない（#/datasets は #/data ではない）', () => {
    expect(legacyHashRewrite('#/questions')).toBeNull()
    expect(legacyHashRewrite('#/questions?new=1')).toBeNull()
    expect(legacyHashRewrite('#/datasets')).toBeNull()
    expect(legacyHashRewrite('#/datasets/add')).toBeNull()
    expect(legacyHashRewrite('#/cards')).toBeNull()
    expect(legacyHashRewrite('#/home')).toBeNull()
  })
})

describe('決めたこと（#/decisions）と データの「記録」（#/data?tab=activity）', () => {
  it('#/decisions は「決めたこと」・すべて', () => {
    expect(parseHash('#/decisions')).toEqual<Route>({ tab: 'decisions' })
    expect(routeToHash({ tab: 'decisions' })).toBe('#/decisions')
  })

  it('#/decisions?filter=perspective は「つながり同士」の絞り込み（往復する）', () => {
    const hash = '#/decisions?filter=perspective'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'decisions', decisionsFilter: 'perspective' })
    expect(roundTrip(hash)).toBe(hash)
    expect(parseHash('#/decisions?filter=bogus')).toEqual<Route>({ tab: 'decisions' })
  })

  it('#/decisions?about=<IRI> は、その種類・用語に掛かるものだけ（往復する）', () => {
    const hash = '#/decisions?about=http%3A%2F%2Fexample.org%2Fns%23Sample'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'decisions', decisionsAbout: 'http://example.org/ns#Sample' })
    expect(roundTrip(hash)).toBe(hash)
    const both = '#/decisions?filter=same&about=urn%3Ak'
    expect(parseHash(both)).toEqual<Route>({ tab: 'decisions', decisionsFilter: 'same', decisionsAbout: 'urn:k' })
    expect(roundTrip(both)).toBe(both)
  })

  it('#/data?tab=activity は データの「記録」タブ（往復する・書き換えない）', () => {
    const hash = '#/data?tab=activity'
    expect(parseHash(hash)).toEqual<Route>({ tab: 'gallery', activity: true })
    expect(roundTrip(hash)).toBe(hash)
    expect(legacyHashRewrite(hash)).toBeNull()
  })

  it('旧 #/jobs・#/decisions?tab=activity は #/data?tab=activity に置き換わる', () => {
    expect(parseHash('#/jobs')).toEqual<Route>({ tab: 'gallery', activity: true })
    expect(legacyHashRewrite('#/jobs')).toBe('#/data?tab=activity')
    expect(parseHash('#/decisions?tab=activity')).toEqual<Route>({ tab: 'gallery', activity: true })
    expect(legacyHashRewrite('#/decisions?tab=activity')).toBe('#/data?tab=activity')
    expect(legacyHashRewrite('#/decisions')).toBeNull()
    expect(legacyHashRewrite('#/decisions?filter=perspective')).toBeNull()
  })
})
