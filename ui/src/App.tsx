import { useState } from 'react'
import './App.css'
import { AskView } from './AskView'
import { isMockMode, type Citation } from './demoApi'
import { GalleryView } from './GalleryView'
import { AskIcon, BrandMark, GalleryIcon, HistoryIcon, ProposeIcon } from './icons'
import { JobsView } from './JobsView'
import { ProvenanceTrace } from './ProvenanceTrace'
import { WorkbenchView } from './WorkbenchView'

type Tab = 'workbench' | 'ask' | 'gallery' | 'jobs'

// Sidebar navigation, grouped by lifecycle phase: the workbench (CSV→RDF, a
// single stepped pipeline), consumption (Ask), and catalog (Gallery).
interface NavItem {
  id: Tab
  label: string
  icon: typeof ProposeIcon
}
const NAV_SECTIONS: { label: string; items: NavItem[] }[] = [
  {
    label: 'ワークベンチ · CSV → RDF',
    items: [{ id: 'workbench', label: 'ワークベンチ（設計）', icon: ProposeIcon }],
  },
  { label: '活用 · 取り込み済みデータ', items: [{ id: 'ask', label: 'Ask（根拠付き回答）', icon: AskIcon }] },
  { label: 'カタログ', items: [{ id: 'gallery', label: 'Gallery（語彙・マッピング）', icon: GalleryIcon }] },
  { label: '管理', items: [{ id: 'jobs', label: '取り込み履歴', icon: HistoryIcon }] },
]

// Topbar context per view: an eyebrow (which phase) + a short title.
const VIEW_META: Record<Tab, { eyebrow: string; title: string }> = {
  workbench: { eyebrow: 'ワークベンチ · CSV → RDF', title: 'ワークベンチ — CSV を RDF 化' },
  ask: { eyebrow: '活用 · 取り込み済みデータ', title: 'Ask — 根拠付き回答' },
  gallery: { eyebrow: 'カタログ', title: 'Gallery — 語彙とマッピング' },
  jobs: { eyebrow: '管理', title: '取り込み履歴' },
}

function App() {
  const [tab, setTab] = useState<Tab>('workbench')
  // Citation whose provenance trace is open (D2). null = drawer closed.
  const [traceCitation, setTraceCitation] = useState<Citation | null>(null)

  const meta = VIEW_META[tab]

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <BrandMark />
          </span>
          <span className="brand-text">
            <span className="brand-name">csv2rdf</span>
            <span className="brand-tag">研究データ → RDF</span>
          </span>
        </div>

        <nav className="side-nav">
          {NAV_SECTIONS.map((sec) => (
            <div className="side-nav-group" key={sec.label}>
              <span className="side-nav-label">{sec.label}</span>
              {sec.items.map((it) => {
                const Icon = it.icon
                return (
                  <button
                    key={it.id}
                    type="button"
                    className={`side-nav-item${tab === it.id ? ' active' : ''}`}
                    onClick={() => setTab(it.id)}
                  >
                    <Icon className="side-nav-icon" />
                    <span>{it.label}</span>
                  </button>
                )
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <span className={`status-dot ${isMockMode ? 'status-dot--mock' : 'status-dot--live'}`} />
          {isMockMode ? 'Ask・Gallery: demo データ (mock)' : 'Ask・Gallery: live'}
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-titles">
            <span className="topbar-eyebrow">{meta.eyebrow}</span>
            <h1 className="topbar-title">{meta.title}</h1>
          </div>
        </header>

        <main className="app-content">
          {tab === 'workbench' && <WorkbenchView />}
          {tab === 'ask' && <AskView onTrace={setTraceCitation} />}
          {tab === 'gallery' && <GalleryView />}
          {tab === 'jobs' && <JobsView />}
        </main>
      </div>

      {traceCitation && (
        <ProvenanceTrace citation={traceCitation} onClose={() => setTraceCitation(null)} />
      )}
    </div>
  )
}

export default App
