import { useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './App.css'
import { inspectCsvs, materializeSchema, proposeCsvs, refineSchema, type MaterializeResult } from './api'
import { AskView } from './AskView'
import { isMockMode, type Citation } from './demoApi'
import { PRESET_HINTS } from './domainHints'
import { GalleryView } from './GalleryView'
import { AskIcon, BrandMark, GalleryIcon, InspectIcon, ProposeIcon } from './icons'
import { MaterializePanel } from './MaterializePanel'
import { ProposalView } from './ProposalView'
import { ProvenanceTrace } from './ProvenanceTrace'

type Tab = 'inspect' | 'propose' | 'ask' | 'gallery'

// Sidebar navigation model. Items are grouped by lifecycle phase so the
// workbench (Inspect→Propose, one shared CSV), consumption (Ask), and catalog
// (Gallery) read as distinct areas — the app-shell version of the phase
// grouping we introduced for the old top tabs.
interface NavItem {
  id: Tab
  label: string
  icon: typeof InspectIcon
}
const NAV_SECTIONS: { label: string; items: NavItem[] }[] = [
  {
    label: 'ワークベンチ · CSV → RDF',
    items: [
      { id: 'inspect', label: 'Inspect（構造解析）', icon: InspectIcon },
      { id: 'propose', label: 'Propose（AI 提案）', icon: ProposeIcon },
    ],
  },
  { label: '活用 · 取り込み済みデータ', items: [{ id: 'ask', label: 'Ask（根拠付き回答）', icon: AskIcon }] },
  { label: 'カタログ', items: [{ id: 'gallery', label: 'Gallery（語彙・マッピング）', icon: GalleryIcon }] },
]

// Topbar context per view: an eyebrow (which phase) + a short title.
const VIEW_META: Record<Tab, { eyebrow: string; title: string }> = {
  inspect: { eyebrow: 'ワークベンチ · CSV → RDF', title: 'Inspect — 構造解析' },
  propose: { eyebrow: 'ワークベンチ · CSV → RDF', title: 'Propose — スキーマ提案 (AI)' },
  ask: { eyebrow: '活用 · 取り込み済みデータ', title: 'Ask — 根拠付き回答' },
  gallery: { eyebrow: 'カタログ', title: 'Gallery — 語彙とマッピング' },
}

// D7: the user-brought API key lives only in sessionStorage (cleared when the
// tab closes) and is sent as a per-request header. It is never persisted
// server-side.
const API_KEY_STORAGE = 'csv2rdf.apiKey'

function App() {
  const [tab, setTab] = useState<Tab>('inspect')
  // Citation whose provenance trace is open (D2). null = drawer closed.
  const [traceCitation, setTraceCitation] = useState<Citation | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [fk, setFk] = useState('')

  // Inspect state
  const [markdown, setMarkdown] = useState('')
  const [inspectErr, setInspectErr] = useState('')
  const [inspecting, setInspecting] = useState(false)

  // Propose state
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem(API_KEY_STORAGE) ?? '')
  const [presetIds, setPresetIds] = useState<Set<string>>(new Set())
  const [domainFree, setDomainFree] = useState('')
  const [proposal, setProposal] = useState('')
  const [status, setStatus] = useState('')
  const [proposeErr, setProposeErr] = useState('')
  const [proposing, setProposing] = useState(false)
  const closeRef = useRef<(() => void) | null>(null)

  // Refine (M1c): review comments applied to the current proposal.
  const [comment, setComment] = useState('')
  const [refining, setRefining] = useState(false)
  const refineCloseRef = useRef<(() => void) | null>(null)

  // Materialize + validate (M1d).
  const [materialized, setMaterialized] = useState<MaterializeResult | null>(null)
  const [materializing, setMaterializing] = useState(false)

  const fks = () =>
    fk
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)

  async function onInspect() {
    setInspectErr('')
    setMarkdown('')
    setInspecting(true)
    try {
      setMarkdown(await inspectCsvs(files, fks()))
    } catch (e) {
      setInspectErr(e instanceof Error ? e.message : String(e))
    } finally {
      setInspecting(false)
    }
  }

  function onApiKeyChange(value: string) {
    setApiKey(value)
    sessionStorage.setItem(API_KEY_STORAGE, value)
  }

  function togglePreset(id: string) {
    setPresetIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Compose the domain hint from ticked presets + the free-text box. Both are
  // optional (案 A): an empty hint is allowed.
  function composedDomain(): string {
    const lines = PRESET_HINTS.filter((h) => presetIds.has(h.id)).map((h) => `- ${h.text}`)
    const parts = []
    if (lines.length) parts.push(lines.join('\n'))
    if (domainFree.trim()) parts.push(domainFree.trim())
    return parts.join('\n\n')
  }

  async function onPropose() {
    setProposeErr('')
    setProposal('')
    setStatus('starting…')
    setProposing(true)
    closeRef.current?.()
    try {
      closeRef.current = await proposeCsvs(files, composedDomain(), fks(), apiKey, {
        onStatus: (m) => setStatus(m),
        onDone: (result) => {
          setProposal(result.proposal_md)
          setMaterialized(null)
          setStatus('done')
          setProposing(false)
        },
        onError: (m) => {
          setProposeErr(m)
          setStatus('')
          setProposing(false)
        },
      })
    } catch (e) {
      setProposeErr(e instanceof Error ? e.message : String(e))
      setStatus('')
      setProposing(false)
    }
  }

  async function onRefine() {
    const c = comment.trim()
    if (!c || !proposal) return
    setProposeErr('')
    setStatus('refining…')
    setRefining(true)
    refineCloseRef.current?.()
    try {
      refineCloseRef.current = await refineSchema(proposal, [c], apiKey, {
        onStatus: (m) => setStatus(m),
        onDone: (result) => {
          setProposal(result.refined_md)
          setMaterialized(null)
          setComment('')
          setStatus('refined')
          setRefining(false)
        },
        onError: (m) => {
          setProposeErr(m)
          setStatus('')
          setRefining(false)
        },
      })
    } catch (e) {
      setProposeErr(e instanceof Error ? e.message : String(e))
      setStatus('')
      setRefining(false)
    }
  }

  async function onMaterialize() {
    if (!proposal) return
    setProposeErr('')
    setMaterializing(true)
    try {
      setMaterialized(await materializeSchema(proposal))
    } catch (e) {
      setProposeErr(e instanceof Error ? e.message : String(e))
    } finally {
      setMaterializing(false)
    }
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
          {(tab === 'inspect' || tab === 'propose') && (
            <section className="controls">
              <input
                type="file"
                accept=".csv"
                multiple
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              />
              <label>
                FK 列ヒント (カンマ区切り・任意)
                <input
                  type="text"
                  value={fk}
                  placeholder="SID"
                  onChange={(e) => setFk(e.target.value)}
                />
              </label>
              {files.length > 0 ? (
                <span className="hint">
                  {files.length} file(s) selected — Inspect と Propose で同じ CSV を使います
                </span>
              ) : (
                <span className="hint">
                  ここで選んだ CSV を Inspect（構造解析）→ Propose（スキーマ提案）で共有します
                </span>
              )}
            </section>
          )}

          {tab === 'ask' && <AskView onTrace={setTraceCitation} />}

          {tab === 'gallery' && <GalleryView />}

      {tab === 'inspect' && (
        <>
          <p className="subtitle">
            CSV をアップロードすると、型 / JSON 列 / 一意性 (複合キー) の構造解析を表示します。LLM は使いません。
          </p>
          <button onClick={onInspect} disabled={inspecting || files.length === 0}>
            {inspecting ? 'Inspecting…' : 'Inspect'}
          </button>
          {inspectErr && <pre className="error">{inspectErr}</pre>}
          {markdown && (
            <section className="result">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
            </section>
          )}
        </>
      )}

      {tab === 'propose' && (
        <>
          <p className="subtitle">
            CSV から、AI が TBox / Mermaid / MIE / ingester のスキーマ案を提案します。
            実行には Anthropic API キーが必要です (このタブ内のみ保持・サーバに保存されません)。
          </p>
          <section className="controls">
            <label>
              Anthropic API キー (sk-…)
              <input
                type="password"
                value={apiKey}
                placeholder="sk-ant-…"
                onChange={(e) => onApiKeyChange(e.target.value)}
                autoComplete="off"
              />
            </label>

            <fieldset className="hints">
              <legend>ヒント (任意・当てはまるものにチェックすると精度が上がります)</legend>
              {PRESET_HINTS.map((h) => (
                <label key={h.id} className="hint-check">
                  <input
                    type="checkbox"
                    checked={presetIds.has(h.id)}
                    onChange={() => togglePreset(h.id)}
                  />
                  {h.label}
                </label>
              ))}
              <label className="domain-label">
                その他の補足 (自由記入・任意)
                <textarea
                  value={domainFree}
                  rows={2}
                  placeholder="例: Seebeck = thermopower = 熱起電力。図は WebPlotDigitizer で読み取った。"
                  onChange={(e) => setDomainFree(e.target.value)}
                />
              </label>
            </fieldset>

            <button onClick={onPropose} disabled={proposing || files.length === 0 || !apiKey}>
              {proposing ? 'Proposing…' : 'Propose'}
            </button>
            {status && <span className="hint">status: {status}</span>}
          </section>
          {proposeErr && <pre className="error">{proposeErr}</pre>}
          {proposal && (
            <>
              <section className="result">
                <ProposalView markdown={proposal} />
              </section>
              <section className="refine-box">
                <label className="domain-label">
                  レビューコメント (この提案を直したい点を書いて再生成)
                  <textarea
                    value={comment}
                    rows={2}
                    placeholder="例: Sample IRI を (SID, sample_id) の複合キーにして。ingester と設計根拠も同期更新して。"
                    onChange={(e) => setComment(e.target.value)}
                  />
                </label>
                <div className="refine-actions">
                  <button onClick={onRefine} disabled={refining || !apiKey || !comment.trim()}>
                    {refining ? 'Refining…' : 'Refine (コメントを反映)'}
                  </button>
                  <button
                    className="secondary-btn"
                    onClick={onMaterialize}
                    disabled={materializing}
                  >
                    {materializing ? 'Materializing…' : 'Materialize + 検証'}
                  </button>
                </div>
              </section>
              {materialized && <MaterializePanel result={materialized} />}
            </>
          )}
        </>
      )}
        </main>
      </div>

      {traceCitation && (
        <ProvenanceTrace citation={traceCitation} onClose={() => setTraceCitation(null)} />
      )}
    </div>
  )
}

export default App
