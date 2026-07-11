import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './App.css'
import { AskView } from './AskView'
import { CrosswalkView } from './CrosswalkView'
import { isMockMode } from './demoApi'
import { type DetailTab, GalleryView } from './GalleryView'
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
import { OntologyMapView } from './OntologyMapView'
import { useLlmSettings } from './settings/context'
import { SharedVocabView } from './SharedVocabView'
import { SparqlView } from './SparqlView'
import { type RedesignTarget, WorkbenchView } from './WorkbenchView'

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
// hash が唯一の真実源: 画面遷移は navigate() が hash を書き、hashchange で state
// に反映する（ブラウザの戻る/進むもそのまま効く）。

interface Route {
  tab: Tab
  datasetId?: string
  detailTab?: DetailTab
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
  if (TABS.includes(parts[0] as Tab)) return { tab: parts[0] as Tab }
  return { tab: 'home' }
}

function routeToHash(r: Route): string {
  if (r.tab === 'gallery' && r.datasetId) {
    const base = `#/datasets/${encodeURIComponent(r.datasetId)}`
    return r.detailTab && r.detailTab !== 'structure' ? `${base}/${r.detailTab}` : base
  }
  if (r.tab === 'gallery') return '#/datasets'
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
  useEffect(() => {
    document.title = t('docTitle')
    document.documentElement.lang = i18n.language.startsWith('en') ? 'en' : 'ja'
  }, [t, i18n.language])
  // Ask⇄Gallery link: a vocabulary class to focus/highlight in the Gallery when
  // the user jumps there from an Ask citation. null = no focus.
  const [galleryFocus, setGalleryFocus] = useState<string | null>(null)

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
  function openDataset(id: string, detailTab?: DetailTab) {
    setGalleryFocus(null)
    navigate({ tab: 'gallery', datasetId: id, detailTab })
  }

  return (
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
          <button
            type="button"
            className={`side-nav-item side-nav-dev${tab === 'sparql' ? ' active' : ''}`}
            onClick={() => navTo('sparql')}
            aria-current={tab === 'sparql' ? 'page' : undefined}
            aria-label={t('nav.sparql')}
            title={t('nav.sparql')}
          >
            <CodeIcon className="side-nav-icon" />
            <span className="side-nav-text">{t('nav.sparql')}</span>
            <span className="side-nav-en">{t('nav.sparqlTag')}</span>
          </button>
          <div className="graph-status">
            <span className={`status-dot ${isMockMode ? 'status-dot--mock' : 'status-dot--live'}`} />
            {isMockMode ? t('status.mock') : t('status.live')}
          </div>
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

        <main className="app-content">
          {tab === 'home' && <HomeView onNavigate={navTo} onOpenDataset={openDataset} />}
          {tab === 'workbench' && (
            <WorkbenchView
              redesignTarget={redesignTarget}
              onRedesignConsumed={() => setRedesignTarget(null)}
              onOpenDataset={openDataset}
            />
          )}
          {tab === 'ask' && <AskView onShowVocab={showVocab} />}
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
              onOpenMap={() => {
                setMapReturn(route)
                navTo('map')
              }}
              onAddData={() => navTo('workbench')}
              onRedesign={redesignDataset}
            />
          )}
          {tab === 'vocab' && <SharedVocabView />}
          {tab === 'crosswalk' && (
            <CrosswalkView
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
  )
}

export default App
