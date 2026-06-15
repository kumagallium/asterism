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
  AddIcon,
  AskIcon,
  BrandMark,
  CatalogIcon,
  CodeIcon,
  HomeIcon,
} from './icons'
import { JobsView } from './JobsView'
import { SharedVocabView } from './SharedVocabView'
import { SparqlView } from './SparqlView'
import { WorkbenchView } from './WorkbenchView'

type Tab = 'home' | 'workbench' | 'ask' | 'gallery' | 'vocab' | 'crosswalk' | 'jobs' | 'sparql'

// New IA (design_handoff_asterism_ux): plain-language, verb-led nav that mirrors
// the user's mental model — Home → Create (add) → Use (ask/browse) → Manage.
// The pipeline-internal nouns (RDF / SPARQL) are demoted: SPARQL sits apart at
// the foot as a developer escape hatch. Labels are resolved via i18n (common.nav.*).
interface NavItem {
  id: Tab
  icon: typeof AddIcon
}
const NAV_SECTIONS: { heading: '' | 'create' | 'use' | 'manage'; items: NavItem[] }[] = [
  { heading: '', items: [{ id: 'home', icon: HomeIcon }] },
  { heading: 'create', items: [{ id: 'workbench', icon: AddIcon }] },
  {
    heading: 'use',
    items: [
      { id: 'ask', icon: AskIcon },
      { id: 'gallery', icon: CatalogIcon },
    ],
  },
  { heading: 'manage', items: [{ id: 'jobs', icon: ActivityIcon }] },
]

function App() {
  const { t, i18n } = useTranslation()
  // Keep the existing two-line nav aesthetic: the active language is primary and
  // the other language sits underneath as a muted gloss.
  const otherLng = i18n.language.startsWith('en') ? 'ja' : 'en'
  const glossT = i18n.getFixedT(otherLng, 'common')
  const [tab, setTab] = useState<Tab>('home')

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
          {NAV_SECTIONS.map((sec) => (
            <div className="side-nav-group" key={sec.heading || 'home'}>
              {sec.heading && (
                <span className="side-nav-label">{t(`nav.section.${sec.heading}`)}</span>
              )}
              {sec.items.map((it) => {
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
          ))}
        </nav>

        <div className="sidebar-foot">
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
              onOpenVocab={() => navTo('vocab')}
              onOpenCrosswalk={() => navTo('crosswalk')}
            />
          )}
          {tab === 'vocab' && <SharedVocabView onBack={() => navTo('gallery')} />}
          {tab === 'crosswalk' && <CrosswalkView onBack={() => navTo('gallery')} />}
          {tab === 'jobs' && <JobsView />}
          {tab === 'sparql' && <SparqlView />}
        </main>
      </div>
    </div>
  )
}

export default App
