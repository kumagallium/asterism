import { useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './App.css'
import { inspectCsvs, proposeCsvs } from './api'
import { PRESET_HINTS } from './domainHints'
import { ProposalView } from './ProposalView'

type Tab = 'inspect' | 'propose'

// D7: the user-brought API key lives only in sessionStorage (cleared when the
// tab closes) and is sent as a per-request header. It is never persisted
// server-side.
const API_KEY_STORAGE = 'csv2rdf.apiKey'

function App() {
  const [tab, setTab] = useState<Tab>('inspect')
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

  return (
    <main className="container">
      <h1>csv2rdf-mcp — Step 0 Workbench</h1>

      <nav className="tabs">
        <button className={tab === 'inspect' ? 'active' : ''} onClick={() => setTab('inspect')}>
          Inspect
        </button>
        <button className={tab === 'propose' ? 'active' : ''} onClick={() => setTab('propose')}>
          Propose (AI)
        </button>
      </nav>

      <section className="controls">
        <input
          type="file"
          accept=".csv"
          multiple
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
        />
        <label>
          FK 列ヒント (カンマ区切り・任意)
          <input type="text" value={fk} placeholder="SID" onChange={(e) => setFk(e.target.value)} />
        </label>
        {files.length > 0 && <span className="hint">{files.length} file(s) selected</span>}
      </section>

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
            <section className="result">
              <ProposalView markdown={proposal} />
            </section>
          )}
        </>
      )}
    </main>
  )
}

export default App
