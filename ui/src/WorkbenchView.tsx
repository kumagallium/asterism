import { Fragment, useEffect, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ApiError,
  attachSource,
  inspectCsvs,
  materializeSchema,
  proposeContinue,
  proposeCsvs,
  proposeSkeleton,
  refineSchema,
  resumeJob,
  validateDesign,
  validateSkeleton,
  type AutocorrectSummary,
  type DetectedDialect,
  type JobHandle,
  type MappingSkeleton,
  type SkeletonAnnotations,
  type SkeletonMap,
  type SkeletonMapAnnotation,
  type MaterializeResult,
  type ProposeResult,
  type RefineResult,
  type SourceDialect,
} from './api'
import { CrosswalkBuilder } from './CrosswalkBuilder'
import { SOURCE_ACCEPT, SUPPORTED_SOURCES, TABULAR_ACCEPT, type SourceKind } from './datasetsApi'
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

// Source dialect (ADR source-dialect.md — the "read settings" panel). The delimiter
// SELECT shows a translated label but its value is the canonical token the server
// pins ('\t'/'whitespace'/…), NOT a localized string (materialize contract). Mirror
// of asterism_step0.dialect._DELIMITER_LABELS.
const DELIMITER_OPTIONS: { value: string; labelKey: string }[] = [
  { value: ',', labelKey: 'workbench:dialect.delim.comma' },
  { value: '\t', labelKey: 'workbench:dialect.delim.tab' },
  { value: ';', labelKey: 'workbench:dialect.delim.semicolon' },
  { value: '|', labelKey: 'workbench:dialect.delim.pipe' },
  { value: 'whitespace', labelKey: 'workbench:dialect.delim.whitespace' },
]
// Preamble handling (ADR source-dialect.md, "Header metadata"). The value is the
// canonical token the server pins ('drop'/'keyvalue'/'lines'), NOT a localized label.
// 'drop' (default) discards the preamble; 'keyvalue'/'lines' broadcast it as columns.
const PREAMBLE_OPTIONS: { value: string; labelKey: string }[] = [
  { value: 'drop', labelKey: 'workbench:dialect.preamble.drop' },
  { value: 'keyvalue', labelKey: 'workbench:dialect.preamble.keyvalue' },
  { value: 'lines', labelKey: 'workbench:dialect.preamble.lines' },
]
// Today's clean-CSV read — the prefill for a source with no detected dialect.
const DEFAULT_DIALECT: SourceDialect = {
  encoding: 'utf-8-sig',
  delimiter: ',',
  collapse: false,
  skip_rows: 0,
  preamble: 'drop',
}
// Legacy instrument-export suffixes: Morph-KGC can't resolve their source type, so
// they're always shown in the read-settings panel even when detection was default.
const LEGACY_SUFFIXES = ['.txt', '.dat', '.asc']

function isLegacySuffix(name: string): boolean {
  const lower = name.toLowerCase()
  return LEGACY_SUFFIXES.some((s) => lower.endsWith(s))
}

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
  // Which of the two "add data" flows is active. Persisted so switching tabs /
  // reloading in the crosswalk (横断でつなぐ) flow doesn't silently drop back to
  // the CSV flow — previously `mode` was NOT in the snapshot, so the crosswalk
  // builder wasn't even rendered after a return.
  mode: 'new' | 'crosswalk'
  step: Step
  source: SourceKind
  fk: string
  markdown: string
  domainFree: string
  presetIds: string[]
  proposal: string
  materialized: MaterializeResult | null
  // Save target: when set, save (materialize) updates this SAME dataset in place
  // instead of minting a new record. Persisted so a tab switch / reload keeps
  // editing the SAME dataset. Two origins: 'catalog' = the user reopened an
  // existing dataset via 見直す; 'adopted' = THIS session created the record on
  // its first save, and every later save (やり直し / AI 修正後の再保存) updates it —
  // otherwise each retry of a failed save would leave one garbage record behind.
  redesignId?: string
  redesignName?: string
  redesignOrigin?: 'catalog' | 'adopted'
  // Whether the last save created the record or updated it in place — drives the
  // ✓ message after a save (state, not derived: the created id is adopted in the
  // same commit, so `redesignId` alone can no longer tell the two apart).
  lastSaveKind?: 'created' | 'updated'
  // Human "read settings" overrides (ADR source-dialect.md), keyed by canonical
  // source name. Persisted so a reload/re-send keeps the edits; empty ⇒ auto-detect.
  dialectOverrides?: Record<string, SourceDialect>
  // The staged skeleton awaiting the human gate (+ its deterministic evidence).
  // Persisted so a tab switch / reload mid-review doesn't throw away the paid
  // generation or the human's edits. Continuing still needs the files re-attached
  // (File objects can't be serialized) — the gate says so.
  stagedSkeleton?: MappingSkeleton | null
  stagedAnnotations?: SkeletonAnnotations | null
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
  onOpenDataset,
}: {
  /** When set, the workbench opens on an EXISTING dataset's design to revise it. */
  redesignTarget?: RedesignTarget | null
  /** Called once the redesign target has seeded the workbench (so the parent can
   *  clear it and a later tab switch doesn't re-seed over the user's edits). */
  onRedesignConsumed?: () => void
  /** 保存完了からカタログの当該データセット詳細へ直行する導線（導線切れ対策）。 */
  onOpenDataset?: (id: string) => void
} = {}) {
  const { t, i18n } = useTranslation()
  // Restore generated artifacts saved before a tab switch / reload (once).
  const [snap] = useState(loadSnapshot)

  // Two ways to add data (crosswalk-hub.md ④): from a NEW source (CSV/JSON → AI
  // designs → save), or by crossing EXISTING datasets into a shared bridge.
  // snap から復元（旧 snapshot に mode が無くても 'new' で後方互換）。
  const [mode, setMode] = useState<'new' | 'crosswalk'>(snap.mode ?? 'new')
  const [step, setStep] = useState<Step>(snap.step ?? 1)
  // Save target: the dataset that save (materialize) updates IN PLACE rather than
  // minting a new record. Seeded from a passed `redesignTarget` (catalog 見直す),
  // adopted from this session's own first save, or restored from the snapshot.
  const [redesignId, setRedesignId] = useState<string | undefined>(snap.redesignId)
  const [redesignName, setRedesignName] = useState<string | undefined>(snap.redesignName)
  // Pre-origin snapshots only ever had catalog-seeded targets.
  const [redesignOrigin, setRedesignOrigin] = useState<'catalog' | 'adopted' | undefined>(
    snap.redesignOrigin ?? (snap.redesignId ? 'catalog' : undefined),
  )
  const [lastSaveKind, setLastSaveKind] = useState<'created' | 'updated' | undefined>(
    snap.lastSaveKind,
  )
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
  // Source dialect (ADR source-dialect.md — the "read settings" panel). `sourceNames`
  // and `detectedDialects` come from /api/inspect's sidecar headers (transient — a
  // fresh inspect repopulates them); `dialectOverrides` are the human's edits (keyed
  // by canonical name, persisted for reload / re-send). Empty overrides ⇒ auto-detect.
  const [sourceNames, setSourceNames] = useState<string[]>([])
  const [detectedDialects, setDetectedDialects] = useState<Record<string, DetectedDialect>>({})
  const [dialectOverrides, setDialectOverrides] = useState<Record<string, SourceDialect>>(
    snap.dialectOverrides ?? {},
  )

  // Propose — the active model + its key come from Settings (shared, never on disk).
  const { isReady, getActiveCredentials } = useLlmSettings()
  const [presetIds, setPresetIds] = useState<Set<string>>(() => new Set(snap.presetIds ?? []))
  const [domainFree, setDomainFree] = useState(snap.domainFree ?? '')
  const [proposal, setProposal] = useState(snap.proposal ?? '')
  // Self-correction loop summary (TODO ④), surfaced on the review step. Transient
  // (not persisted in the snapshot) — it describes the just-completed propose run.
  const [autocorrect, setAutocorrect] = useState<AutocorrectSummary | null>(null)
  const [status, setStatus] = useState('')
  const [proposeErr, setProposeErr] = useState('')
  const [proposing, setProposing] = useState(false)
  // Phase 2b staged round-0: the skeleton awaiting human confirmation (null = no
  // gate open) and whether the skeleton job is running. Persisted in the snapshot
  // (a reload keeps the gate + edits; continuing needs the files re-attached).
  const [stagedSkeleton, setStagedSkeleton] = useState<MappingSkeleton | null>(
    snap.stagedSkeleton ?? null,
  )
  const [skeletonBusy, setSkeletonBusy] = useState(false)
  // Deterministic gate evidence (key uniqueness / ID previews / fix candidates)
  // for the staged skeleton. Recomputed server-side (no LLM) after a human edit.
  const [stagedAnnotations, setStagedAnnotations] = useState<SkeletonAnnotations | null>(
    snap.stagedAnnotations ?? null,
  )
  const [annotationsBusy, setAnnotationsBusy] = useState(false)
  const revalidateTimer = useRef<number | null>(null)
  const proposeJobRef = useRef<JobHandle | null>(null)

  // Refine
  const [comment, setComment] = useState('')
  const [refining, setRefining] = useState(false)
  const refineJobRef = useRef<JobHandle | null>(null)

  // Liveness of the in-flight LLM job (propose/refine/fix — at most one runs at
  // a time): epoch ms of the last server-sent SSE event, incl. ~15s heartbeats.
  // Drives the "サーバ応答: N秒前" line (and the >45s silence warning) in JobProgress.
  const [lastPulseAt, setLastPulseAt] = useState<number | null>(null)
  // Informational (non-error) job outcome — currently only "キャンセルしました",
  // shown where errors are shown but styled neutral.
  const [jobNotice, setJobNotice] = useState('')

  // Materialize
  const [materialized, setMaterialized] = useState<MaterializeResult | null>(
    snap.materialized ?? null,
  )
  const [materializing, setMaterializing] = useState(false)

  // Persist artifacts whenever they change (cheap; sessionStorage only).
  useEffect(() => {
    const snapshot: WorkbenchSnapshot = {
      mode,
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
      redesignOrigin,
      lastSaveKind,
      dialectOverrides,
      stagedSkeleton,
      stagedAnnotations,
    }
    try {
      sessionStorage.setItem(WB_STORAGE, JSON.stringify(snapshot))
    } catch {
      // sessionStorage may be unavailable (private mode quota) — non-fatal.
    }
  }, [mode, step, source, fk, markdown, domainFree, presetIds, proposal, materialized, redesignId, redesignName, redesignOrigin, lastSaveKind, dialectOverrides, stagedSkeleton, stagedAnnotations])

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
    setRedesignOrigin('catalog')
    setLastSaveKind(undefined)
    setProposal(redesignTarget.proposalMd)
    setMarkdown('')
    setProposeErr('')
    setComment('')
    setMaterialized(null)
    setStatus('')
    setStep(2)
    resetDialectContext() // FIX3: a redesign is a different dataset — drop the prior source's dialects
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
    const handle = resumeJob(job.jobId, {
      onPulse: () => {
        markActive()
        setLastPulseAt(Date.now())
      },
      onStatus: (m) => {
        markActive()
        setStatus(m === 'done' ? t('workbench:resume.restored') : t('workbench:resume.reconnecting'))
      },
      onDone: (result) => {
        if (job.kind === 'propose') {
          const r = result as ProposeResult
          setProposal(r.proposal_md)
          setMarkdown(r.inspection_md)
          applyAutocorrect(r.autocorrect)
          setStep(2)
        } else {
          setProposal((result as RefineResult).refined_md)
          setAutocorrect(null) // a refine replaced the design — the summary is stale
        }
        setMaterialized(null)
        setStatus('done')
        finish()
      },
      onError: (message) => {
        // 離席中に失敗した propose/refine（数分＋API 課金）を無言で破棄しない。
        // サーバ再起動でジョブが消えただけの場合も、同じ一行通知欄で理由を見せる。
        setJobNotice(t('workbench:job.resumedFailed', { message }))
        setStatus('')
        finish()
      },
      onCancelled: () => {
        // Terminal like done/error: clear the saved job so a reload doesn't
        // re-resume a stopped one, and say so where errors are shown.
        setJobNotice(t('workbench:job.cancelled'))
        setStatus('')
        finish()
      },
    })
    // Keep the handle in the matching ref so the JobProgress cancel button also
    // works on a resumed job.
    if (job.kind === 'propose') proposeJobRef.current = handle
    else refineJobRef.current = handle
    return () => handle.close()
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
      const result = await inspectCsvs(files, fks())
      setMarkdown(result.markdown)
      setSourceNames(result.sourceNames)
      setDetectedDialects(result.dialects)
      // Drop overrides whose source is no longer present (a changed file set) so a
      // stale entry can't linger and get re-sent. Keys are canonical, so a re-attach
      // of the SAME source keeps its override (reload durability).
      setDialectOverrides((prev) => {
        const kept: Record<string, SourceDialect> = {}
        for (const name of result.sourceNames) {
          if (prev[name]) kept[name] = prev[name]
        }
        return kept
      })
    } catch (e) {
      setInspectErr(e instanceof Error ? e.message : String(e))
    } finally {
      setInspecting(false)
    }
  }

  // Read-settings edits (ADR source-dialect.md). Setting a source's dialect makes it a
  // human override (wins over detection); reset returns it to auto-detection.
  function setDialectOverride(name: string, dialect: SourceDialect) {
    setDialectOverrides((prev) => ({ ...prev, [name]: dialect }))
  }
  function resetDialectOverride(name: string) {
    setDialectOverrides((prev) => {
      const next = { ...prev }
      delete next[name]
      return next
    })
  }

  // Drop ALL source-dialect context (detected + human overrides + the source-name list).
  // Called by every path that discards the current source context — clearWorkbench, a
  // source-kind switch, a file replacement, and the redesign seed (FIX3). Without this a
  // stale override keyed by a device's fixed filename (measurement.txt / data.txt) would
  // be KEPT and re-sent for the NEXT, unrelated file of the same name, making the server
  // read a clean CSV under the wrong dialect → silent column corruption. Emptying the
  // state (not just sessionStorage.removeItem) is what sticks: the persistence effect
  // re-snapshots the now-empty overrides, so a removed key cannot be written back.
  function resetDialectContext() {
    setDialectOverrides({})
    setSourceNames([])
    setDetectedDialects({})
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

  // Apply the self-correction summary the propose run returned: remember it for the
  // review banner and, when it did NOT converge, pre-fill the manual fix box with the
  // remaining issues so one click on "AI に修正を依頼" continues where the loop stopped.
  function applyAutocorrect(ac: AutocorrectSummary | undefined) {
    setAutocorrect(ac ?? null)
    if (ac && !ac.converged && ac.remaining_issues.length > 0) {
      setComment(
        `${t('workbench:fix.commentIntro')}\n` +
          ac.remaining_issues.map((m) => `- ${m}`).join('\n'),
      )
    }
  }

  async function onPropose() {
    setProposeErr('')
    setJobNotice('')
    setProposal('')
    setAutocorrect(null)
    setStatus('starting…')
    setProposing(true)
    setLastPulseAt(null)
    // A fresh AI design forks a NEW dataset — EXCEPT while retrying a failed save.
    // A 'catalog' target (an existing dataset reopened via 見直す) must never be
    // clobbered by a from-scratch design, and a design whose last save was usable
    // is a finished record someone may ingest later. But when THIS session's own
    // adopted record is unusable as saved (incomplete / no RML / validation
    // issues) — or was cleared for a redo — a re-propose is the retry loop: keep
    // the target so the next save recycles the failed record instead of leaving
    // one garbage dataset per attempt.
    const startNewRecord =
      redesignOrigin !== 'adopted' || (materializeUsable && validationIssues.length === 0)
    if (startNewRecord) {
      setRedesignId(undefined)
      setRedesignName(undefined)
      setRedesignOrigin(undefined)
    }
    proposeJobRef.current?.close()
    try {
      proposeJobRef.current = await proposeCsvs(
        files,
        composedDomain(),
        fks(),
        getActiveCredentials(),
        {
          onStart: (jobId) => saveJob(jobId, 'propose'),
          onStatus: (m) => setStatus(m),
          onPulse: () => setLastPulseAt(Date.now()),
          onDone: (result) => {
            setProposal(result.proposal_md)
            // Surface the inspection Propose actually used (no separate step).
            setMarkdown(result.inspection_md)
            applyAutocorrect(result.autocorrect)
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
          onCancelled: () => {
            // User-requested stop: terminal like error, but informational.
            setJobNotice(t('workbench:job.cancelled'))
            setStatus('')
            setProposing(false)
            clearJob()
          },
        },
        i18n.language,
        dialectOverrides,
      )
    } catch (e) {
      setProposeErr(e instanceof Error ? e.message : String(e))
      setStatus('')
      setProposing(false)
    }
  }

  // Phase 2b job 1: generate the skeleton for the human gate. Reuses the propose
  // job ref (so JobProgress's cancel works) but is NOT persisted for resume — the
  // gate lives in memory; a reload just re-runs it. The continue job persists.
  async function onProposeSkeleton() {
    setProposeErr('')
    setJobNotice('')
    setProposal('')
    setAutocorrect(null)
    setStagedSkeleton(null)
    setStagedAnnotations(null)
    setMaterialized(null)
    setStatus('starting…')
    setSkeletonBusy(true)
    setLastPulseAt(null)
    const startNewRecord =
      redesignOrigin !== 'adopted' || (materializeUsable && validationIssues.length === 0)
    if (startNewRecord) {
      setRedesignId(undefined)
      setRedesignName(undefined)
      setRedesignOrigin(undefined)
    }
    proposeJobRef.current?.close()
    try {
      proposeJobRef.current = await proposeSkeleton(
        files,
        composedDomain(),
        fks(),
        getActiveCredentials(),
        {
          onStatus: (m) => setStatus(m),
          onPulse: () => setLastPulseAt(Date.now()),
          onDone: (result) => {
            setStagedSkeleton(result.skeleton)
            setStagedAnnotations(result.annotations ?? null)
            setMarkdown(result.inspection_md)
            setStatus('done')
            setSkeletonBusy(false)
          },
          onError: (m) => {
            setProposeErr(m)
            setStatus('')
            setSkeletonBusy(false)
          },
          onCancelled: () => {
            setJobNotice(t('workbench:job.cancelled'))
            setStatus('')
            setSkeletonBusy(false)
          },
        },
        i18n.language,
        dialectOverrides,
      )
    } catch (e) {
      setProposeErr(e instanceof Error ? e.message : String(e))
      setStatus('')
      setSkeletonBusy(false)
    }
  }

  // A human edit to the skeleton re-checks the evidence server-side (no LLM,
  // sub-second) after a short debounce — a typo'd column or a key that
  // collapses rows is caught HERE, not minutes later in the paid continue run.
  function onSkeletonEdited(edited: MappingSkeleton) {
    setStagedSkeleton(edited)
    if (revalidateTimer.current !== null) window.clearTimeout(revalidateTimer.current)
    if (files.length === 0) return // nothing to check against (gate shows a hint)
    revalidateTimer.current = window.setTimeout(async () => {
      setAnnotationsBusy(true)
      try {
        setStagedAnnotations(await validateSkeleton(files, edited, dialectOverrides))
      } catch {
        // Evidence is enrichment — a failed re-check never blocks editing.
      } finally {
        setAnnotationsBusy(false)
      }
    }, 700)
  }

  // Phase 2b job 2: continue from the CONFIRMED (possibly edited) skeleton —
  // per-map + document + self-correction. The done result is a normal proposal,
  // so it persists like propose and lands in review (step 2).
  async function onContinueFromSkeleton() {
    if (!stagedSkeleton) return
    setProposeErr('')
    setJobNotice('')
    setProposal('')
    setAutocorrect(null)
    setStatus('starting…')
    setProposing(true)
    setLastPulseAt(null)
    proposeJobRef.current?.close()
    try {
      proposeJobRef.current = await proposeContinue(
        files,
        stagedSkeleton,
        composedDomain(),
        fks(),
        getActiveCredentials(),
        {
          onStart: (jobId) => saveJob(jobId, 'propose'),
          onStatus: (m) => setStatus(m),
          onPulse: () => setLastPulseAt(Date.now()),
          onDone: (result) => {
            setProposal(result.proposal_md)
            setMarkdown(result.inspection_md)
            applyAutocorrect(result.autocorrect)
            setMaterialized(null)
            setStagedSkeleton(null)
            setStagedAnnotations(null)
            setStatus('done')
            setProposing(false)
            clearJob()
            setStep(2)
          },
          onError: (m) => {
            setProposeErr(m)
            setStatus('')
            setProposing(false)
            clearJob()
          },
          onCancelled: () => {
            setJobNotice(t('workbench:job.cancelled'))
            setStatus('')
            setProposing(false)
            clearJob()
          },
        },
        i18n.language,
        undefined, // autocorrect: server default
        dialectOverrides,
      )
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
    setJobNotice('')
    setStatus('refining…')
    setRefining(true)
    setLastPulseAt(null)
    refineJobRef.current?.close()
    try {
      refineJobRef.current = await refineSchema(
        proposal,
        comments,
        getActiveCredentials(),
        {
          onStart: (jobId) => saveJob(jobId, 'refine'),
          onStatus: (m) => setStatus(m),
          onPulse: () => setLastPulseAt(Date.now()),
          onDone: (result) => {
            setProposal(result.refined_md)
            setAutocorrect(null) // a manual refine replaced the design — clear the loop summary
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
          onCancelled: () => {
            // User-requested stop: terminal like error, but informational.
            setJobNotice(t('workbench:job.cancelled'))
            setStatus('')
            setRefining(false)
            clearJob()
          },
        },
        i18n.language,
      )
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

  // Ask the server to STOP the in-flight LLM job (400-minute runaway guard).
  // This only *requests* the cancel — the stream's terminal `cancelled` event
  // (onCancelled above) is what settles the spinner/saved-job state. Rethrows on
  // failure so JobProgress can re-arm its button.
  function cancelActiveJob(kind: JobKind): Promise<void> {
    const handle = kind === 'propose' ? proposeJobRef.current : refineJobRef.current
    return handle ? handle.cancel() : Promise.resolve()
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
    // Guard: a request in flight, or a result already shown, must NOT POST again
    // (a stray second click on a fresh design would mint a duplicate). "保存し直す"
    // clears `materialized` first, so an intentional redo is still possible.
    if (!proposal || materializing || materialized) return
    setProposeErr('')
    setMaterializing(true)
    try {
      // With a target id the server re-materializes that SAME dataset in place
      // (id / graphs / lifecycle / persisted source preserved); without one it
      // mints a new record, which is then ADOPTED below.
      let created = !redesignId
      let result: MaterializeResult
      try {
        result = await materializeSchema(proposal, redesignName ?? 'dataset', redesignId)
      } catch (e) {
        if (!redesignId || !(e instanceof ApiError) || e.status !== 404) throw e
        // The target vanished (deleted in the catalog meanwhile) — drop it and
        // recreate ONCE, so the save is never dead-ended on a stale id.
        setRedesignId(undefined)
        setRedesignName(undefined)
        setRedesignOrigin(undefined)
        result = await materializeSchema(proposal, 'dataset')
        created = true
      }
      setMaterialized(result)
      setLastSaveKind(created ? 'created' : 'updated')
      // Adopt the minted record as this session's save target: every further save
      // (やり直し / AI 修正後の再保存, and — via the snapshot — after a reload)
      // then updates the SAME record in place instead of leaving one duplicate
      // dataset per retry.
      if (created && result.dataset?.id) {
        setRedesignId(result.dataset.id)
        setRedesignName(result.dataset.name)
        setRedesignOrigin('adopted')
      }
      // Task E: persist the design-time CSVs alongside the saved dataset so it
      // can be ingested from the catalog later with no re-attach. Best-effort —
      // never block the save (ingest can still re-upload if this fails).
      const datasetId = result.dataset?.id
      if (datasetId && files.length > 0) {
        try {
          await attachSource(datasetId, files)
          // Now that the source is persisted, re-run the advisory design check so a
          // BRAND-NEW design gets the same pre-ingest advice a redesign gets inline
          // (at materialize a fresh design has no source yet, so `validation_issues`
          // came back empty). Merge the issues into the shown result. Best-effort —
          // the hard ingest gate still re-checks, so a hiccup here never blocks.
          try {
            const issues = await validateDesign(datasetId)
            setMaterialized((prev) =>
              prev ? { ...prev, validation_issues: issues } : prev,
            )
          } catch {
            /* advisory re-check is best-effort; ingest gate is the hard check */
          }
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

  // "保存し直す": clear the prior materialize result so the save button re-enables
  // for an *intentional* redo (the in-flight/done guard otherwise blocks re-POST).
  // The session's record stays adopted as the save target, so the redo UPDATES it
  // in place — it does not mint another dataset. Keeps the proposal (step 3).
  function onMaterializeAgain() {
    setMaterialized(null)
    setProposeErr('')
  }

  // (JobProgress defined at module scope below.)

  function clearWorkbench() {
    // AI 提案（1〜6 分＋API 課金）を 1 クリックで失わないための確認。
    // 消える成果物が無いとき（提案も保存結果も無い）は確認なしでよい。
    if ((proposal || materialized) && !window.confirm(t('workbench:restore.clearConfirm'))) return
    setStep(1)
    setMarkdown('')
    setInspectErr('')
    setProposal('')
    setProposeErr('')
    setJobNotice('')
    setComment('')
    setMaterialized(null)
    setStatus('')
    setRedesignId(undefined)
    setRedesignName(undefined)
    setRedesignOrigin(undefined)
    setLastSaveKind(undefined)
    resetDialectContext() // FIX3: don't carry a stale override into the next dataset
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
          <span className="wb-redesign-badge">
            {t(
              redesignOrigin === 'adopted'
                ? 'workbench:redesign.badgeAdopted'
                : 'workbench:redesign.badge',
            )}
          </span>
          <span className="wb-redesign-text">
            <Trans
              i18nKey={
                redesignOrigin === 'adopted'
                  ? 'workbench:redesign.bannerAdopted'
                  : 'workbench:redesign.banner'
              }
              values={{ name: redesignName ?? redesignId }}
              components={{ strong: <strong /> }}
            />
          </span>
        </div>
      )}

      {hasArtifacts && (
        <div className="wb-restore-row">
          {restored && <span className="wb-restore-note">{t('workbench:restore.note')}</span>}
          <button type="button" className="btn btn--ghost btn--sm wb-clear-btn" onClick={clearWorkbench}>
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
                    resetDialectContext() // FIX3: the new source kind has its own dialects
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
              accept={SOURCE_ACCEPT[source] ?? TABULAR_ACCEPT}
              multiple
              onChange={(e) => {
                setFiles(Array.from(e.target.files ?? []))
                // FIX3: a new file set drops any stale dialect from the previous one (a
                // same-named device export must not inherit the prior override); a fresh
                // inspect re-detects and the human can re-confirm before generation.
                resetDialectContext()
              }}
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
              placeholder={source === 'json' ? 'mp_id' : 'sample_id'}
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
            <button type="button" className="btn btn--ghost btn--sm inspect-toggle" onClick={onToggleInspect}>
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
            <DialectEditor
              sourceNames={sourceNames}
              detected={detectedDialects}
              overrides={dialectOverrides}
              onChange={setDialectOverride}
              onReset={resetDialectOverride}
            />
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

              <div className="wb-generate-actions">
                <button
                  onClick={onProposeSkeleton}
                  disabled={skeletonBusy || proposing || files.length === 0 || !isReady}
                >
                  {skeletonBusy ? (
                    <>
                      <span className="spinner" />
                      {t('workbench:skeleton.generating')}
                    </>
                  ) : (
                    t('workbench:skeleton.generate')
                  )}
                </button>
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={onPropose}
                  disabled={skeletonBusy || proposing || files.length === 0 || !isReady}
                >
                  {t('workbench:skeleton.singleShot')}
                </button>
              </div>
              {(skeletonBusy || proposing) && (
                <JobProgress
                  label={
                    skeletonBusy
                      ? t('workbench:skeleton.jobLabel')
                      : stagedSkeleton
                        ? t('workbench:skeleton.continuing')
                        : t('workbench:design.jobLabel')
                  }
                  status={status}
                  lastPulseAt={lastPulseAt}
                  onCancel={() => cancelActiveJob('propose')}
                />
              )}
              {stagedSkeleton && (
                <SkeletonGate
                  skeleton={stagedSkeleton}
                  annotations={stagedAnnotations}
                  annotationsBusy={annotationsBusy}
                  canRevalidate={files.length > 0}
                  busy={proposing}
                  onChange={onSkeletonEdited}
                  onContinue={onContinueFromSkeleton}
                  onDiscard={() => {
                    setStagedSkeleton(null)
                    setStagedAnnotations(null)
                  }}
                />
              )}
            </section>
            {jobNotice && (
              <p className="job-cancelled-note" role="status">
                {jobNotice}
              </p>
            )}
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
              <AutocorrectBanner summary={autocorrect} />
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
                {refining && (
                  <JobProgress
                    label={t('workbench:review.jobLabel')}
                    status={status}
                    lastPulseAt={lastPulseAt}
                    onCancel={() => cancelActiveJob('refine')}
                  />
                )}
              </section>
              {jobNotice && (
                <p className="job-cancelled-note" role="status">
                  {jobNotice}
                </p>
              )}
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
                  with an explicit confirmation + an opt-in "保存し直す" (which now
                  UPDATES the adopted record in place — see onMaterialize). The ✓ label
                  is keyed off lastSaveKind, not redesignId: the created id is adopted
                  in the same commit, so redesignId is set even right after a create. */}
              {materialized ? (
                <div className="materialize-outcome">
                  <p className="materialize-added" role="status">
                    ✓{' '}
                    {(lastSaveKind ?? (redesignId ? 'updated' : 'created')) === 'updated'
                      ? t('workbench:save.addedRedesign')
                      : t('workbench:save.added')}
                    {/* 次工程（取り込み→公開）が住むカタログの当該データセットへ直行 */}
                    {onOpenDataset && redesignId && (
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm materialize-open-btn"
                        onClick={() => onOpenDataset(redesignId)}
                      >
                        {t('workbench:save.openDataset')}
                      </button>
                    )}
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
                    className="btn btn--ghost"
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
              {jobNotice && (
                <p className="job-cancelled-note" role="status">
                  {jobNotice}
                </p>
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
                  {refining && (
                  <JobProgress
                    label={t('workbench:review.jobLabel')}
                    status={status}
                    lastPulseAt={lastPulseAt}
                    onCancel={() => cancelActiveJob('refine')}
                  />
                )}
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
 * Honest summary of the server-side self-correction loop (TODO ④). Green when the loop
 * converged (zero remaining static issues); amber and DISTINCT when it stopped best-effort
 * with issues remaining — the user must NOT read a non-converged result as success. Always
 * qualifies the guarantee (static check against the source, not "ingest-ready"; JSON/XML
 * refs unchecked; coverage may have dropped) so the green check never over-promises.
 */
function AutocorrectBanner({ summary }: { summary: AutocorrectSummary | null }) {
  const { t } = useTranslation()
  if (!summary || !summary.enabled) return null
  const rounds = summary.rounds.length > 0 ? summary.rounds.length - 1 : 0 // exclude round 0
  const ok = summary.converged
  const headline = ok
    ? rounds === 0
      ? t('workbench:autocorrect.cleanFirst')
      : t('workbench:autocorrect.converged', { rounds })
    : t('workbench:autocorrect.bestEffort', { rounds, count: summary.final_issue_count })
  return (
    <div className={`autocorrect-banner ${ok ? 'ok' : 'warn'}`} role="status">
      <div className="autocorrect-head">
        <span aria-hidden="true">{ok ? '✓' : '⚠'}</span> {headline}
      </div>
      {!ok && summary.remaining_issues.length > 0 && (
        <>
          <div className="autocorrect-remaining-label">
            {t('workbench:autocorrect.remainingLabel')}
          </div>
          <ul className="autocorrect-remaining">
            {summary.remaining_issues.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
          <div className="autocorrect-prefill">{t('workbench:autocorrect.prefillFix')}</div>
        </>
      )}
      <div className="autocorrect-caveats">
        <div>{t('workbench:autocorrect.caveat')}</div>
        {!summary.tabular_only && <div>{t('workbench:autocorrect.tabularCaveat')}</div>}
        {summary.coverage_dropped && <div>{t('workbench:autocorrect.coverageCaveat')}</div>}
      </div>
    </div>
  )
}

/**
 * Reassuring progress card for the long (1-6 min) LLM jobs. The backend streams
 * lifecycle events (started/running + generation progress) and a ~15s heartbeat,
 * not token-by-token text, so we can't show a real % — instead we show a live
 * elapsed timer, an indeterminate animated bar, the expected duration, the last
 * status, and a liveness line ("server responded Ns ago", switching to a warning
 * past 45s of silence while EventSource auto-reconnects). The cancel button asks
 * the server to STOP the job (the 400-minute-runaway guard) — it disables itself
 * on the first click and the stream's terminal `cancelled` event settles the UI.
 */
function JobProgress({
  label,
  status,
  lastPulseAt,
  onCancel,
}: {
  label: string
  status: string
  /** Epoch ms of the last server-sent SSE event (incl. heartbeats); null until one. */
  lastPulseAt: number | null
  /** Requests a server-side cancel. A rejection re-arms the button for a retry. */
  onCancel: () => void | Promise<void>
}) {
  const { t } = useTranslation()
  const [elapsed, setElapsed] = useState(0)
  // Wall-clock "now", advanced by the same 1s interval as `elapsed` (render must
  // stay pure, so Date.now() lives in the effect, not the render body).
  const [now, setNow] = useState<number | null>(null)
  const [cancelRequested, setCancelRequested] = useState(false)
  useEffect(() => {
    const start = Date.now()
    const tick = () => {
      setElapsed(Math.floor((Date.now() - start) / 1000))
      setNow(Date.now())
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  const mm = Math.floor(elapsed / 60)
  const ss = String(elapsed % 60).padStart(2, '0')
  const showStatus = status && status !== 'done' && status !== 'refined'
  // Liveness, re-derived on the same 1s tick as `elapsed`. The server pulses at
  // least every ~15s (heartbeat), so >45s of silence means the connection is
  // down and EventSource is auto-reconnecting — worth a visible warning.
  const pulseAgeSec =
    lastPulseAt === null || now === null
      ? null
      : Math.max(0, Math.floor((now - lastPulseAt) / 1000))
  const silent = pulseAgeSec !== null && pulseAgeSec * 1000 > 45000
  function onCancelClick() {
    setCancelRequested(true)
    Promise.resolve()
      .then(() => onCancel())
      .catch(() => setCancelRequested(false))
  }
  return (
    <div className="job-progress" role="status" aria-live="polite">
      <div className="job-progress-head">
        <span className="spinner" />
        {label}
        <button
          type="button"
          className="btn btn--ghost btn--sm job-cancel-btn"
          onClick={onCancelClick}
          disabled={cancelRequested}
        >
          {cancelRequested ? t('workbench:job.cancelling') : t('workbench:job.cancel')}
        </button>
      </div>
      <div className="job-progress-bar" aria-hidden="true">
        <span />
      </div>
      <div className="job-progress-meta">
        {showStatus
          ? t('workbench:job.elapsedStatus', { mm, ss, status })
          : t('workbench:job.elapsed', { mm, ss })}
      </div>
      {pulseAgeSec !== null && (
        <div className={`job-progress-pulse${silent ? ' warn' : ''}`}>
          {silent
            ? t('workbench:job.silent', { s: pulseAgeSec })
            : t('workbench:job.pulse', { s: pulseAgeSec })}
        </div>
      )}
    </div>
  )
}

// Read-settings panel (ADR source-dialect.md): shows the DETECTED dialect of each
// non-default / legacy-suffix source BEFORE generation and lets the human correct it
// (encoding / delimiter / header offset / collapse). A clean-CSV set is all-default →
// nothing to show → the panel renders null (zero friction). An edit becomes a
// per-source override that wins over detection (and pins into §9); reset returns it to
// auto-detection. The delimiter is edited as a label but stored/sent as the canonical
// token — the two-layer contract materialize depends on.
function DialectEditor({
  sourceNames,
  detected,
  overrides,
  onChange,
  onReset,
}: {
  sourceNames: string[]
  detected: Record<string, DetectedDialect>
  overrides: Record<string, SourceDialect>
  onChange: (name: string, dialect: SourceDialect) => void
  onReset: (name: string) => void
}) {
  const { t } = useTranslation()
  const shown = sourceNames.filter((name) => detected[name] || isLegacySuffix(name))
  if (shown.length === 0) return null
  return (
    <details className="dialect-editor" open>
      <summary>{t('workbench:dialect.title')}</summary>
      <p className="hint dialect-editor-hint">{t('workbench:dialect.hint')}</p>
      {shown.map((name) => {
        const det = detected[name]
        const base: SourceDialect = det
          ? {
              encoding: det.encoding,
              delimiter: det.delimiter,
              collapse: det.collapse,
              skip_rows: det.skip_rows,
              preamble: det.preamble ?? 'drop',
            }
          : DEFAULT_DIALECT
        const override = overrides[name]
        const current = override ?? base
        const specified = override !== undefined
        const set = (patch: Partial<SourceDialect>) => onChange(name, { ...current, ...patch })
        return (
          <div key={name} className="dialect-row">
            <div className="dialect-row-head">
              <code className="dialect-row-name">{name}</code>
              <span className={`dialect-badge${specified ? ' dialect-badge--specified' : ''}`}>
                {specified
                  ? t('workbench:dialect.originSpecified')
                  : t('workbench:dialect.originDetected')}
              </span>
              {specified && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => onReset(name)}
                >
                  {t('workbench:dialect.reset')}
                </button>
              )}
            </div>
            <div className="dialect-fields">
              <label className="dialect-field">
                <span>{t('workbench:dialect.colEncoding')}</span>
                <input
                  type="text"
                  value={current.encoding}
                  onChange={(e) => set({ encoding: e.target.value })}
                />
              </label>
              <label className="dialect-field">
                <span>{t('workbench:dialect.colDelimiter')}</span>
                <select
                  value={current.delimiter}
                  onChange={(e) => set({ delimiter: e.target.value })}
                >
                  {DELIMITER_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {t(o.labelKey)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="dialect-field">
                <span>{t('workbench:dialect.colSkipRows')}</span>
                <input
                  type="number"
                  min={0}
                  value={current.skip_rows}
                  onChange={(e) => {
                    const n = Math.max(0, Math.trunc(Number(e.target.value) || 0))
                    // The preamble selector hides at skip_rows==0; reset it too so a
                    // stale `keyvalue`/`lines` can't linger and 422 the next propose
                    // (server lints preamble!='drop' with no preamble rows as invalid).
                    set(n === 0 ? { skip_rows: 0, preamble: 'drop' } : { skip_rows: n })
                  }}
                />
              </label>
              <label className="dialect-field dialect-check">
                <input
                  type="checkbox"
                  checked={current.collapse}
                  onChange={(e) => set({ collapse: e.target.checked })}
                />
                <span>{t('workbench:dialect.colCollapse')}</span>
              </label>
              {current.skip_rows > 0 && (
                <label className="dialect-field">
                  <span>{t('workbench:dialect.colPreamble')}</span>
                  <select
                    value={current.preamble ?? 'drop'}
                    onChange={(e) => set({ preamble: e.target.value })}
                  >
                    {PREAMBLE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {t(o.labelKey)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            {current.skip_rows > 0 && current.preamble !== 'drop' && (
              <p className="hint dialect-preamble-hint">{t('workbench:dialect.preamble.hint')}</p>
            )}
          </div>
        )
      })}
    </details>
  )
}

// Human-readable reasons a map's key could not be checked (kept in sync with
// skeleton_annotate's machine-readable `reason` values).
function evidenceReasonKey(reason: string | undefined): string {
  if (!reason) return 'workbench:skeleton.evidence.notChecked'
  if (reason === 'constant') return 'workbench:skeleton.evidence.constant'
  if (reason === 'missing-columns') return 'workbench:skeleton.evidence.missingColumns'
  if (reason === 'source-not-found') return 'workbench:skeleton.evidence.sourceNotFound'
  if (reason === 'no-template') return 'workbench:skeleton.evidence.noTemplate'
  if (reason.startsWith('unsupported-source-kind')) return 'workbench:skeleton.evidence.unsupported'
  return 'workbench:skeleton.evidence.notChecked'
}

// The per-map evidence block: is the key REALLY unique, shown with the data
// (real example IDs, concrete colliding rows, proven fix candidates) — so a
// domain expert can judge the skeleton without knowing what an IRI is.
function SkeletonEvidence({
  ann,
  onApplyCandidate,
}: {
  ann: SkeletonMapAnnotation
  onApplyCandidate: (columns: string[]) => void
}) {
  const { t } = useTranslation()

  const prefixWarning = ann.undeclared_prefixes.length > 0 && (
    <p className="skeleton-evidence-line skeleton-evidence-warn">
      {t('workbench:skeleton.evidence.undeclaredPrefixes', {
        prefixes: ann.undeclared_prefixes.join(', '),
      })}
    </p>
  )

  if (!ann.checkable) {
    return (
      <div className="skeleton-evidence">
        <p className="skeleton-evidence-line skeleton-evidence-muted">
          {t(evidenceReasonKey(ann.reason), {
            columns: (ann.missing_columns ?? []).join(', '),
          })}
        </p>
        {ann.reason === 'constant' && ann.expanded_template && (
          <p className="skeleton-evidence-line skeleton-evidence-muted">
            <code className="skeleton-evidence-id">{ann.expanded_template}</code>
          </p>
        )}
        {prefixWarning}
      </div>
    )
  }

  const collides = ann.is_unique === false
  return (
    <div className="skeleton-evidence">
      {ann.is_unique ? (
        <p className="skeleton-evidence-line skeleton-evidence-ok">
          ✓ {t('workbench:skeleton.evidence.unique', { rows: ann.total_rows })}
        </p>
      ) : (
        <p className="skeleton-evidence-line skeleton-evidence-bad">
          ⚠ {t('workbench:skeleton.evidence.collides', {
            total: ann.total_rows,
            colliding: ann.colliding_rows,
          })}
        </p>
      )}
      {collides &&
        (ann.collision_examples ?? []).map((ex, i) => (
          <p key={i} className="skeleton-evidence-line skeleton-evidence-muted">
            {t('workbench:skeleton.evidence.collisionExample', {
              lines: ex.line_numbers.join(', '),
              values: Object.entries(ex.key_values)
                .map(([k, v]) => `${k} = ${v}`)
                .join(', '),
              count: ex.row_count,
            })}
          </p>
        ))}
      {(ann.id_previews?.length ?? 0) > 0 && (
        <div className="skeleton-evidence-previews">
          <span className="skeleton-evidence-label">
            {t('workbench:skeleton.evidence.previewHead', { n: ann.id_previews!.length })}
          </span>
          {ann.id_previews!.map((id, i) => (
            <code key={i} className="skeleton-evidence-id">
              {id}
            </code>
          ))}
        </div>
      )}
      {collides && (ann.key_candidates?.length ?? 0) > 0 && (
        <div className="skeleton-evidence-candidates">
          <span className="skeleton-evidence-label">
            {t('workbench:skeleton.evidence.candidatesHead')}
          </span>
          {ann.key_candidates!.map((c) => (
            <button
              key={c.columns.join(' ')}
              type="button"
              className="skeleton-candidate-chip"
              title={
                c.measurement_only
                  ? t('workbench:skeleton.evidence.measurementOnly')
                  : undefined
              }
              onClick={() => onApplyCandidate(c.columns)}
            >
              {c.columns.map((col) => `{${col}}`).join(' + ')}
              {c.measurement_only && ' ⚠'}
            </button>
          ))}
        </div>
      )}
      {prefixWarning}
    </div>
  )
}

// Phase 2b human gate: the editable skeleton table. The user confirms/corrects
// the subject KEY (the single costliest error — a non-unique key collapses rows)
// and the CLASSES per map, then continues. Everything else (properties, prose) is
// generated only after this. Editing stays at the dict level; the confirmed dict
// is posted verbatim to /api/propose/continue. Each row carries deterministic
// EVIDENCE (server-computed, LLM-free) so the human judges data, not syntax.
function SkeletonGate({
  skeleton,
  annotations,
  annotationsBusy,
  canRevalidate,
  busy,
  onChange,
  onContinue,
  onDiscard,
}: {
  skeleton: MappingSkeleton
  annotations: SkeletonAnnotations | null
  annotationsBusy: boolean
  canRevalidate: boolean
  busy: boolean
  onChange: (s: MappingSkeleton) => void
  onContinue: () => void
  onDiscard: () => void
}) {
  const { t } = useTranslation()

  function updateSubject(idx: number, patch: Partial<SkeletonMap['subject']>) {
    const maps = skeleton.maps.map((m, i) =>
      i === idx ? { ...m, subject: { ...m.subject, ...patch } } : m,
    )
    onChange({ ...skeleton, maps })
  }

  // Apply a proven-unique column combination: keep the template's fixed head
  // (up to the first placeholder), swap the key part. The re-check runs after,
  // so the human immediately sees the ✓ this candidate was promised to earn.
  function applyCandidate(idx: number, columns: string[]) {
    const current = skeleton.maps[idx]?.subject.template ?? ''
    const head = current.includes('{') ? current.slice(0, current.indexOf('{')) : `${current}/`
    updateSubject(idx, {
      template: head + columns.map((c) => `{${c}}`).join('/'),
    })
  }

  function updatePrefix(name: string, iri: string) {
    onChange({ ...skeleton, prefixes: { ...skeleton.prefixes, [name]: iri } })
  }

  // Namespaces minted on a placeholder domain (example.org & co) can never be
  // published — the server evidence names them; editing the IRI re-checks like
  // any key edit (ADR instance-iri-base.md).
  const placeholderPrefixes = annotations?.placeholder_prefixes ?? []
  const placeholderSet = new Set(placeholderPrefixes.map((p) => p.prefix))

  // Warn before continuing when the evidence says a key still collapses rows —
  // soft gate: the human can proceed (small collision counts can be legitimate,
  // e.g. deliberate dedup), but never unknowingly.
  const collapsing = skeleton.maps.filter(
    (m) => annotations?.maps?.[m.name]?.is_unique === false,
  )
  function onContinueGuarded() {
    if (placeholderPrefixes.length > 0) {
      const ok = window.confirm(
        t('workbench:skeleton.ns.confirmPlaceholder', {
          prefixes: placeholderPrefixes.map((p) => p.prefix).join(', '),
        }),
      )
      if (!ok) return
    }
    if (collapsing.length > 0) {
      const ok = window.confirm(
        t('workbench:skeleton.confirmCollides', {
          maps: collapsing.map((m) => m.name).join(', '),
        }),
      )
      if (!ok) return
    }
    onContinue()
  }

  return (
    <section className="skeleton-gate">
      <h4>{t('workbench:skeleton.gateTitle')}</h4>
      <p className="skeleton-gate-hint">{t('workbench:skeleton.gateHint')}</p>
      {annotationsBusy && (
        <p className="skeleton-gate-revalidating" role="status">
          <span className="spinner" />
          {t('workbench:skeleton.evidence.revalidating')}
        </p>
      )}
      {!canRevalidate && (
        <p className="skeleton-gate-revalidating">{t('workbench:skeleton.evidence.reattach')}</p>
      )}
      <details className="skeleton-ns" open={placeholderPrefixes.length > 0}>
        <summary>
          {t('workbench:skeleton.ns.title')}
          {placeholderPrefixes.length > 0 && (
            <span className="skeleton-ns-flag">
              {t('workbench:skeleton.ns.flag', { count: placeholderPrefixes.length })}
            </span>
          )}
        </summary>
        <p className="skeleton-gate-hint">{t('workbench:skeleton.ns.hint')}</p>
        <div className="skeleton-ns-rows">
          {Object.entries(skeleton.prefixes ?? {}).map(([name, iri]) => (
            <div key={name} className="skeleton-ns-row">
              <code className="skeleton-ns-prefix">{name}:</code>
              <input
                type="text"
                className="skeleton-gate-input"
                value={iri}
                disabled={busy}
                onChange={(e) => updatePrefix(name, e.target.value)}
              />
              {placeholderSet.has(name) && (
                <p className="skeleton-evidence-line skeleton-evidence-warn">
                  {t('workbench:skeleton.ns.placeholderWarn')}
                </p>
              )}
            </div>
          ))}
        </div>
      </details>
      <div className="skeleton-gate-table-wrap">
        <table className="skeleton-gate-table">
          <thead>
            <tr>
              <th>{t('workbench:skeleton.colClass')}</th>
              <th>{t('workbench:skeleton.colSource')}</th>
              <th>{t('workbench:skeleton.colKey')}</th>
              <th>{t('workbench:skeleton.colClasses')}</th>
            </tr>
          </thead>
          <tbody>
            {skeleton.maps.map((m, idx) => {
              const usesConstant =
                m.subject.template === undefined && m.subject.constant !== undefined
              const keyValue = m.subject.template ?? m.subject.constant ?? ''
              const ann = annotations?.maps?.[m.name]
              return (
                <Fragment key={m.name}>
                  <tr className={ann ? 'skeleton-gate-row' : undefined}>
                    <td className="skeleton-gate-name">{m.name}</td>
                    <td className="skeleton-gate-source">{m.source}</td>
                    <td>
                      <input
                        type="text"
                        className="skeleton-gate-input"
                        value={keyValue}
                        disabled={busy}
                        title={m.note ?? undefined}
                        onChange={(e) =>
                          updateSubject(
                            idx,
                            usesConstant
                              ? { constant: e.target.value }
                              : { template: e.target.value },
                          )
                        }
                      />
                      {m.note && <div className="skeleton-gate-note">{m.note}</div>}
                    </td>
                    <td>
                      <input
                        type="text"
                        className="skeleton-gate-input"
                        value={(m.subject.classes ?? []).join(', ')}
                        disabled={busy}
                        onChange={(e) =>
                          updateSubject(idx, {
                            classes: e.target.value
                              .split(',')
                              .map((s) => s.trim())
                              .filter(Boolean),
                          })
                        }
                      />
                    </td>
                  </tr>
                  {ann && (
                    <tr className="skeleton-evidence-row">
                      <td colSpan={4}>
                        <SkeletonEvidence
                          ann={ann}
                          onApplyCandidate={(cols) => applyCandidate(idx, cols)}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="skeleton-gate-actions">
        <button onClick={onContinueGuarded} disabled={busy}>
          {busy ? (
            <>
              <span className="spinner" />
              {t('workbench:skeleton.continuing')}
            </>
          ) : (
            t('workbench:skeleton.continue')
          )}
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => {
            if (window.confirm(t('workbench:skeleton.discardConfirm'))) onDiscard()
          }}
          disabled={busy}
        >
          {t('workbench:skeleton.discard')}
        </button>
      </div>
    </section>
  )
}
