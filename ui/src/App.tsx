import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './App.css'
import { prefillAskQuestion } from './askPrefill'
import { AskView } from './AskView'
import { fetchProposal } from './api'
import { CardsGallery } from './cards/CardsGallery'
import { CardsView } from './cards/CardsView'
import './cards/embed.css'
import { SubjectRail } from './cards/SubjectRail'
import { ConsultDrawer } from './consult/ConsultDrawer'
import { useWorkspaceChatShown } from './consult/workspaceChatActive'
import { CrosswalkView } from './CrosswalkView'
import { isMockMode } from './demoApi'
import { BackendDownBanner } from './desktop/BackendDownBanner'
import { UpdateBanner } from './desktop/UpdateBanner'
import { type DetailFocus, type DetailTab, GalleryView } from './GalleryView'
import { LanguageToggle } from './i18n/LanguageToggle'
import { BrandMark, ChevronIcon, GearIcon } from './icons'
import { DecisionsView } from './DecisionsView'
import { type DecisionFilter, decisionsQuery, parseDecisionsQuery } from './decide/decisionsModel'
import { JobsView } from './JobsView'
import { WorkbenchTier } from './kantan/WorkbenchTier'
import { NAV_ITEMS } from './navGroups'
import { addSubjectAndPersist } from './cards/subjectStore'
import { formatSetTitle } from './cards/setTitle'
import type { SetResolveResult } from './cards/cardsApi'
import { OntologyMapView } from './OntologyMapView'
import { SkyView } from './sky/SkyView'
import { homeHash, parseHomeQuery } from './sky/skyRoute'
import { useLlmSettings } from './settings/context'
import { SparqlView } from './SparqlView'
import { TermPage } from './themes/TermPage'
import { ThemesView } from './themes/ThemesView'
import type { RedesignTarget } from './WorkbenchView'

// sigma（WebGL）は全体グラフを開くときだけ読む（初期の読み込みを重くしない・テストの node でも落ちない）。
const NetworkView = lazy(() => import('./NetworkView').then((m) => ({ default: m.NetworkView })))

type Tab =
  | 'home'
  | 'workbench'
  | 'ask'
  | 'gallery'
  | 'vocab'
  | 'crosswalk'
  | 'map'
  /** 「決めたこと」（人が決めた関係の記録）。左ナビには出さない——用語のページと
   *  星の横からだけ辿る。 */
  | 'decisions'
  | 'sparql'
  /** 裏タブ（NAV_ITEMS には出さない）。カード描画器 3 つの見本ページ・スクショ用。 */
  | 'cardsdemo'
  /** 「1 件／絞り込み × カード」の使う画面（object-cards-ui.md）。既定ルート
   *  （契約メモ contract_pr_e.md §3）— 左ナビ「見る」から入る。 */
  | 'cards'

// ---- hash ルーティング -------------------------------------------------------
// リロードで常にホームへ戻る／ディープリンク不可だった問題への最小のルータ。
//   #/home … #/sparql        画面タブ
//   #/datasets/<id>          データセット詳細（一覧⇄詳細の往復でも選択が消えない）
//   #/datasets/<id>/<tab>    詳細内タブ（structure/tools/files/connect/design）
//   #/ask/<threadId>         質問する: チャットのスレッド（#/ask だけなら新しいチャット）
// hash が唯一の真実源: 画面遷移は navigate() が hash を書き、hashchange で state
// に反映する（ブラウザの戻る/進むもそのまま効く）。

export interface Route {
  tab: Tab
  /** `#/home?deep=1`（`tab: 'home'`）— 星図の同じ場所に「中身まで見る」（値の網）を出す。
   *  旧 `#/network` はここへ置き換える。 */
  deep?: boolean
  /** `#/home?new=<iri>,<iri>`（`tab: 'home'`）— 公開直後の星図で、その種類・用語を 1 度だけ灯す。 */
  newIris?: string[]
  datasetId?: string
  detailTab?: DetailTab
  /** `#/crosswalk/new` — the guided "make a connection" flow, deep-linkable so other
   *  screens can send someone straight into it and back/forward still work. */
  create?: boolean
  /** `#/ask/<id>` — the open chat thread (reload / back / forward keep it). */
  threadId?: string
  /** `#/cards/place` — 「データを置く」（object-cards-ui.md 契約メモ §6.2）。
   *  旧 URL 互換のためだけに残す — 開いたら `#/datasets/add` に置き換える
   *  （契約メモ contract_pr_f8.md §1.2）。新しい入口は下の `add`。 */
  place?: boolean
  /** `#/datasets/add`（`tab: 'gallery'`）— データが入る唯一の入口「作る ›
   *  データセット › データを追加」。中身はかんたんウィザードの S1（ファイルを
   *  置く）を直接出す `WorkbenchTier`（契約メモ contract_pr_f10.md §1.2 —
   *  旧 PlaceView の中間画面は撤去。「同じ形なら設計なしで追加」の近道は S1 の
   *  帯が担う）。`tab: 'cards'` のときは `#/cards/add`（オブジェクトを追加・
   *  契約メモ contract_pr_f9.md §1-3）を指す — 同じフィールドを tab ごとに
   *  読み替える（`datasetId`/`subjectKey` 等と同じ流儀）。 */
  add?: boolean
  /** `#/questions?new=1`（`tab: 'vocab'`）— 上部バーの「？ 知りたいことを書く」から
   *  入ったとき、「問いから始める」の入力欄に自動でフォーカスする。 */
  newQuestion?: boolean
  /** `#/questions/term/<slug>`（`tab: 'vocab'`）— 用語のページ。 */
  termSlug?: string
  /** `#/questions/<theme>`（`tab: 'vocab'`）— そのテーマのカードを見せる。 */
  themeId?: string
  /** `#/cards/add?kind=<class_iri>` — 種類のページの「＋ 追加」から、その種類を
   *  開いた状態で追加画面に入る（PR F9）。 */
  addClassIri?: string
  /** `#/cards/k/<encoded class_iri>` — 種類のページ（契約メモ
   *  contract_pr_f9.md §1-4）。 */
  classPageIri?: string
  /** `#/cards/place?dataset=<id>` | `#/datasets/add?dataset=<id>` — かんたん
   *  ウィザードから「ページに戻る」で入ってきたときの、既に棚にあるデータセット
   *  （契約メモ §6.3 の `KantanWizard.tsx` の `returnTo + datasetId`）。`place`
   *  または `add` と組で使う。 */
  placeDatasetId?: string
  /** `#/cards/i/<encoded iri>` | `#/cards/s/<set_id>` — 契約メモ §1 の subject_key
   *  文字列表現をそのまま持つ（`i:<iri>` | `s:<set_id>`）。URL 変換は
   *  parseHash/routeToHash が担う。 */
  subjectKey?: string
  /** `.../c/<card_id>` — カード詳細。subjectKey と組み合わせて使う。 */
  cardId?: string
  /** `#/cards/i/<iri>?solo=1` — 束ねずに単独のページを見る逃げ道（契約メモ
   *  §1.3）。`?` は `i/<iri>` の直後にも `c/<cardId>` の後ろにも付けられる。 */
  solo?: boolean
  /** `#/cards/d/<dataset_id>` — データセットのページ（契約メモ contract_pr_f2.md
   *  §2.2・§2.4）。「作る」と「使う」の合流点。 */
  datasetPageId?: string
  /** `#/cards/d/<dataset_id>/define` | `#/cards/d/<dataset_id>/details[/<detailTab>]`
   *  — 「意味を定義する」「詳しい情報」を見るの枠（SubjectRail・topbar）の中で
   *  開く子ルート（契約メモ contract_pr_f5.md §1.1）。無ければ従来どおり
   *  データセットのページだけ。 */
  datasetSub?: 'define' | 'details'
  /** `#/cards/s/new?dataset=<id>&class=<iri>` — 「条件で集める」の新規作成
   *  （契約メモ §2.4）。`setDatasetId`/`setClassIri` と組で使う。 */
  setNew?: boolean
  setDatasetId?: string
  setClassIri?: string
  /** `#/workbench?returnTo=<hash>` — かんたんウィザードの完了・中止の戻り先
   *  （契約メモ §2.6: 「returnTo をルートで持つ」）。データセットのページから
   *  「定義を直す」で入ったときだけ立てる — 置く画面から入る既存の経路
   *  （`goBuildShelf`/`autoInspect`）はウィザード内部の別の returnTo をそのまま
   *  使うので触らない。 */
  returnTo?: string
  /** `#/data?tab=activity`（`tab: 'gallery'`）— データの 3 つ目のタブ「記録」（取り込みの履歴）。
   *  旧 `#/jobs`・`#/decisions?tab=activity` はここへ置き換える。 */
  activity?: boolean
  /** `#/decisions?filter=perspective` — 「決めたこと」の絞り込み（無ければ「すべて」）。 */
  decisionsFilter?: DecisionFilter
  /** `#/decisions?about=<IRI>` — その種類・用語に掛かる決めたことだけ。 */
  decisionsAbout?: string
}

const TABS: readonly Tab[] = [
  'home',
  'workbench',
  'ask',
  'gallery',
  'vocab',
  'crosswalk',
  'map',
  'decisions',
  'sparql',
  'cardsdemo',
  'cards',
]
const DETAIL_TABS: readonly DetailTab[] = ['structure', 'tools', 'files', 'connect', 'design']

// parseHash/routeToHash は App.tsx から export して routes.test.ts が単体テストする
// （契約メモ §6.4）。react-refresh の「コンポーネントだけ export しろ」規約とは
// ぶつかるが、テスト容易性のため意図して許容する。
// eslint-disable-next-line react-refresh/only-export-components
export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  // 既定ルート（`''`・`#/`）は「見る」（cards）— ユーザー指示。既存の各画面
  // （`#/home` 等）を明示した場合はそのまま尊重し、これは hash が空のときだけ効く。
  if (parts.length === 0) return { tab: 'cards' }
  // 新しい綴り（UI 再設計 PR 1）。画面名＝綴り: 知りたいこと=#/questions、
  // データ=#/data（つながりは ?tab=connections）、見る=#/browse。内部の Tab id は
  // そのまま（vocab / gallery / crosswalk / cards）。旧 `#/vocab…` も同じ結果（互換）。
  if (parts[0] === 'questions' || parts[0] === 'vocab') {
    // `#/questions/term/<slug>` — 用語のページ。`#/questions/<theme>` — テーマのカードへ。
    if (parts[1] === 'term' && parts[2]) {
      return { tab: 'vocab', termSlug: decodeURIComponent(parts[2].split('?')[0]) }
    }
    if (parts[1] && parts[1] !== 'term') {
      return { tab: 'vocab', themeId: decodeURIComponent(parts[1].split('?')[0]) }
    }
    return { tab: 'vocab' }
  }
  if (parts[0]?.startsWith('questions?') || parts[0]?.startsWith('vocab?')) {
    const query = parts[0].slice(parts[0].indexOf('?') + 1)
    return new URLSearchParams(query).get('new') === '1'
      ? { tab: 'vocab', newQuestion: true }
      : { tab: 'vocab' }
  }
  if (parts[0] === 'data') return { tab: 'gallery' }
  if (parts[0]?.startsWith('data?')) {
    const params = new URLSearchParams(parts[0].slice('data?'.length))
    if (params.get('tab') === 'connections') return { tab: 'crosswalk' }
    if (params.get('tab') === 'activity') return { tab: 'gallery', activity: true }
    if (params.get('add') === '1') return { tab: 'gallery', add: true }
    return { tab: 'gallery' }
  }
  if (parts[0] === 'browse' || parts[0]?.startsWith('browse?')) return { tab: 'cards' }
  // 星図（ホーム）。`#/home?deep=1` = 中身まで見る・`#/home?new=…` = 生まれた輪。
  // 旧 `#/network`（全体グラフ）はホームの「中身まで見る」に統合した。
  if (parts[0] === 'network' || parts[0]?.startsWith('network?')) return { tab: 'home', deep: true }
  if (parts[0]?.startsWith('home?')) {
    const q = parseHomeQuery(parts[0].slice('home?'.length))
    const r: Route = { tab: 'home' }
    if (q.deep) r.deep = true
    if (q.newIris.length > 0) r.newIris = q.newIris
    return r
  }
  if (parts[0] === 'datasets' && parts[1]) {
    // `#/datasets/add[?dataset=<id>]` — データが入る唯一の入口（契約メモ
    // contract_pr_f8.md §1.1）。`add` は予約語として `<id>` の一般形より先に見る
    // （`add` という id は実在しないが、`cards/place`・`cards/s/new` と同じ流儀で
    // 明示的に区別する）。
    if (parts[1] === 'add') return { tab: 'gallery', add: true }
    if (parts[1].startsWith('add?')) {
      const query = parts[1].slice('add?'.length)
      const placeDatasetId = new URLSearchParams(query).get('dataset')
      return placeDatasetId
        ? { tab: 'gallery', add: true, placeDatasetId }
        : { tab: 'gallery', add: true }
    }
    const detailTab = DETAIL_TABS.includes(parts[2] as DetailTab)
      ? (parts[2] as DetailTab)
      : undefined
    return { tab: 'gallery', datasetId: decodeURIComponent(parts[1]), detailTab }
  }
  // 旧 URL 互換: #/datasets はタブ名 gallery の別名
  if (parts[0] === 'datasets') return { tab: 'gallery' }
  if (parts[0] === 'crosswalk' && parts[1] === 'new') return { tab: 'crosswalk', create: true }
  if (parts[0] === 'ask' && parts[1]) return { tab: 'ask', threadId: decodeURIComponent(parts[1]) }
  // `#/workbench?returnTo=<encoded hash>` — かんたんウィザードの完了・中止の
  // 戻り先（契約メモ §2.6）。クエリ無しの `#/workbench` は下の TABS.includes に
  // そのまま落ちる。
  if (parts[0]?.startsWith('workbench?')) {
    const query = parts[0].slice('workbench?'.length)
    const returnTo = new URLSearchParams(query).get('returnTo')
    return returnTo ? { tab: 'workbench', returnTo: decodeURIComponent(returnTo) } : { tab: 'workbench' }
  }
  if (parts[0] === 'cards') {
    // `#/cards/add` — オブジェクトを追加する唯一の入口（契約メモ
    // contract_pr_f9.md §1-3）。`add`/`k` は予約語として `<class_iri>` の一般形
    // より先に見る（`add`・`k` という主語は実在しないが、`place`・`d`・`s/new`
    // と同じ流儀で明示的に区別する）。
    if (parts[1] === 'add') return { tab: 'cards', add: true }
    if (parts[1]?.startsWith('add?')) {
      const kind = new URLSearchParams(parts[1].slice('add?'.length)).get('kind')
      return kind ? { tab: 'cards', add: true, addClassIri: kind } : { tab: 'cards', add: true }
    }
    // `#/cards/k/<encoded class_iri>` — 種類のページ（契約メモ §1-4）。
    if (parts[1] === 'k' && parts[2]) {
      return { tab: 'cards', classPageIri: decodeURIComponent(parts[2]) }
    }
    if (parts[1] === 'place') return { tab: 'cards', place: true }
    // `#/cards/place?dataset=<id>` — parts は '/' でしか割っていないので
    // クエリ文字列は parts[1] の末尾にくっついたまま届く（`place?dataset=…`）。
    if (parts[1]?.startsWith('place?')) {
      const query = parts[1].slice('place?'.length)
      const datasetId = new URLSearchParams(query).get('dataset')
      return datasetId ? { tab: 'cards', place: true, placeDatasetId: datasetId } : { tab: 'cards', place: true }
    }
    // `#/cards/d/<id>` — データセットのページ（契約メモ §2.2）。
    // `.../define` | `.../details[/<detailTab>]` — 見るの枠の中で開く子ルート
    // （契約メモ contract_pr_f5.md §1.1）。
    if (parts[1] === 'd' && parts[2]) {
      const datasetPageId = decodeURIComponent(parts[2])
      if (parts[3] === 'define') return { tab: 'cards', datasetPageId, datasetSub: 'define' }
      if (parts[3] === 'details') {
        const detailTab = DETAIL_TABS.includes(parts[4] as DetailTab) ? (parts[4] as DetailTab) : undefined
        return { tab: 'cards', datasetPageId, datasetSub: 'details', detailTab }
      }
      return { tab: 'cards', datasetPageId }
    }
    // `#/cards/s/new?dataset=<id>&class=<iri>` — 「条件で集める」の新規作成
    // （契約メモ §2.4）。`s/<set_id>` の一般形より先に見る（`new` という set_id
    // は実在しないが、`place` と同じ流儀で明示的に区別する）。
    if (parts[1] === 's' && (parts[2] === 'new' || parts[2]?.startsWith('new?'))) {
      const qIdx = parts[2].indexOf('?')
      const query = qIdx === -1 ? '' : parts[2].slice(qIdx + 1)
      const params = new URLSearchParams(query)
      const setDatasetId = params.get('dataset') ?? undefined
      const setClassIri = params.get('class') ?? undefined
      return { tab: 'cards', setNew: true, setDatasetId, setClassIri }
    }
    if ((parts[1] === 'i' || parts[1] === 's') && parts[2]) {
      // `?solo=1` — 束ねずに単独のページを見る逃げ道（契約メモ §1.3）。
      // encodeURIComponent された IRI 自体には生の `?` は現れないので、
      // 見つかった `?` はこのクエリのもの（`c/<cardId>` の後ろに付くこともある）。
      const iriRaw = parts[2]
      const iriQIdx = iriRaw.indexOf('?')
      const iriPart = iriQIdx === -1 ? iriRaw : iriRaw.slice(0, iriQIdx)
      const subjectKey = `${parts[1]}:${decodeURIComponent(iriPart)}`
      let queryStr = iriQIdx === -1 ? undefined : iriRaw.slice(iriQIdx + 1)
      let cardId: string | undefined
      if (parts[3] === 'c' && parts[4]) {
        const cardRaw = parts[4]
        const cardQIdx = cardRaw.indexOf('?')
        cardId = decodeURIComponent(cardQIdx === -1 ? cardRaw : cardRaw.slice(0, cardQIdx))
        if (cardQIdx !== -1) queryStr = cardRaw.slice(cardQIdx + 1)
      }
      const solo = queryStr ? new URLSearchParams(queryStr).get('solo') === '1' || undefined : undefined
      return { tab: 'cards', subjectKey, cardId, solo }
    }
    return { tab: 'cards' }
  }
  // `#/questions?q=<題>` ・ `#/questions?scope=…&dataset=<id>` — 「知りたいこと」画面への
  // 持ち込み（上で解く）。クエリは画面（ThemesView）が自分で hash から読む。
  // 旧 `#/jobs`（アクティビティ）は「データ」の 3 つ目のタブ「記録」へ。
  if (parts[0] === 'jobs') return { tab: 'gallery', activity: true }
  if (parts[0] === 'decisions' || parts[0]?.startsWith('decisions?')) {
    const query = parts[0].includes('?') ? parts[0].slice(parts[0].indexOf('?') + 1) : ''
    // 旧 `#/decisions?tab=activity` も「記録」へ。
    if (new URLSearchParams(query).get('tab') === 'activity') return { tab: 'gallery', activity: true }
    const q = parseDecisionsQuery(query)
    return {
      tab: 'decisions',
      ...(q.filter !== 'all' ? { decisionsFilter: q.filter } : {}),
      ...(q.about ? { decisionsAbout: q.about } : {}),
    }
  }
  if (TABS.includes(parts[0] as Tab)) return { tab: parts[0] as Tab }
  return { tab: 'home' }
}

// eslint-disable-next-line react-refresh/only-export-components
export function routeToHash(r: Route): string {
  if (r.tab === 'home') return homeHash({ deep: r.deep, newIris: r.newIris })
  if (r.tab === 'gallery' && r.add) {
    return r.placeDatasetId
      ? `#/datasets/add?dataset=${encodeURIComponent(r.placeDatasetId)}`
      : '#/datasets/add'
  }
  if (r.tab === 'gallery' && r.datasetId) {
    const base = `#/datasets/${encodeURIComponent(r.datasetId)}`
    return r.detailTab && r.detailTab !== 'structure' ? `${base}/${r.detailTab}` : base
  }
  if (r.tab === 'gallery' && r.activity) return '#/data?tab=activity'
  if (r.tab === 'gallery') return '#/datasets'
  if (r.tab === 'vocab') {
    if (r.termSlug) return `#/questions/term/${encodeURIComponent(r.termSlug)}`
    if (r.themeId) return `#/questions/${encodeURIComponent(r.themeId)}`
    return r.newQuestion ? '#/questions?new=1' : '#/questions'
  }
  if (r.tab === 'decisions') {
    return `#/decisions${decisionsQuery(r.decisionsFilter ?? 'all', r.decisionsAbout)}`
  }
  if (r.tab === 'crosswalk' && r.create) return '#/crosswalk/new'
  if (r.tab === 'ask' && r.threadId) return `#/ask/${encodeURIComponent(r.threadId)}`
  if (r.tab === 'workbench' && r.returnTo) return `#/workbench?returnTo=${encodeURIComponent(r.returnTo)}`
  if (r.tab === 'cards') {
    if (r.add) return r.addClassIri ? `#/cards/add?kind=${encodeURIComponent(r.addClassIri)}` : '#/cards/add'
    if (r.classPageIri) return `#/cards/k/${encodeURIComponent(r.classPageIri)}`
    if (r.place) {
      return r.placeDatasetId
        ? `#/cards/place?dataset=${encodeURIComponent(r.placeDatasetId)}`
        : '#/cards/place'
    }
    if (r.datasetPageId) {
      const base = `#/cards/d/${encodeURIComponent(r.datasetPageId)}`
      if (r.datasetSub === 'define') return `${base}/define`
      if (r.datasetSub === 'details') {
        return r.detailTab && r.detailTab !== 'structure' ? `${base}/details/${r.detailTab}` : `${base}/details`
      }
      return base
    }
    if (r.setNew) {
      const params = new URLSearchParams()
      if (r.setDatasetId) params.set('dataset', r.setDatasetId)
      if (r.setClassIri) params.set('class', r.setClassIri)
      const qs = params.toString()
      return qs ? `#/cards/s/new?${qs}` : '#/cards/s/new'
    }
    if (r.subjectKey) {
      const kind = r.subjectKey.startsWith('s:') ? 's' : 'i'
      const base = `#/cards/${kind}/${encodeURIComponent(r.subjectKey.slice(2))}`
      const withCard = r.cardId ? `${base}/c/${encodeURIComponent(r.cardId)}` : base
      return r.solo ? `${withCard}?solo=1` : withCard
    }
    return '#/cards'
  }
  return `#/${r.tab}`
}

/**
 * 旧 hash を新しい綴りに直した文字列を返す（直す必要が無ければ null）。
 * `#/vocab…` → `#/questions…`（クエリは温存）、`#/jobs`・`#/decisions?tab=activity` → `#/data?tab=activity`、`#/data…` / `#/browse…` /
 * `#/network` → `routeToHash(parseHash(hash))`（`#/network` は `#/home?deep=1`）。`#/datasets…` は別物なので触らない。
 */
// eslint-disable-next-line react-refresh/only-export-components
export function legacyHashRewrite(hash: string): string | null {
  if (/^#\/vocab(?=[?/]|$)/.test(hash)) return hash.replace(/^#\/vocab/, '#/questions')
  if (/^#\/jobs(?=[?/]|$)/.test(hash)) return '#/data?tab=activity'
  if (/^#\/decisions\?(?:.*&)?tab=activity(?:&|$)/.test(hash)) return '#/data?tab=activity'
  if (/^#\/(data|browse|network)(?=[?/]|$)/.test(hash)) {
    const next = routeToHash(parseHash(hash))
    return next === hash ? null : next
  }
  return null
}

/** 起動時と hashchange で呼ぶ。旧 hash なら `replaceState` で置き換える（戻る履歴を汚さない）。 */
function applyLegacyHashRewrite(): void {
  const next = legacyHashRewrite(window.location.hash)
  if (next) window.history.replaceState(null, '', next)
}

// v2 IA (design_handoff_asterism_ux/v2): a flat, object-axis nav — the sidebar
// lists only "places to look" (nouns). Creation is inline (the Home action and
// the Datasets add-tile), so there is NO global create button and "データを追加"
// (workbench) is reachable but not a nav entry. Crosswalk (つながり) and shared
// terms (ことば) are promoted to first-class places; the ontology map (全体像)
// is reached from つながり. SPARQL sits apart at the foot as a developer escape
// hatch. Labels are resolved via i18n (common.nav.*).
//
// 左ナビは 1 本・常に同じ（契約メモ contract_pr_f7.md §1）: 見出し「作る」
// 「使う」＋項目は navGroups.ts の NAV_GROUPS（純データ）から。かつては
// `tab === 'cards'` のあいだこの一覧ごと SubjectRail に差し替えていたが、
// それだと旧ナビが消えて戻り道が「見る」1 行しか無かった（ユーザー指摘・O52）。

/** 左ナビの「たたむ」状態を保つキー（契約メモ §1-2）。既定は開いている。 */
const NAV_COLLAPSE_STORAGE = 'asterism.nav.collapsed'
/** 「本文の最小幅を割る画面幅」（契約メモ §1-6）。 */
const NAV_AUTO_COLLAPSE_QUERY = '(max-width: 1099px)'

function loadNavCollapsed(): boolean | null {
  try {
    const raw = localStorage.getItem(NAV_COLLAPSE_STORAGE)
    return raw === null ? null : raw === '1'
  } catch {
    return null
  }
}

function saveNavCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem(NAV_COLLAPSE_STORAGE, collapsed ? '1' : '0')
  } catch {
    /* private mode 等 — 今回のセッションだけ効く */
  }
}

/**
 * The desktop shell adds `?port_fallback=1` to the window URL when its usual
 * entrance (port 8765) was taken by ANOTHER program, so this run is on a
 * different port and its settings will not be the ones the user set up before.
 * The shell already says so before launching; this is the reminder while working.
 * Nothing to decide here — so this is a note, not a dialog. (It never appears
 * when the other side is Asterism itself: the shell then reuses that window.)
 */
function PortFallbackBanner() {
  const { t } = useTranslation()
  const [shown, setShown] = useState(() => {
    try {
      return new URLSearchParams(window.location.search).get('port_fallback') === '1'
    } catch {
      return false
    }
  })
  if (!shown) return null
  return (
    <div className="update-banner" role="status" aria-live="polite">
      <span className="update-banner-text">{t('portFallback.text')}</span>
      <span className="update-banner-actions">
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setShown(false)}>
          {t('portFallback.dismiss')}
        </button>
      </span>
    </div>
  )
}

/**
 * Read (and immediately clear) the note main.tsx leaves before it reloads a tab
 * whose chunks a deploy replaced. Done once at module load, not inside the
 * component: StrictMode renders twice in dev, and a flag consumed in a state
 * initializer would be gone by the render React keeps.
 */
const STALE_CHUNK_RELOADED = (() => {
  try {
    if (sessionStorage.getItem('asterism.staleChunkNotice')) {
      sessionStorage.removeItem('asterism.staleChunkNotice')
      return true
    }
  } catch {
    /* no sessionStorage — nothing to announce */
  }
  return false
})()

/**
 * Why the screen just reset. A deploy swaps the hashed chunks under a tab that
 * is still running the pre-deploy shell, so main.tsx reloads once to pick up the
 * new one — which mid-wizard looks like the work disappearing. Say it happened,
 * once, then get out of the way. If a chunk still fails to load AFTER that
 * reload, the deploy really is broken: the same banner then says so and offers
 * the reload, instead of leaving buttons that quietly do nothing.
 */
function StaleChunkBanner() {
  const { t } = useTranslation()
  const [state, setState] = useState<'none' | 'reloaded' | 'failed'>(
    STALE_CHUNK_RELOADED ? 'reloaded' : 'none',
  )
  useEffect(() => {
    const onPreloadError = () => {
      // main.tsx's listener runs first (registered at module load). On the FIRST
      // failure it leaves the notice and reloads — a reload takes a moment,
      // during which this banner would sit there claiming the update failed. So
      // treat "a notice is pending" as "a reload is on its way" and stay quiet;
      // the note is consumed at the next load and becomes the 'reloaded' line.
      try {
        if (sessionStorage.getItem('asterism.staleChunkNotice')) return
      } catch {
        /* no sessionStorage — main.tsx never auto-reloads, so this IS the failure */
      }
      setState('failed')
    }
    window.addEventListener('vite:preloadError', onPreloadError)
    return () => window.removeEventListener('vite:preloadError', onPreloadError)
  }, [])
  if (state === 'none') return null
  const failed = state === 'failed'
  return (
    <div className="update-banner" role="status" aria-live="polite">
      <span className="update-banner-text">
        {failed ? t('staleChunk.failed') : t('staleChunk.reloaded')}
      </span>
      <span className="update-banner-actions">
        {failed ? (
          <button type="button" className="btn btn--sm" onClick={() => window.location.reload()}>
            {t('staleChunk.reload')}
          </button>
        ) : (
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setState('none')}>
            {t('staleChunk.dismiss')}
          </button>
        )}
      </span>
    </div>
  )
}

function App() {
  const { t, i18n } = useTranslation()
  // Keep the existing two-line nav aesthetic: the active language is primary and
  // the other language sits underneath as a muted gloss.
  const otherLng = i18n.language.startsWith('en') ? 'ja' : 'en'
  const glossT = i18n.getFixedT(otherLng, 'common')
  // hash が唯一の真実源（リロード/戻る/進む/ディープリンクが全て効く）
  const [route, setRoute] = useState<Route>(() => {
    applyLegacyHashRewrite()
    return parseHash(window.location.hash)
  })
  const tab = route.tab
  const workspaceChatShown = useWorkspaceChatShown()
  const showGlobalConsult = tab !== 'cards' && tab !== 'home' && tab !== 'vocab' && !workspaceChatShown
  const { openSettings } = useLlmSettings()
  const tSettings = i18n.getFixedT(i18n.language, 'settings')
  const glossSettingsT = i18n.getFixedT(otherLng, 'settings')

  useEffect(() => {
    const onHash = () => {
      applyLegacyHashRewrite()
      setRoute(parseHash(window.location.hash))
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // 左ナビの「たたむ」（契約メモ §1-2・§1-6）。localStorage に値があれば
  // それが唯一の真実源（ユーザーの明示操作が優先＝以後この画面幅監視は無視）。
  // 無ければ、狭い画面幅では最初から自動でたたんだ状態にする。
  const navCollapsedExplicit = useRef(loadNavCollapsed() !== null)
  const [navCollapsed, setNavCollapsed] = useState<boolean>(() => {
    const stored = loadNavCollapsed()
    if (stored !== null) return stored
    try {
      return window.matchMedia(NAV_AUTO_COLLAPSE_QUERY).matches
    } catch {
      return false
    }
  })

  useEffect(() => {
    if (navCollapsedExplicit.current) return
    let mq: MediaQueryList
    try {
      mq = window.matchMedia(NAV_AUTO_COLLAPSE_QUERY)
    } catch {
      return
    }
    // 手動でたたんだ／ひろげた後は、画面幅が変わっても（ヘッドレスの撮影や
    // ウィンドウのリサイズを含む）その選択を上書きしない。
    const onChange = () => {
      if (navCollapsedExplicit.current) return
      setNavCollapsed(mq.matches)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  function toggleNavCollapsed() {
    setNavCollapsed((cur) => {
      const next = !cur
      navCollapsedExplicit.current = true
      saveNavCollapsed(next)
      return next
    })
  }

  /** 画面遷移の唯一の入口。pushState/replaceState で hash を書き、state を同期する
   *  （push/replace は hashchange を発火しないため手で set。hashchange リスナは
   *  ブラウザの戻る/進む・手入力 URL 用）。replace=true は履歴を積まない
   *  （詳細内タブ切替など、戻るボタンで巻き戻したくない細かな状態変更用）。 */
  function navigate(r: Route, opts?: { replace?: boolean }) {
    const h = routeToHash(r)
    if (window.location.hash !== h) {
      if (opts?.replace) window.history.replaceState(null, '', h)
      else window.history.pushState(null, '', h)
    }
    setRoute(r)
  }

  // Keep the document title and <html lang> in sync with the chosen language.
  // Before paint (useLayoutEffect, not useEffect): index.html ships lang="ja",
  // so an English reader would otherwise have the first frame — what a screen
  // reader picks its voice from — announced as Japanese.
  useLayoutEffect(() => {
    document.title = t('docTitle')
    document.documentElement.lang = i18n.language.startsWith('en') ? 'en' : 'ja'
  }, [t, i18n.language])
  // Ask⇄Gallery link: a vocabulary class to focus/highlight in the Gallery when
  // the user jumps there from an Ask citation. null = no focus.
  const [galleryFocus, setGalleryFocus] = useState<string | null>(null)
  const [detailFocus, setDetailFocus] = useState<DetailFocus | null>(null)

  // Gallery→Workbench redesign link: the existing dataset whose stored design the
  // workbench should reopen for a revision. Cleared once the workbench consumes it.
  const [redesignTarget, setRedesignTarget] = useState<RedesignTarget | null>(null)

  // cards タブの topbar 見出し = 選んだ対象のラベル（契約メモ §3）。CardsView が
  // route の変化を見て上げてくる（props 経由・「小さな store」は使わない —
  // App はすでに全画面の状態を持つ器なので、ここに足すだけで十分）。
  const [cardsLabel, setCardsLabel] = useState<string | null>(null)

  // 「定義を直す」が proposal 無しで詳しい情報（設計タブ）へ倒したデータセット
  // （契約メモ contract_pr_f5.md §1.3）。読み取り専用の 1 行はそのデータセットの
  // details/design に着地したときだけ帯の下に出す。
  const [meaningReadonlyFor, setMeaningReadonlyFor] = useState<string | null>(null)

  // 全体像（map）の「戻る」を入ってきた画面へ返す（従来は常に crosswalk 固定で、
  // データセット詳細の「全体像を見る」から入ると戻り先で現在地を見失っていた）。
  const [mapReturn, setMapReturn] = useState<Route>({ tab: 'crosswalk' })

  // タブ・詳細切替時にスクロールを先頭へ（.app-main は全画面共有のスクロール
  // コンテナなので、深くスクロールした位置が次の画面に持ち越されていた）。
  const mainRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    mainRef.current?.scrollTo(0, 0)
  }, [route.tab, route.datasetId])

  // Jump from a grounded answer to the ontology class that backs it.
  function showVocab(className: string) {
    setGalleryFocus(className)
    navigate({ tab: 'gallery' })
  }

  // Open the workbench on an existing dataset's design (the catalog "見直す" action).
  // `returnTo` (契約メモ §2.6) は「データセットのページから入ったとき」だけ
  // 呼び出し側が立てる — 置く画面から入る既存の経路（GalleryView の「見直す」）は
  // 渡さないので、そちらの戻り先（カタログの当該データセット詳細）は変わらない。
  // WorkbenchTier が消費する redesignTarget の state だけを立てる（ナビゲーション
  // はしない）。旧ナビの「見直す」（redesignDataset・下）と、見るの枠の中で開く
  // 「定義を直す」（onDefine・下）の両方がこれを共有する（契約メモ
  // contract_pr_f5.md §1.4）。
  function setRedesignState(target: RedesignTarget) {
    setGalleryFocus(null)
    setRedesignTarget(target)
  }

  // Gallery→Workbench redesign link（旧ナビの「見直す」・置く画面から入る既存の
  // 経路）: state を立てたうえで、従来どおり workbench タブへ移る。
  function redesignDataset(target: RedesignTarget, returnTo?: string) {
    setRedesignState(target)
    navigate({ tab: 'workbench', returnTo })
  }

  // データセットのページの「定義を直す」「続きから」（契約メモ §2.2・§2.6・
  // contract_pr_f5.md §1.4）。proposal があれば見るの枠の中の子ルート
  // （`#/cards/d/<id>/define`）でウィザードを開く（旧ナビの workbench タブへは
  // 移らない）。無ければ黙って止まらず（K39）、同じ枠の中の詳しい情報（設計
  // タブ）で定義を見せる。
  async function onDefine(datasetId: string) {
    try {
      const p = await fetchProposal(datasetId)
      if (!p.has_proposal || !p.proposal_md.trim()) {
        // 設計の下書き（proposal）が無いデータセット（同梱の見本・exchange で
        // 受け取ったもの）はウィザードで直せない。
        setMeaningReadonlyFor(datasetId)
        navigate({ tab: 'cards', datasetPageId: datasetId, datasetSub: 'details', detailTab: 'design' })
        return
      }
      setRedesignState({
        // K4: 生の id を人向けの文言に出さない — フォールバックはトップバーに
        // 既に出ているデータセットのページの見出し（cardsLabel）。
        datasetId,
        datasetName: p.dataset_name || cardsLabel || datasetId,
        proposalMd: p.proposal_md,
      })
      navigate({ tab: 'cards', datasetPageId: datasetId, datasetSub: 'define' })
    } catch {
      // best-effort: 開けなくても致命的にしない（データセットのページに留まる）。
    }
  }

  // WorkbenchTier/KantanWizard の唯一の「戻る」出口（完了の grow 導線・見直しの
  // やめる/この単位でよい、両方がここを通る）。`route.returnTo` が立っていれば
  // そこへ（旧ナビ「見直す」からデータセットのページ経由で入ったとき／
  // `#/datasets/add` の「設定の手順へ」から入ったとき）。無くても見るの枠の中
  // （`#/cards/d/<id>/define`）で開いていれば、そのデータセットのページへ戻す
  // （契約メモ contract_pr_f5.md §1.1・注意書き）。それ以外は従来どおりカタログの
  // 当該データセット詳細へ（カタログから入った経路）。
  function onWorkbenchDone(id: string, tab?: DetailTab, focus?: DetailFocus) {
    if (route.returnTo) {
      const target = parseHash(route.returnTo)
      // 「データを追加」（`#/datasets/add`）の「設定の手順へ」はワークスペース
      // （`#/cards`）を returnTo に持つ — ウィザード完了は素のワークスペースでは
      // なく、追加が終わったそのデータセットのページに着地させる（契約メモ
      // contract_pr_f8.md §1.3: 「作る → 使う の一本道」）。
      if (target.tab === 'cards' && !target.datasetPageId && !target.subjectKey && !target.place) {
        navigate({ tab: 'cards', datasetPageId: id })
        return
      }
      navigate(target)
      return
    }
    if (route.tab === 'cards' && route.datasetPageId) {
      navigate({ tab: 'cards', datasetPageId: route.datasetPageId })
      return
    }
    openDataset(id, tab, focus)
  }

  // Manual nav clears any pending vocabulary focus.
  function navTo(id: Tab) {
    setGalleryFocus(null)
    navigate({ tab: id })
  }

  // 全体グラフの値・束 →「一覧で開く」。CardsView の束と同じ経路（私の一覧に足して
  // 絞り込みのページへ）。
  function openNetworkSet(result: SetResolveResult) {
    const tc = i18n.getFixedT(null, 'cards')
    addSubjectAndPersist({
      kind: 'set',
      id: result.set_id,
      label: formatSetTitle(result.title, tc),
      class_label: result.title.class_label,
      source: 'open',
      card_count: null,
      match: null,
      subject_key: `s:${result.set_id}`,
      spec: result.spec,
      created_at: new Date().toISOString(),
    })
    navigate({ tab: 'cards', subjectKey: `s:${result.set_id}` })
  }

  // データセット詳細への直行導線（ホームの最近行・保存完了リンクなどから）。
  function openDataset(id: string, detailTab?: DetailTab, focus?: DetailFocus) {
    setGalleryFocus(null)
    // WHERE inside the tab, not just which tab. かんたん S9 sends people to a
    // control that sits partway down a long page; without this they land at the
    // top and have to hunt for the same button a second time. Kept in state
    // rather than the hash: it is a one-shot intent, not part of the address.
    setDetailFocus(focus ?? null)
    navigate({ tab: 'gallery', datasetId: id, detailTab })
  }

  // Ask 画面へ質問文を持って直行（かんたん S9 の試し質問チップ）。入力欄に入る
  // だけで自動送信はしない（Ask はキー必須+LLM 課金 — 送るのは人間）。
  function openAsk(question: string) {
    setGalleryFocus(null)
    prefillAskQuestion(question)
    navigate({ tab: 'ask' })
  }

  return (
    // .app-frame: 縦積み＝上に更新のお知らせ（デスクトップ版で更新があるときだけ・
    // サイドバーも含めた全幅）、下に従来の 2 列シェル。
    <div className="app-frame">
      <StaleChunkBanner />
      <PortFallbackBanner />
      <UpdateBanner />
      <BackendDownBanner />
      <div className="app-shell">
        <aside className={`sidebar${navCollapsed ? ' sidebar--collapsed' : ''}`}>
          <div className="brand">
            <span className="brand-mark">
              <BrandMark />
            </span>
            <span className="brand-text">
              <span className="brand-name">{t('brand.name')}</span>
              <span className="brand-tag">{t('brand.tag')}</span>
            </span>
          </div>

          {/* 左ナビは 1 本・5 つ・常に同じ（navGroups.ts の NAV_ITEMS）。
              tab === 'cards' でも差し替えない——「見る」がここへの戻り道を兼ねる。
              つながり（crosswalk）は「データ」の子なので、そのときは「データ」を active に。 */}
          <nav className="side-nav" id="app-side-nav">
            <div className="side-nav-group">
              {NAV_ITEMS.map((it) => {
                const Icon = it.icon
                const navLabel = t(`nav.${it.id}`)
                const navGloss = glossT(`nav.${it.id}`)
                const isActive = tab === it.id || (it.id === 'gallery' && tab === 'crosswalk')
                return (
                  <button
                    key={it.id}
                    type="button"
                    className={`side-nav-item${isActive ? ' active' : ''}`}
                    onClick={() => navTo(it.id)}
                    aria-current={isActive ? 'page' : undefined}
                    // たたんだ状態・860px 以下でラベルが display:none になる
                    // アイコンレールでも名前が残るように（ツールチップ兼
                    // スクリーンリーダー名・契約メモ §1-2）。
                    aria-label={navLabel}
                    title={navLabel}
                  >
                    <Icon className="side-nav-icon" />
                    <span className="side-nav-text">{navLabel}</span>
                    <span className="side-nav-en">{navGloss}</span>
                  </button>
                )
              })}
            </div>
          </nav>

          <div className="sidebar-foot">
            {/* たたむ／ひろげる（契約メモ §1-2）。下段は「たたむ」と「設定」だけ。
                たたんだ状態でも設定は残る（アイコンのみ・title/aria-label で名前は引ける）。 */}
            <button
              type="button"
              className="side-nav-item side-nav-collapse"
              onClick={toggleNavCollapsed}
              aria-expanded={!navCollapsed}
              aria-controls="app-side-nav"
              aria-label={t(navCollapsed ? 'nav.expand' : 'nav.collapse')}
              title={t(navCollapsed ? 'nav.expand' : 'nav.collapse')}
            >
              <ChevronIcon className={`side-nav-icon side-nav-collapse-icon${navCollapsed ? '' : ' side-nav-collapse-icon--open'}`} />
              <span className="side-nav-text">{t(navCollapsed ? 'nav.expand' : 'nav.collapse')}</span>
            </button>
            <button
              type="button"
              className="side-nav-item side-nav-settings"
              onClick={openSettings}
              aria-label={tSettings('open')}
              title={tSettings('open')}
            >
              <GearIcon className="side-nav-icon" />
              <span className="side-nav-text">{tSettings('open')}</span>
              <span className="side-nav-en">{glossSettingsT('open')}</span>
            </button>
            {/* Only the sample-data notice is shown. The old green "データ稼働中"
                came from a BUILD flag, not from the server: it stayed lit while
                Home said it could not reach anything — two contradictory claims
                on one screen. A real connection indicator needs a live check. */}
            {isMockMode && (
              <div className="graph-status">
                <span className="status-dot status-dot--mock" />
                {t('status.mock')}
              </div>
            )}
          </div>
        </aside>

        <div className="app-main">
          {/* ワークスペース（cards）の子ナビ＝第 2 列（契約メモ §1-3・O52）。
              左ナビと本文のあいだ。F5 の /define・/details のあいだも出たまま
              （route.datasetSub があっても tab は 'cards' のまま）。 */}
          {tab === 'cards' && (
            <SubjectRail route={route} navigate={navigate} />
          )}
          <div className="app-main-body" ref={mainRef}>
          <header className="topbar">
            <div className="topbar-titles">
              {/* ヘッダーは題と言語切替だけ（UI 再設計 PR 5）。見るの datasetSub 中だけ
                  パンくず（「<データセット名> › データの意味を定義する／詳しい情報」）。 */}
              {tab === 'cards' && route.datasetSub && (
                <span className="topbar-eyebrow topbar-eyebrow--crumb">
                  {`${cardsLabel ?? t('cards:topbar.unselected', { defaultValue: '見る' })} › ${t(`cards:topbar.${route.datasetSub}`)}`}
                </span>
              )}
              <h1 className="topbar-title">
                {/* cards タブだけ見出しが動く（契約メモ contract_pr_f9.md §1-7）:
                    追加画面（`#/cards/add`）は「オブジェクトを追加」、種類の
                    ページ・オブジェクトのページは onLabel が上げた名前
                    （cardsLabel）、未選択は「見る」。datasetSub 中も
                    見出しはデータセットの名前のまま（§1.2）。 */}
                {tab === 'cards'
                  ? route.add
                    ? t('cards:topbar.add', { defaultValue: 'オブジェクトを追加' })
                    : (cardsLabel ?? t('cards:topbar.unselected', { defaultValue: '見る' }))
                  : t(`view.${tab}.title`)}
              </h1>
            </div>
            {/* datasetSub 中だけ「戻る」（データセットのページへ・契約メモ §1.2）。 */}
            {tab === 'cards' && route.datasetSub && route.datasetPageId && (
              <button
                type="button"
                className="btn btn--ghost btn--sm topbar-back"
                onClick={() => navigate({ tab: 'cards', datasetPageId: route.datasetPageId })}
              >
                {t('cards:topbar.back')}
              </button>
            )}
            <LanguageToggle />
          </header>

          {/* 「データ」の下のタブ: データセット／つながり／記録。詳細・追加の画面では出さない。 */}
          {((tab === 'gallery' && !route.datasetId && !route.add) || tab === 'crosswalk') && (
            <div className="data-subtabs">
              <div className="ds-tabs" role="tablist">
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === 'gallery' && !route.activity}
                  className={`ds-tab${tab === 'gallery' && !route.activity ? ' active' : ''}`}
                  onClick={() => navTo('gallery')}
                >
                  {t('dataTabs.datasets')}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === 'crosswalk'}
                  className={`ds-tab${tab === 'crosswalk' ? ' active' : ''}`}
                  onClick={() => navTo('crosswalk')}
                >
                  {t('dataTabs.connections')}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === 'gallery' && !!route.activity}
                  className={`ds-tab${tab === 'gallery' && route.activity ? ' active' : ''}`}
                  onClick={() => navigate({ tab: 'gallery', activity: true })}
                >
                  {t('dataTabs.activity')}
                </button>
              </div>
            </div>
          )}

          {/* 質問する（チャット）は画面の残り高さを使い切り、各列が内側でスクロール
              する（メッセージ一覧はスクロール・入力欄は下に固定）。他画面は従来通り
              .app-main-body がスクロールコンテナ。 */}
          <main className={`app-content${tab === 'ask' ? ' app-content--chat' : ''}`}>
            {tab === 'home' && (
              <SkyView
                deep={!!route.deep}
                onDeep={(on) => navigate(on ? { tab: 'home', deep: true } : { tab: 'home' })}
                newIris={route.newIris ?? []}
                onNewConsumed={() => {
                  // new だけを外す（deep は残す）。履歴は積まない。
                  const next = routeToHash({ tab: 'home', deep: route.deep })
                  if (window.location.hash !== next) window.history.replaceState(null, '', next)
                  setRoute({ tab: 'home', deep: route.deep })
                }}
                onAddData={() => navigate({ tab: 'gallery', add: true })}
                onWriteQuestion={() => navigate({ tab: 'vocab', newQuestion: true })}
                onOpenKind={(iri) => navigate({ tab: 'cards', classPageIri: iri })}
                onOpenDataset={(id) => navigate({ tab: 'cards', datasetPageId: id })}
                onOpenTerm={(slug) => navigate({ tab: 'vocab', termSlug: slug })}
                onOpenQuestions={() => navigate({ tab: 'vocab' })}
                deepView={
                  <Suspense fallback={null}>
                    <NetworkView
                      onOpenSubject={(iri) => navigate({ tab: 'cards', subjectKey: `i:${iri}` })}
                      onOpenSet={openNetworkSet}
                    />
                  </Suspense>
                }
              />
            )}
            {tab === 'workbench' && (
              <WorkbenchTier
                redesignTarget={redesignTarget}
                onRedesignConsumed={() => setRedesignTarget(null)}
                onOpenDataset={onWorkbenchDone}
                onOpenAsk={openAsk}
                onCreateCrosswalk={() => navigate({ tab: 'crosswalk', create: true })}
              />
            )}
            {tab === 'ask' && (
              <AskView
                onShowVocab={showVocab}
                threadId={route.threadId ?? null}
                onSelectThread={(id, opts) =>
                  navigate(id ? { tab: 'ask', threadId: id } : { tab: 'ask' }, opts)
                }
                onAddData={() => navigate({ tab: 'gallery', add: true })}
                onOpenDataset={openDataset}
              />
            )}
            {/* `#/datasets/add` — データが入る唯一の入口（契約メモ
                contract_pr_f10.md §1.2）。PlaceView（形の一致の判定だけを先に
                見せる中間画面）はもう出さず、かんたんウィザードの S1（ファイル
                を置く）を直に出す。「同じ形なら設計なしで追加」の近道は S1 の
                帯（KantanWizard.tsx）が担い、完了は `onShortcutDone` で
                その種類のページへ着地する。 */}
            {tab === 'gallery' && route.add && (
              <WorkbenchTier
                redesignTarget={redesignTarget}
                onRedesignConsumed={() => setRedesignTarget(null)}
                onOpenDataset={onWorkbenchDone}
                onOpenAsk={openAsk}
                onCreateCrosswalk={() => navigate({ tab: 'crosswalk', create: true })}
                onShortcutDone={(target) => {
                  navigate(
                    target.classIri
                      ? { tab: 'cards', classPageIri: target.classIri }
                      : { tab: 'cards', datasetPageId: target.datasetId },
                  )
                }}
              />
            )}
            {tab === 'gallery' && !route.add && route.activity && <JobsView />}
            {tab === 'gallery' && !route.add && !route.activity && (
              <GalleryView
                focusClass={galleryFocus}
                selectedId={route.datasetId ?? null}
                detailTab={route.detailTab ?? 'structure'}
                onSelect={(id) =>
                  navigate(id ? { tab: 'gallery', datasetId: id } : { tab: 'gallery' })
                }
                onDetailTab={(dt) =>
                  navigate({ tab: 'gallery', datasetId: route.datasetId, detailTab: dt }, { replace: true })
                }
                onOpenCrosswalk={() => navTo('crosswalk')}
                onCreateCrosswalk={() => navigate({ tab: 'crosswalk', create: true })}
                onOpenMap={() => {
                  setMapReturn(route)
                  navTo('map')
                }}
                onAddData={() => navigate({ tab: 'gallery', add: true })}
                onRedesign={redesignDataset}
                detailFocus={detailFocus}
                onDetailFocusConsumed={() => setDetailFocus(null)}
              />
            )}
            {tab === 'vocab' &&
              (route.termSlug ? (
                <TermPage
                  slug={route.termSlug}
                  onBack={() => navigate({ tab: 'vocab' })}
                />
              ) : (
                <ThemesView
                  newQuestion={!!route.newQuestion}
                  focusTheme={route.themeId}
                  onCloseNew={() => {
                    // 3 段を閉じた: `new`・持ち込まれた題 `q`・続きの下書き `resume` を hash から外す。
                    const h = window.location.hash
                    const qi = h.indexOf('?')
                    if (qi >= 0) {
                      const params = new URLSearchParams(h.slice(qi + 1))
                      params.delete('new')
                      params.delete('q')
                      params.delete('resume')
                      const rest = params.toString()
                      window.history.replaceState(null, '', rest ? `${h.slice(0, qi)}?${rest}` : h.slice(0, qi))
                    }
                    setRoute((r) => ({ ...r, newQuestion: undefined }))
                  }}
                  onOpenAsk={openAsk}
                />
              ))}
            {tab === 'crosswalk' && (
              <CrosswalkView
                createMode={!!route.create}
                onCreateMode={(on) => navigate({ tab: 'crosswalk', create: on })}
                onAddData={() => navigate({ tab: 'gallery', add: true })}
                onOpenAsk={openAsk}
                onOpenMap={() => {
                  setMapReturn({ tab: 'crosswalk' })
                  navTo('map')
                }}
              />
            )}
            {tab === 'map' && <OntologyMapView onBack={() => navigate(mapReturn)} />}
            {tab === 'decisions' && (
              <DecisionsView
                filter={route.decisionsFilter ?? 'all'}
                about={route.decisionsAbout}
                onFilterChange={(next) =>
                  navigate(
                    {
                      tab: 'decisions',
                      ...(next !== 'all' ? { decisionsFilter: next } : {}),
                      ...(route.decisionsAbout ? { decisionsAbout: route.decisionsAbout } : {}),
                    },
                    { replace: true },
                  )
                }
                onClearAbout={() =>
                  navigate(
                    {
                      tab: 'decisions',
                      ...(route.decisionsFilter ? { decisionsFilter: route.decisionsFilter } : {}),
                    },
                    { replace: true },
                  )
                }
              />
            )}
            {tab === 'sparql' && <SparqlView />}
            {tab === 'cardsdemo' && <CardsGallery />}
            {/* 「意味を定義する」「詳しい情報」も見るの枠（SubjectRail・topbar）の
                中で開く（契約メモ contract_pr_f5.md §1.1）— tab は 'cards' の
                まま、中身だけ WorkbenchTier/GalleryView に差し替える。 */}
            {tab === 'cards' && route.datasetPageId && route.datasetSub === 'define' && (
              <div className="cards-embed">
                <WorkbenchTier
                  redesignTarget={redesignTarget}
                  onRedesignConsumed={() => setRedesignTarget(null)}
                  onOpenDataset={onWorkbenchDone}
                  onOpenAsk={openAsk}
                  onCreateCrosswalk={() => navigate({ tab: 'crosswalk', create: true })}
                />
              </div>
            )}
            {tab === 'cards' && route.datasetPageId && route.datasetSub === 'details' && (
              <>
                {meaningReadonlyFor === route.datasetPageId && route.detailTab === 'design' && (
                  <p className="cards-meaning-readonly">{t('cards:dataset.meaning_readonly')}</p>
                )}
                <div className="cards-embed">
                  <GalleryView
                    focusClass={null}
                    selectedId={route.datasetPageId}
                    detailTab={route.detailTab ?? 'structure'}
                    onSelect={(id) =>
                      navigate(
                        id
                          ? { tab: 'cards', datasetPageId: id, datasetSub: 'details' }
                          : { tab: 'cards', datasetPageId: route.datasetPageId },
                      )
                    }
                    onDetailTab={(dt) =>
                      navigate(
                        { tab: 'cards', datasetPageId: route.datasetPageId, datasetSub: 'details', detailTab: dt },
                        { replace: true },
                      )
                    }
                    onOpenCrosswalk={() => navTo('crosswalk')}
                    onCreateCrosswalk={() => navigate({ tab: 'crosswalk', create: true })}
                    onOpenMap={() => {
                      setMapReturn(route)
                      navTo('map')
                    }}
                    onAddData={() => navigate({ tab: 'gallery', add: true })}
                    // 詳しい情報（見るの枠に埋め込み）からの「見直す」は旧ナビの
                    // workbench タブへ切り替えない — 見るの枠（SubjectRail・
                    // topbar）のまま子ルート #/cards/d/<id>/define に留める
                    // （契約メモ contract_pr_f5.md §1.1・チェッカー指摘 blocker）。
                    // onDefine と同じ形: state だけ立てて cards タブの中で navigate。
                    onRedesign={(target) => {
                      setRedesignState(target)
                      navigate({ tab: 'cards', datasetPageId: route.datasetPageId, datasetSub: 'define' })
                    }}
                  />
                </div>
              </>
            )}
            {tab === 'cards' && !(route.datasetPageId && route.datasetSub) && (
              <CardsView
                route={route}
                navigate={navigate}
                onAsk={openAsk}
                onLabel={setCardsLabel}
                onDefine={onDefine}
              />
            )}
          </main>
          </div>
        </div>
      </div>
      {/* Global right-drawer AI consult (ADR design-consult-chat.md D1): available
          on every screen, not just the かんたん wizard — a domain expert can also
          ask "what does this column mean" from the Gallery or Ask. cards タブ
          （見る）だけは出さない（契約メモ §3: 質問窓口を 1 つにする §7）。
          ホーム（星図）・知りたいこと（テーマ）は、画面の中に「左に図・右に会話」の会話があるので
          出さない。データ（かんたん）は、会話（③の「AI に相談して結ぶ」）が出ているあいだだけ
          隠す（契約 pr7c A3: 会話が 2 つにならない）。 */}
      {showGlobalConsult && <ConsultDrawer />}
    </div>
  )
}

export default App
