import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './App.css'
import { prefillAskQuestion } from './askPrefill'
import { AskView } from './AskView'
import { ConsultDrawer } from './consult/ConsultDrawer'
import { CrosswalkView } from './CrosswalkView'
import { isMockMode } from './demoApi'
import { UpdateBanner } from './desktop/UpdateBanner'
import { type DetailFocus, type DetailTab, GalleryView } from './GalleryView'
import { HomeView } from './HomeView'
import { LanguageToggle } from './i18n/LanguageToggle'
import {
  ActivityIcon,
  AskIcon,
  BrandMark,
  CodeIcon,
  ConnectIcon,
  DataIcon,
  GearIcon,
  HomeIcon,
  TermsIcon,
} from './icons'
import { JobsView } from './JobsView'
import { WorkbenchTier } from './kantan/WorkbenchTier'
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

// ---- hash ルーティング -------------------------------------------------------
// リロードで常にホームへ戻る／ディープリンク不可だった問題への最小のルータ。
//   #/home … #/sparql        画面タブ
//   #/datasets/<id>          データセット詳細（一覧⇄詳細の往復でも選択が消えない）
//   #/datasets/<id>/<tab>    詳細内タブ（structure/tools/files/connect/design）
//   #/ask/<threadId>         質問する: チャットのスレッド（#/ask だけなら新しいチャット）
// hash が唯一の真実源: 画面遷移は navigate() が hash を書き、hashchange で state
// に反映する（ブラウザの戻る/進むもそのまま効く）。

interface Route {
  tab: Tab
  datasetId?: string
  detailTab?: DetailTab
  /** `#/crosswalk/new` — the guided "make a connection" flow, deep-linkable so other
   *  screens can send someone straight into it and back/forward still work. */
  create?: boolean
  /** `#/ask/<id>` — the open chat thread (reload / back / forward keep it). */
  threadId?: string
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
]
const DETAIL_TABS: readonly DetailTab[] = ['structure', 'tools', 'files', 'connect', 'design']

function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
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
  if (TABS.includes(parts[0] as Tab)) return { tab: parts[0] as Tab }
  return { tab: 'home' }
}

function routeToHash(r: Route): string {
  if (r.tab === 'gallery' && r.datasetId) {
    const base = `#/datasets/${encodeURIComponent(r.datasetId)}`
    return r.detailTab && r.detailTab !== 'structure' ? `${base}/${r.detailTab}` : base
  }
  if (r.tab === 'gallery') return '#/datasets'
  if (r.tab === 'crosswalk' && r.create) return '#/crosswalk/new'
  if (r.tab === 'ask' && r.threadId) return `#/ask/${encodeURIComponent(r.threadId)}`
  return `#/${r.tab}`
}

// v2 IA (design_handoff_asterism_ux/v2): a flat, object-axis nav — the sidebar
// lists only "places to look" (nouns). Creation is inline (the Home action and
// the Datasets add-tile), so there is NO global create button and "データを追加"
// (workbench) is reachable but not a nav entry. Crosswalk (つながり) and shared
// terms (共通の言葉) are promoted to first-class places; the ontology map (全体像)
// is reached from つながり. SPARQL sits apart at the foot as a developer escape
// hatch. Labels are resolved via i18n (common.nav.*).
interface NavItem {
  id: Tab
  icon: typeof HomeIcon
}
const NAV_ITEMS: NavItem[] = [
  { id: 'home', icon: HomeIcon },
  { id: 'gallery', icon: DataIcon },
  { id: 'crosswalk', icon: ConnectIcon },
  { id: 'ask', icon: AskIcon },
  { id: 'vocab', icon: TermsIcon },
  { id: 'jobs', icon: ActivityIcon },
]

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
  function redesignDataset(target: RedesignTarget) {
    setGalleryFocus(null)
    setRedesignTarget(target)
    navigate({ tab: 'workbench' })
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
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark">
              <BrandMark />
            </span>
            <span className="brand-text">
              <span className="brand-name">{t('brand.name')}</span>
              <span className="brand-tag">{t('brand.tag')}</span>
            </span>
          </div>

          <nav className="side-nav">
            <div className="side-nav-group">
              {NAV_ITEMS.map((it) => {
                const Icon = it.icon
                return (
                  <button
                    key={it.id}
                    type="button"
                    className={`side-nav-item${tab === it.id ? ' active' : ''}`}
                    onClick={() => navTo(it.id)}
                    aria-current={tab === it.id ? 'page' : undefined}
                    // 860px 以下でラベルが display:none になるアイコンレールでも
                    // 名前が残るように（ツールチップ兼スクリーンリーダー名）
                    aria-label={t(`nav.${it.id}`)}
                    title={t(`nav.${it.id}`)}
                  >
                    <Icon className="side-nav-icon" />
                    <span className="side-nav-text">{t(`nav.${it.id}`)}</span>
                    <span className="side-nav-en">{glossT(`nav.${it.id}`)}</span>
                  </button>
                )
              })}
            </div>
          </nav>

          <div className="sidebar-foot">
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

        <div className="app-main" ref={mainRef}>
          <header className="topbar">
            <div className="topbar-titles">
              <span className="topbar-eyebrow">{t(`view.${tab}.eyebrow`)}</span>
              <h1 className="topbar-title">{t(`view.${tab}.title`)}</h1>
            </div>
            <span className="topbar-sub">{t(`view.${tab}.sub`)}</span>
            <LanguageToggle />
          </header>

          {/* 質問する（チャット）は画面の残り高さを使い切り、各列が内側でスクロール
              する（メッセージ一覧はスクロール・入力欄は下に固定）。他画面は従来通り
              .app-main がスクロールコンテナ。 */}
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
                onOpenDataset={openDataset}
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
          </main>
        </div>
      </div>
      {/* Global right-drawer AI consult (ADR design-consult-chat.md D1): available
          on every screen, not just the かんたん wizard — a domain expert can also
          ask "what does this column mean" from the Gallery or Ask. */}
      <ConsultDrawer />
    </div>
  )
}

export default App
