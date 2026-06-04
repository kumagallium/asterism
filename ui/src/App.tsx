import { useState } from 'react'
import './App.css'
import { AskView } from './AskView'
import { isMockMode } from './demoApi'
import { GalleryView } from './GalleryView'
import { HomeView } from './HomeView'
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
import { SparqlView } from './SparqlView'
import { WorkbenchView } from './WorkbenchView'

type Tab = 'home' | 'workbench' | 'ask' | 'gallery' | 'jobs' | 'sparql'

// New IA (design_handoff_asterism_ux): plain-language, verb-led nav that mirrors
// the user's mental model — ホーム → つくる (入れる) → つかう (問う/見渡す) → 管理.
// The pipeline-internal nouns (RDF / SPARQL) are demoted: SPARQL sits apart at
// the foot as a developer escape hatch.
interface NavItem {
  id: Tab
  label: string
  en: string
  icon: typeof AddIcon
}
const NAV_SECTIONS: { heading: string; items: NavItem[] }[] = [
  { heading: '', items: [{ id: 'home', label: 'ホーム', en: 'Home', icon: HomeIcon }] },
  { heading: 'つくる', items: [{ id: 'workbench', label: 'データを追加', en: 'Add data', icon: AddIcon }] },
  {
    heading: 'つかう',
    items: [
      { id: 'ask', label: '質問する', en: 'Ask', icon: AskIcon },
      { id: 'gallery', label: 'カタログ', en: 'Catalog', icon: CatalogIcon },
    ],
  },
  { heading: '管理', items: [{ id: 'jobs', label: 'アクティビティ', en: 'Activity', icon: ActivityIcon }] },
]

// Topbar context per view: eyebrow (which phase, amber) + title + a short sub.
const VIEW_META: Record<Tab, { eyebrow: string; title: string; sub: string }> = {
  home: { eyebrow: 'はじめに', title: 'ホーム', sub: '今ある「つながったデータ」と次の一手' },
  workbench: { eyebrow: 'つくる', title: 'データを追加', sub: 'CSV から、AI と一緒に知識グラフを作る' },
  ask: { eyebrow: 'つかう', title: '質問する', sub: '取り込んだデータに、根拠つきで答える' },
  gallery: { eyebrow: 'つかう', title: 'カタログ', sub: '作ったデータの中身を見渡す' },
  jobs: { eyebrow: '管理', title: 'アクティビティ', sub: 'いつ・何が取り込まれたか' },
  sparql: { eyebrow: '管理 · 開発者向け', title: 'SPARQL', sub: '読み取り専用クエリ' },
}

function App() {
  const [tab, setTab] = useState<Tab>('home')
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

  const meta = VIEW_META[tab]

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <BrandMark />
          </span>
          <span className="brand-text">
            <span className="brand-name">asterism</span>
            <span className="brand-tag">研究データ → つながったデータ</span>
          </span>
        </div>

        <nav className="side-nav">
          {NAV_SECTIONS.map((sec) => (
            <div className="side-nav-group" key={sec.heading || 'home'}>
              {sec.heading && <span className="side-nav-label">{sec.heading}</span>}
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
                    <span className="side-nav-text">{it.label}</span>
                    <span className="side-nav-en">{it.en}</span>
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
            <span className="side-nav-text">SPARQL</span>
            <span className="side-nav-en">開発者向け</span>
          </button>
          <div className="graph-status">
            <span className={`status-dot ${isMockMode ? 'status-dot--mock' : 'status-dot--live'}`} />
            {isMockMode ? 'Ask・カタログ: demo データ (mock)' : 'グラフ稼働中'}
          </div>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-titles">
            <span className="topbar-eyebrow">{meta.eyebrow}</span>
            <h1 className="topbar-title">{meta.title}</h1>
          </div>
          <span className="topbar-sub">{meta.sub}</span>
        </header>

        <main className="app-content">
          {tab === 'home' && <HomeView onNavigate={navTo} />}
          {tab === 'workbench' && <WorkbenchView />}
          {tab === 'ask' && <AskView onShowVocab={showVocab} />}
          {tab === 'gallery' && <GalleryView focusClass={galleryFocus} />}
          {tab === 'jobs' && <JobsView />}
          {tab === 'sparql' && <SparqlView />}
        </main>
      </div>
    </div>
  )
}

export default App
