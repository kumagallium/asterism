import { useEffect, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  attachSource,
  inspectCsvs,
  materializeSchema,
  proposeCsvs,
  refineSchema,
  resumeJob,
  type MaterializeResult,
  type ProposeResult,
  type RefineResult,
} from './api'
import { CrosswalkBuilder } from './CrosswalkBuilder'
import { SOURCE_ACCEPT, SUPPORTED_SOURCES, type SourceKind } from './datasetsApi'
import { DocumentPanel } from './DocumentPanel'
import { PRESET_HINTS } from './domainHints'
import { MaterializePanel } from './MaterializePanel'
import { ProposalView } from './ProposalView'

// Data-source kinds. CSV and JSON (#19) are wired end-to-end (Morph-KGC reads
// both via the RML's referenceFormulation); API/DB are shown (the redesign's
// "any structured source" promise) but disabled until their connect flow lands.
// `labelKey` is an i18n key (workbench namespace) resolved at render via t().
const SOURCES: { id: SourceKind; labelKey: string }[] = [
  { id: 'csv', labelKey: 'workbench:source.csv' },
  { id: 'json', labelKey: 'workbench:source.json' },
  { id: 'document', labelKey: 'workbench:source.document' },
  { id: 'api', labelKey: 'workbench:source.apiShort' },
  { id: 'db', labelKey: 'workbench:source.db' },
]

// D7: the user-brought API key lives only in sessionStorage (cleared when the
// tab closes) and is sent as a per-request header. It is never persisted.
const API_KEY_STORAGE = 'asterism.apiKey'

// Inspect is NOT a step: Propose re-runs the deterministic inspection itself,
// so a separate Inspect gate is redundant. It's available on demand from the
// data-source panel ("構造を見る"), and the inspection Propose actually used is
// shown inline with the proposal.
type Step = 1 | 2 | 3
// labelKey/enKey are i18n keys (workbench namespace) resolved at render via t().
const STEPS: { n: Step; labelKey: string; enKey: string }[] = [
  { n: 1, labelKey: 'workbench:step.design', enKey: 'workbench:step.designEn' },
  { n: 2, labelKey: 'workbench:step.review', enKey: 'workbench:step.reviewEn' },
  { n: 3, labelKey: 'workbench:step.save', enKey: 'workbench:step.saveEn' },
]

// Persist the workbench's *generated artifacts* (not secrets) to sessionStorage
// so switching tabs — or reloading — doesn't lose an expensive 5-6 min proposal.
// sessionStorage (per-tab, cleared on tab close, never sent anywhere) matches
// the API key's lifetime (D7). File objects can't be serialized, so the picked
// CSVs are not persisted — only the AI-generated outputs and the inputs that
// produced them.
const WB_STORAGE = 'asterism.workbench'

// In-flight LLM job (propose/refine). Persisted so a reload/crash/disconnect can
// reconnect to the server's SSE replay and recover the (often $-costing) result.
const JOB_STORAGE = 'asterism.workbench.job'
type JobKind = 'propose' | 'refine'

function saveJob(jobId: string, kind: JobKind) {
  try {
    sessionStorage.setItem(JOB_STORAGE, JSON.stringify({ jobId, kind }))
  } catch {
    /* sessionStorage may be unavailable — non-fatal */
  }
}
function clearJob() {
  sessionStorage.removeItem(JOB_STORAGE)
}
function loadJob(): { jobId: string; kind: JobKind } | null {
  try {
    const raw = sessionStorage.getItem(JOB_STORAGE)
    return raw ? (JSON.parse(raw) as { jobId: string; kind: JobKind }) : null
  } catch {
    return null
  }
}

interface WorkbenchSnapshot {
  step: Step
  source: SourceKind
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
  const { t } = useTranslation()
  // Restore generated artifacts saved before a tab switch / reload (once).
  const [snap] = useState(loadSnapshot)

  // Two ways to add data (crosswalk-hub.md ④): from a NEW source (CSV/JSON → AI
  // designs → save), or by crossing EXISTING datasets into a shared bridge.
  const [mode, setMode] = useState<'new' | 'crosswalk'>('new')
  const [step, setStep] = useState<Step>(snap.step ?? 1)
  // Selected data-source kind (#19). CSV / JSON are wired; switching kinds clears
  // any picked files since they no longer match the new kind's picker filter.
  const [source, setSource] = useState<SourceKind>(snap.source ?? 'csv')
  const [files, setFiles] = useState<File[]>([])
  const [fk, setFk] = useState(snap.fk ?? '')

  // Inspect (on-demand "構造を見る", not a step). `markdown` holds the structure
  // analysis — set either by the on-demand button or by Propose (which returns
  // the inspection it used).
  const [markdown, setMarkdown] = useState(snap.markdown ?? '')
  const [inspectErr, setInspectErr] = useState('')
  const [inspecting, setInspecting] = useState(false)
  const [showInspect, setShowInspect] = useState(false)

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
      source,
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
  }, [step, source, fk, markdown, domainFree, presetIds, proposal, materialized])

  // Resume an in-flight propose/refine job after a reload/crash/disconnect: the
  // server replays the job's events, so a result that completed while the UI was
  // gone is recovered (and a still-running one keeps streaming). All setState is
  // in the SSE callbacks (not the effect body) so the activity shows up without
  // a synchronous mount-time update.
  useEffect(() => {
    const job = loadJob()
    if (!job) return
    const markActive = () => (job.kind === 'propose' ? setProposing(true) : setRefining(true))
    const finish = () => {
      setProposing(false)
      setRefining(false)
      clearJob()
    }
    const close = resumeJob(job.jobId, {
      onStatus: (m) => {
        markActive()
        setStatus(m === 'done' ? t('workbench:resume.restored') : t('workbench:resume.reconnecting'))
      },
      onDone: (result) => {
        if (job.kind === 'propose') {
          const r = result as ProposeResult
          setProposal(r.proposal_md)
          setMarkdown(r.inspection_md)
          setStep(2)
        } else {
          setProposal((result as RefineResult).refined_md)
        }
        setMaterialized(null)
        setStatus('done')
        finish()
      },
      onError: () => {
        // Job no longer on the server (e.g. it was restarted) — drop quietly.
        setStatus('')
        finish()
      },
    })
    return () => close()
    // Mount-only: resume whatever job was persisted before this mount.
  }, [])

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

  // "構造を見る": reveal the on-demand inspection (run it if not done yet).
  function onToggleInspect() {
    const next = !showInspect
    setShowInspect(next)
    if (next && !markdown && files.length > 0) onInspect()
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
        onStart: (jobId) => saveJob(jobId, 'propose'),
        onStatus: (m) => setStatus(m),
        onDone: (result) => {
          setProposal(result.proposal_md)
          // Surface the inspection Propose actually used (no separate step).
          setMarkdown(result.inspection_md)
          setMaterialized(null)
          setStatus('done')
          setProposing(false)
          clearJob()
          setStep(2) // guide to review once a proposal exists
        },
        onError: (m) => {
          setProposeErr(m)
          setStatus('')
          setProposing(false)
          clearJob()
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
        onStart: (jobId) => saveJob(jobId, 'refine'),
        onStatus: (m) => setStatus(m),
        onDone: (result) => {
          setProposal(result.refined_md)
          setMaterialized(null)
          setComment('')
          setStatus('refined')
          setRefining(false)
          clearJob()
        },
        onError: (m) => {
          setProposeErr(m)
          setStatus('')
          setRefining(false)
          clearJob()
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
      const result = await materializeSchema(proposal)
      setMaterialized(result)
      // Task E: persist the design-time CSVs alongside the saved dataset so it
      // can be ingested from the catalog later with no re-attach. Best-effort —
      // never block the save (ingest can still re-upload if this fails).
      const datasetId = result.dataset?.id
      if (datasetId && files.length > 0) {
        try {
          await attachSource(datasetId, files)
        } catch {
          /* source persistence is a convenience, not required */
        }
      }
    } catch (e) {
      setProposeErr(e instanceof Error ? e.message : String(e))
    } finally {
      setMaterializing(false)
    }
  }

  // (JobProgress defined at module scope below.)

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

  // Completion drives the ✓ marks. Refine (2) is optional, so it has none.
  const done: Record<Step, boolean> = {
    1: proposal !== '',
    2: false,
    3: materialized !== null,
  }
  // Artifacts that were restored from a previous session (proposal exists but
  // the File objects, which can't be persisted, are gone).
  const restored = proposal !== '' && files.length === 0
  const hasArtifacts = markdown !== '' || proposal !== '' || materialized !== null

  return (
    <>
      <div className="wb-mode-switch" role="group" aria-label={t('workbench:mode.groupLabel')}>
        <button
          type="button"
          className={`wb-mode-pill${mode === 'new' ? ' active' : ''}`}
          onClick={() => setMode('new')}
        >
          {t('workbench:mode.new')} <span className="wb-mode-en">{t('workbench:mode.newTag')}</span>
        </button>
        <button
          type="button"
          className={`wb-mode-pill${mode === 'crosswalk' ? ' active' : ''}`}
          onClick={() => setMode('crosswalk')}
        >
          {t('workbench:mode.crosswalk')}{' '}
          <span className="wb-mode-en">{t('workbench:mode.crosswalkTag')}</span>
        </button>
      </div>

      {mode === 'crosswalk' ? (
        <CrosswalkBuilder />
      ) : (
        <>
      <p className="subtitle">
        <Trans i18nKey="workbench:intro" components={{ strong: <strong /> }} />
      </p>

      {hasArtifacts && (
        <div className="wb-restore-row">
          {restored && <span className="wb-restore-note">{t('workbench:restore.note')}</span>}
          <button type="button" className="secondary-btn wb-clear-btn" onClick={clearWorkbench}>
            {t('workbench:restore.clear')}
          </button>
        </div>
      )}

      {/* Persistent data source: the CSV is shared across every step. */}
      <section className="data-source">
        <div className="source-switch-row">
          <span className="data-source-label">{t('workbench:source.label')}</span>
          <div className="source-switch" role="group" aria-label={t('workbench:source.kindsGroupLabel')}>
            {SOURCES.map((s) => {
              const supported = SUPPORTED_SOURCES.includes(s.id)
              return (
                <button
                  key={s.id}
                  type="button"
                  className={`source-pill${s.id === source ? ' active' : ''}`}
                  disabled={!supported}
                  title={supported ? undefined : t('workbench:source.soonTitle')}
                  onClick={() => {
                    if (s.id === source) return
                    // Switching kinds invalidates the picked files (different picker filter).
                    setSource(s.id)
                    setFiles([])
                  }}
                >
                  {t(s.labelKey)}
                  {!supported && <span className="source-soon">{t('workbench:source.soonBadge')}</span>}
                </button>
              )
            })}
          </div>
          <span className="hint source-note">{t('workbench:source.note')}</span>
        </div>
        {source === 'document' ? (
          <DocumentPanel />
        ) : (
          <>
        <div className="data-source-row">
          <label className="file-btn">
            {source === 'json' ? t('workbench:source.pickJson') : t('workbench:source.pickCsv')}
            <input
              type="file"
              accept={SOURCE_ACCEPT[source] ?? '.csv'}
              multiple
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
          </label>
          <span className={`file-names${files.length ? '' : ' empty'}`}>
            {files.length ? files.map((f) => f.name).join('、') : t('workbench:source.noFile')}
          </span>
          <label className="fk-field">
            <span>{source === 'json' ? t('workbench:source.fkJson') : t('workbench:source.fkCsv')}</span>
            <input
              type="text"
              value={fk}
              placeholder={source === 'json' ? 'mp_id' : 'SID'}
              onChange={(e) => setFk(e.target.value)}
            />
          </label>
        </div>
        <div className="data-source-foot">
          <span className="hint">
            {files.length > 0
              ? t('workbench:source.footSelected', { n: files.length })
              : t('workbench:source.footEmpty')}
          </span>
          {files.length > 0 && (
            <button type="button" className="secondary-btn inspect-toggle" onClick={onToggleInspect}>
              {showInspect ? t('workbench:source.inspectHide') : t('workbench:source.inspectShow')}
            </button>
          )}
        </div>

        {showInspect && (
          <div className="inspect-inline">
            {inspecting && (
              <p className="trace-loading">
                <span className="spinner" />
                {t('workbench:source.analyzing')}
              </p>
            )}
            {inspectErr && <pre className="error">{inspectErr}</pre>}
            {markdown && (
              <section className="result">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
              </section>
            )}
          </div>
        )}
          </>
        )}
      </section>

      {/* Stepper — only for the schema-design (CSV/JSON) flow; a document needs none. */}
      {source !== 'document' && (
        <>
      <ol className="stepper">
        {STEPS.map((s, i) => (
          <li key={s.n} className="stepper-item">
            <button
              type="button"
              className={`step-btn${step === s.n ? ' active' : ''}${done[s.n] ? ' done' : ''}`}
              onClick={() => setStep(s.n)}
            >
              <span className="step-num">{done[s.n] ? '✓' : s.n}</span>
              <span className="step-text">
                <span className="step-label">{t(s.labelKey)}</span>
                <span className="step-en">{t(s.enKey)}</span>
              </span>
            </button>
            {i < STEPS.length - 1 && <span className="step-connector" aria-hidden="true" />}
          </li>
        ))}
      </ol>

      <div className="step-body">
        {step === 1 && (
          <>
            <p className="step-hint">
              <Trans i18nKey="workbench:design.hint" components={{ strong: <strong /> }} />
            </p>
            <section className="controls">
              <label>
                {t('workbench:design.apiKeyLabel')}
                <input
                  type="password"
                  value={apiKey}
                  placeholder="sk-ant-…"
                  onChange={(e) => onApiKeyChange(e.target.value)}
                  autoComplete="off"
                />
              </label>

              <fieldset className="hints">
                <legend>{t('workbench:design.hintsLegend')}</legend>
                {PRESET_HINTS.map((h) => (
                  <label key={h.id} className="hint-check">
                    <input
                      type="checkbox"
                      checked={presetIds.has(h.id)}
                      onChange={() => togglePreset(h.id)}
                    />
                    {t(h.label)}
                  </label>
                ))}
                <label className="domain-label">
                  {t('workbench:design.domainFreeLabel')}
                  <textarea
                    value={domainFree}
                    rows={2}
                    placeholder={t('workbench:design.domainFreePlaceholder')}
                    onChange={(e) => setDomainFree(e.target.value)}
                  />
                </label>
              </fieldset>

              <button onClick={onPropose} disabled={proposing || files.length === 0 || !apiKey}>
                {proposing ? (
                  <>
                    <span className="spinner" />
                    {t('workbench:design.proposing')}
                  </>
                ) : (
                  t('workbench:design.propose')
                )}
              </button>
              {proposing && <JobProgress label={t('workbench:design.jobLabel')} status={status} />}
            </section>
            {proposeErr && <pre className="error">{proposeErr}</pre>}
            {proposal && (
              <>
                <section className="result">
                  <ProposalView markdown={proposal} />
                </section>
                {markdown && (
                  <details className="inspect-details">
                    <summary>{t('workbench:design.inspectDetails')}</summary>
                    <section className="result">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
                    </section>
                  </details>
                )}
              </>
            )}
          </>
        )}

        {step === 2 &&
          (proposal ? (
            <>
              <p className="step-hint">{t('workbench:review.hint')}</p>
              <section className="refine-box">
                <label className="domain-label">
                  {t('workbench:review.commentLabel')}
                  <textarea
                    value={comment}
                    rows={2}
                    placeholder={t('workbench:review.commentPlaceholder')}
                    onChange={(e) => setComment(e.target.value)}
                  />
                </label>
                <div className="refine-actions">
                  <button onClick={onRefine} disabled={refining || !apiKey || !comment.trim()}>
                    {refining ? (
                      <>
                        <span className="spinner" />
                        {t('workbench:review.regenerating')}
                      </>
                    ) : (
                      t('workbench:review.regenerate')
                    )}
                  </button>
                </div>
                {refining && <JobProgress label={t('workbench:review.jobLabel')} status={status} />}
              </section>
              {proposeErr && <pre className="error">{proposeErr}</pre>}
              <section className="result">
                <ProposalView markdown={proposal} />
              </section>
            </>
          ) : (
            <p className="step-guard">{t('workbench:step.guard')}</p>
          ))}

        {step === 3 &&
          (proposal ? (
            <>
              <p className="step-hint">
                <Trans i18nKey="workbench:save.hint" components={{ strong: <strong /> }} />
              </p>
              <button onClick={onMaterialize} disabled={materializing}>
                {materializing ? (
                  <>
                    <span className="spinner" />
                    {t('workbench:save.saving')}
                  </>
                ) : (
                  t('workbench:save.save')
                )}
              </button>
              {proposeErr && <pre className="error">{proposeErr}</pre>}
              {materialized && <MaterializePanel result={materialized} csvFiles={files} />}
            </>
          ) : (
            <p className="step-guard">{t('workbench:step.guard')}</p>
          ))}
      </div>
        </>
      )}
        </>
      )}
    </>
  )
}

/**
 * Reassuring progress card for the long (1-6 min) LLM jobs. The backend streams
 * lifecycle events (started/running) + a 15s keep-alive, not token-by-token
 * text, so we can't show a real % — instead we show a live elapsed timer, an
 * indeterminate animated bar, the expected duration, and the last status, so
 * the user can see it's alive and roughly how long to wait.
 */
function JobProgress({ label, status }: { label: string; status: string }) {
  const { t } = useTranslation()
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const start = Date.now()
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => clearInterval(id)
  }, [])
  const mm = Math.floor(elapsed / 60)
  const ss = String(elapsed % 60).padStart(2, '0')
  const showStatus = status && status !== 'done' && status !== 'refined'
  return (
    <div className="job-progress" role="status" aria-live="polite">
      <div className="job-progress-head">
        <span className="spinner" />
        {label}
      </div>
      <div className="job-progress-bar" aria-hidden="true">
        <span />
      </div>
      <div className="job-progress-meta">
        {showStatus
          ? t('workbench:job.elapsedStatus', { mm, ss, status })
          : t('workbench:job.elapsed', { mm, ss })}
      </div>
    </div>
  )
}
