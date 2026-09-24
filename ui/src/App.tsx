import { useEffect, useLayoutEffect, useRef, useState } from 'react'
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
import { CrosswalkView } from './CrosswalkView'
import { isMockMode } from './demoApi'
import { BackendDownBanner } from './desktop/BackendDownBanner'
import { UpdateBanner } from './desktop/UpdateBanner'
import { type DetailFocus, type DetailTab, GalleryView } from './GalleryView'
import { HomeView } from './HomeView'
import { LanguageToggle } from './i18n/LanguageToggle'
import { BrandMark, ChevronIcon, CodeIcon, GearIcon } from './icons'
import { JobsView } from './JobsView'
import { WorkbenchTier } from './kantan/WorkbenchTier'
import { NAV_GROUPS } from './navGroups'
import { OntologyMapView } from './OntologyMapView'
import { useLlmSettings } from './settings/context'
import { SharedVocabView } from './SharedVocabView'
import { SparqlView } from './SparqlView'
import type { RedesignTarget } from './WorkbenchView'

type Tab =
  | 'home'
  | 'workbench'
  | 'ask'
  | 'gallery'
  | 'vocab'
  | 'crosswalk'
  | 'map'
  | 'jobs'
  | 'sparql'
  /** 裏タブ（NAV_GROUPS には出さない）。カード描画器 3 つの見本ページ・スクショ用。 */
  | 'cardsdemo'
  /** 「1 件／絞り込み × カード」の使う画面（object-cards-ui.md）。既定ルート
   *  （契約メモ contract_pr_e.md §3）— 左ナビ「使う」見出しの「ワークスペース」
   *  （旧「見る」・契約メモ contract_pr_f7.md §1）から入る。 */
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
  datasetId?: string
  detailTab?: DetailTab
  /** `#/crosswalk/new` — the guided "make a connection" flow, deep-linkable so other
   *  screens can send someone straight into it and back/forward still work. */
  create?: boolean
  /** `#/ask/<id>` — the open chat thread (reload / back / forward keep it). */
  threadId?: string
  /** `#/cards/place` — 「データを置く」（object-cards-ui.md 契約メモ §6.2）。 */
  place?: boolean
  /** `#/cards/place?dataset=<id>` — かんたんウィザードから「ページに戻る」で
   *  入ってきたときの、既に棚にあるデータセット（契約メモ §6.3 の
   *  `KantanWizard.tsx` の `returnTo + datasetId`）。`place` と組で使う。 */
  placeDatasetId?: string
  /** `#/cards/i/<encoded iri>` | `#/cards/s/<set_id>` — 契約メモ §1 の subject_key
   *  文字列表現をそのまま持つ（`i:<iri>` | `s:<set_id>`）。URL 変換は
   *  parseHash/routeToHash が担う。 */
  subjectKey?: string
  /** `.../c/<card_id>` — カード詳細。subjectKey と組み合わせて使う。 */
  cardId?: string
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
}

const TABS: readonly Tab[] = [
  'home',
  'workbench',
  'ask',
  'gallery',
  'vocab',
  'crosswalk',
  'map',
  'jobs',
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
  if (parts[0] === 'datasets' && parts[1]) {
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
      const subjectKey = `${parts[1]}:${decodeURIComponent(parts[2])}`
      const cardId = parts[3] === 'c' && parts[4] ? decodeURIComponent(parts[4]) : undefined
      return { tab: 'cards', subjectKey, cardId }
    }
    return { tab: 'cards' }
  }
  if (TABS.includes(parts[0] as Tab)) return { tab: parts[0] as Tab }
  return { tab: 'home' }
}

// eslint-disable-next-line react-refresh/only-export-components
export function routeToHash(r: Route): string {
  if (r.tab === 'gallery' && r.datasetId) {
    const base = `#/datasets/${encodeURIComponent(r.datasetId)}`
    return r.detailTab && r.detailTab !== 'structure' ? `${base}/${r.detailTab}` : base
  }
  if (r.tab === 'gallery') return '#/datasets'
  if (r.tab === 'crosswalk' && r.create) return '#/crosswalk/new'
  if (r.tab === 'ask' && r.threadId) return `#/ask/${encodeURIComponent(r.threadId)}`
  if (r.tab === 'workbench' && r.returnTo) return `#/workbench?returnTo=${encodeURIComponent(r.returnTo)}`
  if (r.tab === 'cards') {
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
      return r.cardId ? `${base}/c/${encodeURIComponent(r.cardId)}` : base
    }
    return '#/cards'
  }
  return `#/${r.tab}`
}

// v2 IA (design_handoff_asterism_ux/v2): a flat, object-axis nav — the sidebar
// lists only "places to look" (nouns). Creation is inline (the Home action and
// the Datasets add-tile), so there is NO global create button and "データを追加"
// (workbench) is reachable but not a nav entry. Crosswalk (つながり) and shared
// terms (共通の言葉) are promoted to first-class places; the ontology map (全体像)
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
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
  const tab = route.tab
  const { openSettings } = useLlmSettings()
  const tSettings = i18n.getFixedT(i18n.language, 'settings')
  const glossSettingsT = i18n.getFixedT(otherLng, 'settings')

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash))
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
    const onChange = () => setNavCollapsed(mq.matches)
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
  // そこへ（旧ナビ「見直す」からデータセットのページ経由で入ったとき）。無くても
  // 見るの枠の中（`#/cards/d/<id>/define`）で開いていれば、そのデータセットの
  // ページへ戻す（契約メモ contract_pr_f5.md §1.1・注意書き）。それ以外は従来
  // どおりカタログの当該データセット詳細へ（置く画面・カタログから入った経路）。
  function onWorkbenchDone(id: string, tab?: DetailTab, focus?: DetailFocus) {
    if (route.returnTo) {
      navigate(parseHash(route.returnTo))
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

          {/* 左ナビは 1 本・常に同じ（契約メモ §1・O52）: 見出し「作る」「使う」
              ＋ navGroups.ts の NAV_GROUPS。tab === 'cards' でも差し替えない
              ——「使う」の「ワークスペース」がここへの戻り道を兼ねる。 */}
          <nav className="side-nav" id="app-side-nav">
            {NAV_GROUPS.map((group) => (
              <div className="side-nav-group" key={group.key}>
                <h2 className="side-nav-group-title">{t(`nav.group_${group.key}`)}</h2>
                {group.items.map((it) => {
                  const Icon = it.icon
                  const navLabel = t(`nav.${it.id}`)
                  const navGloss = glossT(`nav.${it.id}`)
                  return (
                    <button
                      key={it.id}
                      type="button"
                      className={`side-nav-item${tab === it.id ? ' active' : ''}`}
                      onClick={() => navTo(it.id)}
                      aria-current={tab === it.id ? 'page' : undefined}
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
            ))}
          </nav>

          <div className="sidebar-foot">
            {/* たたむ／ひろげる（契約メモ §1-2）。たたんだ状態でも設定／開発者向け
                は下に残る（アイコンのみ・title/aria-label で名前は引ける）。 */}
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
            {/* The gloss slot under every other nav item holds the same label in
                the other language; under this one it holds "SPARQL", which says
                nothing about what the screen is BEFORE you open it. The name
                stays the plain one (ui-guidelines §2) and the tooltip carries
                what it is for, so nobody has to open a code editor to find out. */}
            <button
              type="button"
              className={`side-nav-item side-nav-dev${tab === 'sparql' ? ' active' : ''}`}
              onClick={() => navTo('sparql')}
              aria-current={tab === 'sparql' ? 'page' : undefined}
              aria-label={t('nav.sparql')}
              title={t('nav.sparqlTitle')}
            >
              <CodeIcon className="side-nav-icon" />
              <span className="side-nav-text">{t('nav.sparql')}</span>
              <span className="side-nav-en">{t('nav.sparqlTag')}</span>
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
              <span
                className={
                  tab === 'cards' && route.datasetSub ? 'topbar-eyebrow topbar-eyebrow--crumb' : 'topbar-eyebrow'
                }
              >
                {/* datasetSub（定義を直す／詳しい情報）中はパンくずに差し替える
                    （契約メモ contract_pr_f5.md §1.2）: 「<データセット名> ›
                    データの意味を定義する／詳しい情報」。 */}
                {tab === 'cards' && route.datasetSub
                  ? `${cardsLabel ?? t('cards:topbar.unselected', { defaultValue: '探す' })} › ${t(`cards:topbar.${route.datasetSub}`)}`
                  : t(`view.${tab}.eyebrow`)}
              </span>
              <h1 className="topbar-title">
                {/* cards タブだけ見出しが動く: 対象を選んでいればそのラベル、
                    未選択（`#/cards`・`#/cards/place`）なら「探す」（契約メモ §3・
                    §5: 「ページ（見出し）→ 対象のラベル／未選択は『探す』」）。
                    datasetSub 中も見出しはデータセットの名前のまま（§1.2）。
                    `cards:topbar.unselected` は新設キー（notes 参照）。 */}
                {tab === 'cards'
                  ? cardsLabel ?? t('cards:topbar.unselected', { defaultValue: '探す' })
                  : t(`view.${tab}.title`)}
              </h1>
            </div>
            {/* cards タブは sub を出さない（契約メモ §3）。 */}
            {tab !== 'cards' && <span className="topbar-sub">{t(`view.${tab}.sub`)}</span>}
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

          {/* 質問する（チャット）は画面の残り高さを使い切り、各列が内側でスクロール
              する（メッセージ一覧はスクロール・入力欄は下に固定）。他画面は従来通り
              .app-main-body がスクロールコンテナ。 */}
          <main className={`app-content${tab === 'ask' ? ' app-content--chat' : ''}`}>
            {tab === 'home' && (
              <HomeView
                onNavigate={navTo}
                onOpenDataset={openDataset}
                onCreateCrosswalk={() => navigate({ tab: 'crosswalk', create: true })}
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
                onAddData={() => navTo('workbench')}
                onOpenDataset={openDataset}
              />
            )}
            {tab === 'gallery' && (
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
                onAddData={() => navTo('workbench')}
                onRedesign={redesignDataset}
                detailFocus={detailFocus}
                onDetailFocusConsumed={() => setDetailFocus(null)}
              />
            )}
            {tab === 'vocab' && <SharedVocabView />}
            {tab === 'crosswalk' && (
              <CrosswalkView
                createMode={!!route.create}
                onCreateMode={(on) => navigate({ tab: 'crosswalk', create: on })}
                onAddData={() => navTo('workbench')}
                onOpenAsk={openAsk}
                onOpenMap={() => {
                  setMapReturn({ tab: 'crosswalk' })
                  navTo('map')
                }}
              />
            )}
            {tab === 'map' && <OntologyMapView onBack={() => navigate(mapReturn)} />}
            {tab === 'jobs' && <JobsView />}
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
                    onAddData={() => navTo('workbench')}
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
          （見る）だけは出さない（契約メモ §3: 質問窓口を 1 つにする §7）。 */}
      {tab !== 'cards' && <ConsultDrawer />}
    </div>
  )
}

export default App
