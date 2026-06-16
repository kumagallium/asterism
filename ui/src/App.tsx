import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './App.css'
import { AskView } from './AskView'
import { CrosswalkView } from './CrosswalkView'
import { isMockMode } from './demoApi'
import { GalleryView } from './GalleryView'
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
import { WorkbenchView } from './WorkbenchView'

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
  const [tab, setTab] = useState<Tab>('home')
  const { openSettings } = useLlmSettings()
  const tSettings = i18n.getFixedT(i18n.language, 'settings')
  const glossSettingsT = i18n.getFixedT(otherLng, 'settings')

  // Keep the document title and <html lang> in sync with the chosen language.
  useEffect(() => {
    document.title = t('docTitle')
    document.documentElement.lang = i18n.language.startsWith('en') ? 'en' : 'ja'
  }, [t, i18n.language])
  // Ask⇄Gallery link: a vocabulary class to focus/highlight in the Gallery when
  // the user jumps there from an Ask citation. null = no focus.
  const [galleryFocus, setGalleryFocus] = useState<string | null>(null)

  // Jump from a grounded answer to the ontology class that backs it.
  function showVocab(className: string) {
    setGalleryFocus(className)
    setTab('gallery')
  }

  // Manual nav clears any pending vocabulary focus.
  function navTo(id: Tab) {
    setGalleryFocus(null)
    setTab(id)
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
          >
            <GearIcon className="side-nav-icon" />
            <span className="side-nav-text">{tSettings('open')}</span>
            <span className="side-nav-en">{glossSettingsT('open')}</span>
          </button>
          <button
            type="button"
            className={`side-nav-item side-nav-dev${tab === 'sparql' ? ' active' : ''}`}
            onClick={() => navTo('sparql')}
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

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-titles">
            <span className="topbar-eyebrow">{t(`view.${tab}.eyebrow`)}</span>
            <h1 className="topbar-title">{t(`view.${tab}.title`)}</h1>
          </div>
          <span className="topbar-sub">{t(`view.${tab}.sub`)}</span>
          <LanguageToggle />
        </header>

        <main className="app-content">
          {tab === 'home' && <HomeView onNavigate={navTo} />}
          {tab === 'workbench' && <WorkbenchView />}
          {tab === 'ask' && <AskView onShowVocab={showVocab} />}
          {tab === 'gallery' && (
            <GalleryView
              focusClass={galleryFocus}
              onOpenCrosswalk={() => navTo('crosswalk')}
              onOpenMap={() => navTo('map')}
              onAddData={() => navTo('workbench')}
            />
          )}
          {tab === 'vocab' && <SharedVocabView />}
          {tab === 'crosswalk' && <CrosswalkView onOpenMap={() => navTo('map')} />}
          {tab === 'map' && <OntologyMapView onBack={() => navTo('crosswalk')} />}
          {tab === 'jobs' && <JobsView />}
          {tab === 'sparql' && <SparqlView />}
        </main>
      </div>
    </div>
  )
}

export default App
