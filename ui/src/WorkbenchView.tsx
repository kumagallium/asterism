import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { inspectCsvs, materializeSchema, proposeCsvs, refineSchema, type MaterializeResult } from './api'
import { PRESET_HINTS } from './domainHints'
import { MaterializePanel } from './MaterializePanel'
import { ProposalView } from './ProposalView'

// D7: the user-brought API key lives only in sessionStorage (cleared when the
// tab closes) and is sent as a per-request header. It is never persisted.
const API_KEY_STORAGE = 'csv2rdf.apiKey'

type Step = 1 | 2 | 3 | 4
const STEPS: { n: Step; label: string }[] = [
  { n: 1, label: '構造解析' },
  { n: 2, label: 'スキーマ提案' },
  { n: 3, label: 'レビュー' },
  { n: 4, label: '確定・保存' },
]

// Persist the workbench's *generated artifacts* (not secrets) to sessionStorage
// so switching tabs — or reloading — doesn't lose an expensive 5-6 min proposal.
// sessionStorage (per-tab, cleared on tab close, never sent anywhere) matches
// the API key's lifetime (D7). File objects can't be serialized, so the picked
// CSVs are not persisted — only the AI-generated outputs and the inputs that
// produced them.
const WB_STORAGE = 'csv2rdf.workbench'

interface WorkbenchSnapshot {
  step: Step
  fk: string
  markdown: string
  domainFree: string
  presetIds: string[]
  proposal: string
  materialized: MaterializeResult | null
}

function loadSnapshot(): Partial<WorkbenchSnapshot> {
  try {
    return JSON.parse(sessionStorage.getItem(WB_STORAGE) ?? '{}') as Partial<WorkbenchSnapshot>
  } catch {
    return {}
  }
}

/**
 * The workbench as an explicit step flow: data source → 1 Inspect → 2 Propose
 * → 3 Refine → 4 Materialize(=保存). Previously these were two flat tabs with
 * refine/materialize buried in the propose result, so the pipeline wasn't
 * legible. The CSV/FK data source is a persistent panel (shared across steps);
 * the stepper shows progress (✓ when a step has produced output) and step 4
 * persists the bundle to the registry so it appears in the Gallery.
 */
export function WorkbenchView() {
  // Restore generated artifacts saved before a tab switch / reload (once).
  const [snap] = useState(loadSnapshot)

  const [step, setStep] = useState<Step>(snap.step ?? 1)
  const [files, setFiles] = useState<File[]>([])
  const [fk, setFk] = useState(snap.fk ?? '')

  // Inspect
  const [markdown, setMarkdown] = useState(snap.markdown ?? '')
  const [inspectErr, setInspectErr] = useState('')
  const [inspecting, setInspecting] = useState(false)

  // Propose
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem(API_KEY_STORAGE) ?? '')
  const [presetIds, setPresetIds] = useState<Set<string>>(() => new Set(snap.presetIds ?? []))
  const [domainFree, setDomainFree] = useState(snap.domainFree ?? '')
  const [proposal, setProposal] = useState(snap.proposal ?? '')
  const [status, setStatus] = useState('')
  const [proposeErr, setProposeErr] = useState('')
  const [proposing, setProposing] = useState(false)
  const closeRef = useRef<(() => void) | null>(null)

  // Refine
  const [comment, setComment] = useState('')
  const [refining, setRefining] = useState(false)
  const refineCloseRef = useRef<(() => void) | null>(null)

  // Materialize
  const [materialized, setMaterialized] = useState<MaterializeResult | null>(
    snap.materialized ?? null,
  )
  const [materializing, setMaterializing] = useState(false)

  // Persist artifacts whenever they change (cheap; sessionStorage only).
  useEffect(() => {
    const snapshot: WorkbenchSnapshot = {
      step,
      fk,
      markdown,
      domainFree,
      presetIds: [...presetIds],
      proposal,
      materialized,
    }
    try {
      sessionStorage.setItem(WB_STORAGE, JSON.stringify(snapshot))
    } catch {
      // sessionStorage may be unavailable (private mode quota) — non-fatal.
    }
  }, [step, fk, markdown, domainFree, presetIds, proposal, materialized])

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
          setStep(3) // guide to review once a proposal exists
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

  function clearWorkbench() {
    setStep(1)
    setMarkdown('')
    setInspectErr('')
    setProposal('')
    setProposeErr('')
    setComment('')
    setMaterialized(null)
    setStatus('')
    sessionStorage.removeItem(WB_STORAGE)
  }

  // Completion drives the ✓ marks. Refine (3) is optional, so it has none.
  const done: Record<Step, boolean> = {
    1: markdown !== '',
    2: proposal !== '',
    3: false,
    4: materialized !== null,
  }
  // Artifacts that were restored from a previous session (proposal exists but
  // the File objects, which can't be persisted, are gone).
  const restored = proposal !== '' && files.length === 0
  const hasArtifacts = markdown !== '' || proposal !== '' || materialized !== null

  return (
    <>
      <p className="subtitle">
        CSV をアップロードし、構造解析 → AI スキーマ提案 → レビュー → 確定・保存 の順に進めます。
        確定するとカタログ（Gallery）に保存されます。
      </p>

      {hasArtifacts && (
        <div className="wb-restore-row">
          {restored && (
            <span className="wb-restore-note">
              前回の作業を復元しました（再実行する場合のみ CSV を選び直してください）。
            </span>
          )}
          <button type="button" className="secondary-btn wb-clear-btn" onClick={clearWorkbench}>
            ワークベンチをクリア
          </button>
        </div>
      )}

      {/* Persistent data source: the CSV is shared across every step. */}
      <section className="data-source">
        <span className="data-source-label">データソース</span>
        <div className="data-source-row">
          <label className="file-btn">
            CSV を選択
            <input
              type="file"
              accept=".csv"
              multiple
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
          </label>
          <span className={`file-names${files.length ? '' : ' empty'}`}>
            {files.length ? files.map((f) => f.name).join('、') : 'ファイル未選択'}
          </span>
          <label className="fk-field">
            <span>FK 列ヒント（任意）</span>
            <input
              type="text"
              value={fk}
              placeholder="SID"
              onChange={(e) => setFk(e.target.value)}
            />
          </label>
        </div>
        <span className="hint">
          {files.length > 0
            ? `${files.length} file(s) selected — 全ステップで同じ CSV を使います`
            : 'ここで選んだ CSV を全ステップで共有します'}
        </span>
      </section>

      {/* Stepper */}
      <ol className="stepper">
        {STEPS.map((s, i) => (
          <li key={s.n} className="stepper-item">
            <button
              type="button"
              className={`step-btn${step === s.n ? ' active' : ''}${done[s.n] ? ' done' : ''}`}
              onClick={() => setStep(s.n)}
            >
              <span className="step-num">{done[s.n] ? '✓' : s.n}</span>
              <span className="step-label">{s.label}</span>
            </button>
            {i < STEPS.length - 1 && <span className="step-connector" aria-hidden="true" />}
          </li>
        ))}
      </ol>

      <div className="step-body">
        {step === 1 && (
          <>
            <p className="step-hint">CSV の型 / JSON 列 / 一意性（複合キー）を解析します。LLM は使いません。</p>
            <button onClick={onInspect} disabled={inspecting || files.length === 0}>
              {inspecting ? (
                <>
                  <span className="spinner" />
                  解析中…
                </>
              ) : (
                '構造を解析'
              )}
            </button>
            {files.length === 0 && <span className="hint">先に CSV を選択してください。</span>}
            {inspectErr && <pre className="error">{inspectErr}</pre>}
            {markdown && (
              <section className="result">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
              </section>
            )}
          </>
        )}

        {step === 2 && (
          <>
            <p className="step-hint">
              AI が TBox / Mermaid / MIE / ingester のスキーマ案を提案します。Anthropic API
              キーが必要です（このセッションのみ保持・サーバ非保存）。
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
                {proposing ? (
                  <>
                    <span className="spinner" />
                    提案中…
                  </>
                ) : (
                  'スキーマを提案'
                )}
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

        {step === 3 &&
          (proposal ? (
            <>
              <p className="step-hint">提案を確認し、直したい点をコメントすると AI が再生成します（任意）。</p>
              <section className="refine-box">
                <label className="domain-label">
                  レビューコメント
                  <textarea
                    value={comment}
                    rows={2}
                    placeholder="例: Sample IRI を (SID, sample_id) の複合キーにして。ingester と設計根拠も同期更新して。"
                    onChange={(e) => setComment(e.target.value)}
                  />
                </label>
                <div className="refine-actions">
                  <button onClick={onRefine} disabled={refining || !apiKey || !comment.trim()}>
                    {refining ? (
                      <>
                        <span className="spinner" />
                        再生成中…
                      </>
                    ) : (
                      'コメントを反映して再生成'
                    )}
                  </button>
                  {status && <span className="hint">status: {status}</span>}
                </div>
              </section>
              {proposeErr && <pre className="error">{proposeErr}</pre>}
              <section className="result">
                <ProposalView markdown={proposal} />
              </section>
            </>
          ) : (
            <p className="step-guard">先に「スキーマ提案」でスキーマを生成してください。</p>
          ))}

        {step === 4 &&
          (proposal ? (
            <>
              <p className="step-hint">
                スキーマを 4 つの artifact に分割し 8 罠を検証して<strong>カタログに保存</strong>します。
              </p>
              <button onClick={onMaterialize} disabled={materializing}>
                {materializing ? (
                  <>
                    <span className="spinner" />
                    保存中…
                  </>
                ) : (
                  '確定してカタログに保存'
                )}
              </button>
              {proposeErr && <pre className="error">{proposeErr}</pre>}
              {materialized && <MaterializePanel result={materialized} />}
            </>
          ) : (
            <p className="step-guard">先に「スキーマ提案」でスキーマを生成してください。</p>
          ))}
      </div>
    </>
  )
}
