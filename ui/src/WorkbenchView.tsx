import { useEffect, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
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
import { SchemaGroundingPanel } from './SchemaGroundingPanel'
import { useLlmSettings } from './settings/context'
import { LlmGate } from './settings/LlmGate'

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
  // Redesign: when the workbench was opened to revise an existing dataset's
  // design, the target id/name persist so a tab switch / reload keeps editing
  // the SAME dataset (re-materialize in place) instead of minting a new one.
  redesignId?: string
  redesignName?: string
}

/**
 * A dataset whose stored design the workbench should reopen for a redesign
 * (refine/edit → re-materialize in place). Passed by the catalog's "見直す"
 * action; null/undefined keeps the normal new-design flow.
 */
export interface RedesignTarget {
  datasetId: string
  datasetName: string
  proposalMd: string
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
export function WorkbenchView({
  redesignTarget,
  onRedesignConsumed,
}: {
  /** When set, the workbench opens on an EXISTING dataset's design to revise it. */
  redesignTarget?: RedesignTarget | null
  /** Called once the redesign target has seeded the workbench (so the parent can
   *  clear it and a later tab switch doesn't re-seed over the user's edits). */
  onRedesignConsumed?: () => void
} = {}) {
  const { t } = useTranslation()
  // Restore generated artifacts saved before a tab switch / reload (once).
  const [snap] = useState(loadSnapshot)

  // Two ways to add data (crosswalk-hub.md ④): from a NEW source (CSV/JSON → AI
  // designs → save), or by crossing EXISTING datasets into a shared bridge.
  const [mode, setMode] = useState<'new' | 'crosswalk'>('new')
  const [step, setStep] = useState<Step>(snap.step ?? 1)
  // Redesign target: the existing dataset being revised (id + display name). Seeded
  // from a passed `redesignTarget` or restored from the snapshot. When set, save
  // (materialize) re-materializes that SAME dataset in place rather than minting one.
  const [redesignId, setRedesignId] = useState<string | undefined>(snap.redesignId)
  const [redesignName, setRedesignName] = useState<string | undefined>(snap.redesignName)
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

  // Propose — the active model + its key come from Settings (shared, never on disk).
  const { isReady, getActiveCredentials } = useLlmSettings()
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
      redesignId,
      redesignName,
    }
    try {
      sessionStorage.setItem(WB_STORAGE, JSON.stringify(snapshot))
    } catch {
      // sessionStorage may be unavailable (private mode quota) — non-fatal.
    }
  }, [step, source, fk, markdown, domainFree, presetIds, proposal, materialized, redesignId, redesignName])

  // Seed the workbench from a redesign target during render (the "adjust state on
  // prop change" pattern — same as GalleryView's focusClass handling — so we avoid a
  // synchronous setState-in-effect cascade). When a NEW target id arrives we load the
  // existing dataset's design into the proposal and jump straight to review (step 2),
  // so the user can refine/edit then re-materialize the SAME dataset. `seededTarget`
  // remembers the last-seeded id so we seed once per target and don't clobber edits;
  // `onRedesignConsumed` lets the parent clear the prop after this lands.
  const [seededTarget, setSeededTarget] = useState<string | null>(null)
  if (redesignTarget && redesignTarget.datasetId !== seededTarget) {
    setSeededTarget(redesignTarget.datasetId)
    setMode('new')
    setRedesignId(redesignTarget.datasetId)
    setRedesignName(redesignTarget.datasetName)
    setProposal(redesignTarget.proposalMd)
    setMarkdown('')
    setProposeErr('')
    setComment('')
    setMaterialized(null)
    setStatus('')
    setStep(2)
    onRedesignConsumed?.()
  }

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
    // A fresh AI design is a NEW dataset — drop any redesign target so it doesn't
    // overwrite the dataset the user was previously revising.
    setRedesignId(undefined)
    setRedesignName(undefined)
    closeRef.current?.()
    try {
      closeRef.current = await proposeCsvs(files, composedDomain(), fks(), getActiveCredentials(), {
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

  // Shared refine driver: sends one or more review comments through the existing
  // refine flow (SSE / in-flight guard / credentials). Both the manual comment box
  // (onRefine) and the one-click "ask AI to fix" (onFixFailures) call this, so the
  // recovery/replay machinery is reused exactly. `refining` guards double-trigger.
  async function runRefine(comments: string[]) {
    if (!proposal || refining || comments.length === 0) return
    setProposeErr('')
    setStatus('refining…')
    setRefining(true)
    refineCloseRef.current?.()
    try {
      refineCloseRef.current = await refineSchema(proposal, comments, getActiveCredentials(), {
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

  async function onRefine() {
    const c = comment.trim()
    if (!c) return
    await runRefine([c])
  }

  // One-click "ask AI to fix": compose a corrective refine comment from the
  // materialize result's failing traps + warnings (so the AI gets the SPECIFIC
  // trap / function / column that broke) and send it through the SAME refine
  // flow. The user then re-materializes to re-check the traps.
  async function onFixFailures() {
    const c = composeFixComment(materialized, t)
    if (!c) return
    // Pre-fill the manual box too, so the user sees exactly what was sent.
    setComment(c)
    await runRefine([c])
  }

  async function onMaterialize() {
    // Guard: a request in flight, or a result already minted, must NOT POST again.
    // Each materialize mints a new dataset, so a stray second click would create a
    // duplicate (the very bug this prevents). "Create again" clears `materialized`
    // first, so an intentional redo is still possible.
    if (!proposal || materializing || materialized) return
    setProposeErr('')
    setMaterializing(true)
    try {
      // Redesign: re-materialize the SAME dataset in place (pass its id + keep its
      // display name) so graphs / IRIs / lifecycle / persisted source are preserved.
      const result = await materializeSchema(
        proposal,
        redesignName ?? 'dataset',
        redesignId,
      )
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

  // "Create again": clear the prior materialize result so the save button re-enables
  // for an *intentional* redo (the in-flight/done guard otherwise blocks re-POST to
  // prevent accidental duplicates). Keeps the proposal so the user stays on step 3.
  function onMaterializeAgain() {
    setMaterialized(null)
    setProposeErr('')
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
    setRedesignId(undefined)
    setRedesignName(undefined)
    sessionStorage.removeItem(WB_STORAGE)
  }

  // Completion drives the ✓ marks. Refine (2) is optional, so it has none.
  const done: Record<Step, boolean> = {
    1: proposal !== '',
    2: false,
    3: materialized !== null,
  }
  // Materialize usability (surfaced at save time, not buried in the ingest gate):
  // a result is only ingestable when it carries a non-empty declarative RML mapping
  // AND the backend marked it complete with no warnings. Otherwise (the AI proposal
  // had no §RML block, etc.) the dataset is saved but stuck — we warn prominently.
  const materializeHasRml = !!(materialized?.artifacts['mapping.rml.ttl'] ?? '').trim()
  const materializeUsable =
    !!materialized &&
    materializeHasRml &&
    materialized.complete &&
    materialized.warnings.length === 0
  // Advisory design-validation issues (bad column / wrong function parameter,
  // checked at materialize against the real source CSVs). Shown prominently and
  // fed to the one-click fix so they're corrected during design, not only at ingest.
  const validationIssues = materialized?.validation_issues ?? []
  // Whether the one-click "ask AI to fix" has something actionable: a blocking
  // failure (exit != 0 / a FAIL trap), any warning, or a design-validation issue.
  // Drives whether the fix button is shown (and guarantees composeFixComment
  // returns a non-empty string).
  const materializeHasFixable =
    !!materialized &&
    (materialized.exit_code !== 0 ||
      materialized.warnings.length > 0 ||
      validationIssues.length > 0 ||
      materialized.traps.some((tr) => tr.status === 'fail'))

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

      {redesignId && (
        <div className="wb-redesign-banner" role="status">
          <span className="wb-redesign-badge">{t('workbench:redesign.badge')}</span>
          <span className="wb-redesign-text">
            <Trans
              i18nKey="workbench:redesign.banner"
              values={{ name: redesignName ?? redesignId }}
              components={{ strong: <strong /> }}
            />
          </span>
        </div>
      )}

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
              <LlmGate />

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

              <button onClick={onPropose} disabled={proposing || files.length === 0 || !isReady}>
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
                  <button onClick={onRefine} disabled={refining || !isReady || !comment.trim()}>
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
              <SchemaGroundingPanel proposalMd={proposal} />
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
              {/* Done state: the materialize succeeded. We replace the live save button
                  with an explicit confirmation + an opt-in "Create again" — leaving an
                  enabled button here would let a second click mint a DUPLICATE dataset
                  (each materialize POST creates a new one). */}
              {materialized ? (
                <div className="materialize-outcome">
                  <p className="materialize-added" role="status">
                    ✓{' '}
                    {redesignId
                      ? t('workbench:save.addedRedesign')
                      : t('workbench:save.added')}
                  </p>
                  {/* Advisory design validation (run at materialize against the real
                      source): a bad column reference or wrong Tier 0 function parameter
                      is surfaced here — prominently, as a readable bulleted list (the
                      same rendering the ingest gate uses) — so the user fixes it BEFORE
                      ingest via the one-click "ask AI to fix" below. */}
                  {validationIssues.length > 0 && (
                    <div className="ingest-issues materialize-validation" role="alert">
                      <p className="ingest-issues-head">{t('workbench:save.validationHead')}</p>
                      <ul>
                        {validationIssues.map((issue, i) => (
                          <li key={i}>{issue}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {!materializeUsable && (
                    <div className="materialize-incomplete" role="alert">
                      <strong className="materialize-incomplete-head">
                        ⚠ {t('workbench:save.incompleteHeading')}
                      </strong>
                      <p className="materialize-incomplete-body">
                        {materializeHasRml
                          ? t('workbench:save.incomplete')
                          : t('workbench:save.noRml')}
                      </p>
                      {materialized.warnings.length > 0 && (
                        <>
                          <p className="materialize-incomplete-warnlabel">
                            {t('workbench:save.warningsLabel')}
                          </p>
                          <ul className="materialize-incomplete-warnings">
                            {materialized.warnings.map((w, i) => (
                              <li key={i}>{w}</li>
                            ))}
                          </ul>
                        </>
                      )}
                    </div>
                  )}
                  <p className="materialize-duplicate-note">{t('workbench:save.duplicateNote')}</p>
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={onMaterializeAgain}
                  >
                    {t('workbench:save.again')}
                  </button>
                </div>
              ) : (
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
              )}
              {proposeErr && <pre className="error">{proposeErr}</pre>}
              {materialized && <MaterializePanel result={materialized} csvFiles={files} />}
              {/* One-click corrective refine: when the materialize traps reported
                  blocking failures and/or warnings, compose a refine comment from
                  those specifics and send it through the SAME refine flow. The user
                  then re-materializes (the existing save flow) to re-check. */}
              {materialized && materializeHasFixable && (
                <section className="wb-fix-box" role="group" aria-label={t('workbench:fix.groupLabel')}>
                  <p className="wb-fix-hint">{t('workbench:fix.hint')}</p>
                  <button
                    type="button"
                    className="wb-fix-btn"
                    onClick={onFixFailures}
                    disabled={refining || !isReady}
                    title={!isReady ? t('workbench:fix.needKey') : undefined}
                  >
                    {refining ? (
                      <>
                        <span className="spinner" />
                        {t('workbench:fix.fixing')}
                      </>
                    ) : (
                      t('workbench:fix.fix')
                    )}
                  </button>
                  {refining && <JobProgress label={t('workbench:review.jobLabel')} status={status} />}
                  {!isReady && <p className="wb-fix-note">{t('workbench:fix.needKey')}</p>}
                </section>
              )}
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

// Trap ids that have a localized label (workbench:trap.<id>); others fall back to
// the backend's English `name`. Mirrors MaterializePanel's TRAP_IDS.
const FIX_TRAP_IDS = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8']

/**
 * Compose a corrective refine comment from a materialize result's *failing* traps
 * and warnings, so the one-click "ask AI to fix" hands the LLM the SPECIFIC
 * trap / function / column that broke (pulled from each trap's `detail` and the
 * `warnings` list) instead of a vague "please fix" request. Returns '' when there
 * is nothing actionable (no fails, no warnings) so the caller can no-op.
 */
function composeFixComment(result: MaterializeResult | null, t: TFunction): string {
  if (!result) return ''
  const trapLabel = (id: string, name: string) =>
    FIX_TRAP_IDS.includes(id) ? t(`workbench:trap.${id}`) : name
  const lines: string[] = []
  for (const tr of result.traps) {
    if (tr.status !== 'fail') continue
    const label = trapLabel(tr.id, tr.name)
    lines.push(tr.detail ? `${tr.id} ${label}: ${tr.detail}` : `${tr.id} ${label}`)
  }
  for (const w of result.warnings) lines.push(w)
  // Advisory design-validation issues (bad column / wrong function parameter,
  // checked against the real source) — fed to the AI so the one-click fix can
  // correct them at design time, in one click, instead of only at ingest.
  for (const issue of result.validation_issues ?? []) lines.push(issue)
  if (lines.length === 0) return ''
  const bullets = lines.map((l) => `- ${l}`).join('\n')
  return `${t('workbench:fix.commentIntro')}\n${bullets}`
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
