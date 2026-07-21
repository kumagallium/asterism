import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ApiError,
  attachSource,
  fetchDraftStats,
  IngestCancelledError,
  IngestValidationError,
  inspectCsvs,
  materializeSchema,
  proposeContinue,
  proposeSkeleton,
  refineSchema,
  resumeIngestJob,
  resumeJob,
  StaleIngestJobError,
  startIngestJob,
  validateSkeleton,
  type DraftStats,
  type IngestJobHandle,
  type IngestProgress,
  type InspectResult,
  type JobHandle,
  type MappingSkeleton,
  type MaterializeResult,
  type ProposeResult,
  type SkeletonAnnotations,
  type SourceDialect,
} from '../api'
import { TABULAR_ACCEPT } from '../datasetsApi'
import { DocumentPanel } from '../DocumentPanel'
import { PRESET_HINTS } from '../domainHints'
import { getDatasetRules, type DatasetRules, type RuleMap, type RuleProperty } from '../galleryApi'
import { clearIngestJob, loadIngestJob, saveIngestJob } from '../ingestJob'
import { JobProgress } from '../JobProgress'
import { useLlmSettings } from '../settings/context'
import { SkeletonGate } from '../SkeletonGate'
import { localName } from '../vocab'
import { RecipeCard } from './RecipeCard'

// The kantan (かんたん) tier wizard — ADR kantan-mode-two-tier-ux.md, S1-S6.
// A linear, plain-language flow over the SAME backend calls the detail tier
// uses: drop files → auto inspect → two "only you know this" questions →
// staged skeleton propose → the human row-counting gate (S4, human gate ①) →
// continue → S5 auto chain (save → source persist → DRAFT ingest, no approval
// button by design — ADR K3) → S6 column-meaning review (human gate ②) →
// confirm → hand over to the dataset screen for ためす/公開 (S7-S9, upcoming).
// No jargon may appear in this layer (no RML/IRI/namespace/canonical wording).

// Storage keys shared with the detail tier (WorkbenchView.tsx). Duplicated by
// value on purpose — exporting them from WorkbenchView would grow its diff.
const WB_STORAGE = 'asterism.workbench'
const JOB_STORAGE = 'asterism.workbench.job'
// Kantan's own snapshot (File objects can't persist — restore is best-effort).
const KZ_STORAGE = 'asterism.kantan'

type KantanKind = 'tabular' | 'json' | 'document'
type Q1Answer = 'keep' | 'drop'
type Q2Answer = 'only' | 'elsewhere' | 'unknown'
type KzStep = 1 | 2 | 3 | 4 | 5 | 6

/** Where the S5 auto chain (save → source persist → draft ingest) restarts. */
type PipeStage = 'materialize' | 'attach' | 'ingest'

/** The S5 stop card (K11 minimal): one plain-language headline per failure
 *  kind, the raw technical detail folded, and at most two exits — retry (same
 *  stage) and "check in detail mode". The 'design' kind adds a primary third
 *  exit: the same one-click "ask AI to fix" the detail tier has. The full
 *  error-family → plain-question translation table is K11 proper (a later
 *  task). */
interface StopCard {
  kind: 'materialize' | 'attach' | 'ingest' | 'design' | 'files' | 'interrupted'
  detail: string
  /** Present → "もう一度試す" re-runs the chain from this stage. */
  retryFrom?: PipeStage
  /** 'design' kind only: the failure lines (trap details + repair recipes +
   *  warnings + validation/mapping issues) handed verbatim to the one-click
   *  AI fix — mirrors the detail tier's composeFixComment. */
  fixLines?: string[]
}

// Extension → kind. Tabular comes from the shared TABULAR_ACCEPT constant so a
// later extension of that list (e.g. .xlsx) is picked up here automatically.
const TABULAR_EXTS = TABULAR_ACCEPT.split(',')
const JSON_EXTS = ['.json', '.geojson']
const DOCUMENT_EXTS = ['.xml', '.docx', '.pdf']
const DROP_ACCEPT = [...TABULAR_EXTS, ...JSON_EXTS, ...DOCUMENT_EXTS].join(',')

// Columns that look like a per-file serial ID (the Q2 trigger).
const ID_COLUMN_RE = /(^|[_-])(id|no|code)$/i

const PREVIEW_BYTES = 2048
const PREVIEW_ROWS = 5

function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

function kindOf(name: string): KantanKind | null {
  const ext = extOf(name)
  if (TABULAR_EXTS.includes(ext)) return 'tabular'
  if (JSON_EXTS.includes(ext)) return 'json'
  if (DOCUMENT_EXTS.includes(ext)) return 'document'
  return null
}

// Light-weight row split for the on-screen preview ONLY (the real read lives
// server-side): canonical delimiter tokens + minimal double-quote handling.
function splitRow(line: string, delimiter: string): string[] {
  if (delimiter === 'whitespace') return line.trim().split(/\s+/)
  const cells: string[] = []
  let cur = ''
  let quoted = false
  for (const ch of line) {
    if (ch === '"') {
      quoted = !quoted
      continue
    }
    if (ch === delimiter && !quoted) {
      cells.push(cur)
      cur = ''
      continue
    }
    cur += ch
  }
  cells.push(cur)
  return cells
}

/** First-rows preview of one dropped file. `header === null` ⇒ no client-side
 *  parse (json / xlsx / unreadable) — the UI shows a file-name card instead. */
interface PreviewCard {
  name: string
  header: string[] | null
  rows: string[][]
}

async function buildPreviews(files: File[], inspect: InspectResult): Promise<PreviewCard[]> {
  const out: PreviewCard[] = []
  for (let i = 0; i < files.length; i++) {
    const file = files[i]
    const ext = extOf(file.name)
    if (kindOf(file.name) !== 'tabular' || ext === '.xlsx') {
      out.push({ name: file.name, header: null, rows: [] })
      continue
    }
    // Canonical (slugged) source names come back in upload order, so zip by
    // index; fall back to the raw file name when the counts don't line up.
    const canonical =
      inspect.sourceNames.length === files.length ? inspect.sourceNames[i] : file.name
    const dialect = inspect.dialects[canonical]
    try {
      const text = await file.slice(0, PREVIEW_BYTES).text()
      let lines = text.split(/\r\n|\r|\n/)
      if (file.size > PREVIEW_BYTES) lines = lines.slice(0, -1) // drop the cut-off tail
      lines = lines.slice(dialect?.skip_rows ?? 0).filter((l) => l.trim() !== '')
      const delim = dialect?.delimiter ?? (ext === '.tsv' ? '\t' : ',')
      const cells = lines.slice(0, PREVIEW_ROWS + 1).map((l) => splitRow(l, delim))
      const [header, ...rows] = cells
      out.push({ name: file.name, header: header ?? null, rows })
    } catch {
      out.push({ name: file.name, header: null, rows: [] }) // preview is enrichment
    }
  }
  return out
}

/** Up to 3 real example values per column, from the S2 client-side preview —
 *  the S6 column table shows them as "実データの例". Serializable (unlike the
 *  File objects), so they survive a reload. First file wins on a name clash. */
function deriveColumnSamples(cards: PreviewCard[]): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const card of cards) {
    if (!card.header) continue
    card.header.forEach((col, ci) => {
      if (out[col]) return
      const vals = card.rows
        .map((r) => r[ci] ?? '')
        .filter((v) => v.trim() !== '')
        .slice(0, 3)
      if (vals.length > 0) out[col] = vals
    })
  }
  return out
}

// ---------------------------------------------------------------------------
// Persistence (best-effort: File objects can't be serialized)
// ---------------------------------------------------------------------------

interface KantanSnapshot {
  step: KzStep
  /** Source kind in the detail tier's vocabulary ('csv' | 'json'). */
  kind: 'csv' | 'json' | null
  q1: Q1Answer | null
  q2: Q2Answer | null
  dialectOverrides: Record<string, SourceDialect>
  skeleton: MappingSkeleton | null
  annotations: SkeletonAnnotations | null
  inspectionMd: string
  proposal: string
  // S5/S6 (all serializable) — lets a reload land back on the auto chain or
  // the column-meaning review instead of the drop zone.
  datasetId: string | null
  datasetName: string | null
  sourceAttached: boolean
  autoFixed: boolean
  confirmed: boolean
  columnSamples: Record<string, string[]>
}

function loadSnapshot(): Partial<KantanSnapshot> {
  try {
    return JSON.parse(sessionStorage.getItem(KZ_STORAGE) ?? '{}') as Partial<KantanSnapshot>
  } catch {
    return {}
  }
}

// In-flight continue job — same key + shape the detail tier persists, so the
// SSE replay recovery works identically (the tiers never mount together).
function loadSavedJob(): { jobId: string; kind: string } | null {
  try {
    const raw = sessionStorage.getItem(JOB_STORAGE)
    return raw ? (JSON.parse(raw) as { jobId: string; kind: string }) : null
  } catch {
    return null
  }
}
function saveJob(jobId: string) {
  try {
    sessionStorage.setItem(JOB_STORAGE, JSON.stringify({ jobId, kind: 'propose' }))
  } catch {
    /* non-fatal */
  }
}
function clearJob() {
  sessionStorage.removeItem(JOB_STORAGE)
}

export function KantanWizard({
  onBusyChange,
  onHandoffToDetail,
  onOpenDataset,
}: {
  /** Reports whether a job is in flight (the tier toggle locks while true). */
  onBusyChange: (busy: boolean) => void
  /** Called when the user opens the finished design in the detail tier. */
  onHandoffToDetail: () => void
  /** Opens the catalog detail for a dataset (the S6 "公開へ" exit). */
  onOpenDataset?: (id: string) => void
}) {
  const { t, i18n } = useTranslation()
  const { isReady, getActiveCredentials, openSettings } = useLlmSettings()

  const [snap] = useState(loadSnapshot)
  // Restore priority: S5/S6 survive on their persisted dataset id (an S5
  // restore additionally needs the proposal — the chain restarts from it); a
  // continue job that survived a reload keeps S4 alive; otherwise every restore
  // lands on S1 (files are gone) — the skeleton, if any, is kept so a re-drop
  // of the same files resumes at the gate.
  const [step, setStep] = useState<KzStep>(() => {
    if (snap.step === 6 && snap.datasetId) return 6
    if (snap.step === 5 && snap.datasetId && snap.proposal) return 5
    return snap.skeleton && loadSavedJob()?.kind === 'propose' ? 4 : 1
  })
  const [files, setFiles] = useState<File[]>([])
  const [kind, setKind] = useState<KantanKind | null>(
    snap.kind === 'json' ? 'json' : snap.kind === 'csv' ? 'tabular' : null,
  )
  const [pickError, setPickError] = useState('')

  // S2: inspection + previews + the two questions.
  const [inspecting, setInspecting] = useState(false)
  const [inspectErr, setInspectErr] = useState('')
  const [inspectionMd, setInspectionMd] = useState(snap.inspectionMd ?? '')
  const [inspection, setInspection] = useState<InspectResult | null>(null)
  const [previews, setPreviews] = useState<PreviewCard[]>([])
  const [q1, setQ1] = useState<Q1Answer | null>(snap.q1 ?? null)
  const [q2, setQ2] = useState<Q2Answer | null>(snap.q2 ?? null)
  const [dialectOverrides, setDialectOverrides] = useState<Record<string, SourceDialect>>(
    snap.dialectOverrides ?? {},
  )

  // S3/S4: staged skeleton → gate → continue.
  const [skeleton, setSkeleton] = useState<MappingSkeleton | null>(snap.skeleton ?? null)
  const [annotations, setAnnotations] = useState<SkeletonAnnotations | null>(
    snap.annotations ?? null,
  )
  const [annotationsBusy, setAnnotationsBusy] = useState(false)
  const [skeletonBusy, setSkeletonBusy] = useState(false)
  const [continuing, setContinuing] = useState(false)
  const [status, setStatus] = useState('')
  const [lastPulseAt, setLastPulseAt] = useState<number | null>(null)
  const [jobNotice, setJobNotice] = useState('')
  const [errMsg, setErrMsg] = useState('')
  const [proposal, setProposal] = useState(snap.proposal ?? '')
  const jobRef = useRef<JobHandle | null>(null)
  const revalidateTimer = useRef<number | null>(null)

  // S5: the automatic save → source persist → draft ingest chain (ADR K3).
  const [kzDatasetId, setKzDatasetId] = useState<string | null>(snap.datasetId ?? null)
  const [kzDatasetName, setKzDatasetName] = useState<string | null>(snap.datasetName ?? null)
  const [sourceAttached, setSourceAttached] = useState<boolean>(snap.sourceAttached ?? false)
  const [autoFixed, setAutoFixed] = useState<boolean>(snap.autoFixed ?? false)
  const [pipeBusy, setPipeBusy] = useState(false)
  const [pipePhase, setPipePhase] = useState<'save' | 'ingest' | null>(null)
  const [ingestProgress, setIngestProgress] = useState<IngestProgress | null>(null)
  const [ingestHandle, setIngestHandle] = useState<IngestJobHandle | null>(null)
  // An S5 restore with NO still-running ingest job lands on a "resume from
  // here" stop card instead of silently re-POSTing a server job (decided at
  // init — the live-job resume itself is the mount effect below).
  const [stop, setStop] = useState<StopCard | null>(() => {
    if (snap.step !== 5 || !snap.datasetId || !snap.proposal) return null
    const saved = loadIngestJob()
    if (saved && saved.kind === 'ingest' && saved.datasetId === snap.datasetId) return null
    return snap.sourceAttached
      ? { kind: 'interrupted', detail: '', retryFrom: 'ingest' }
      : { kind: 'files', detail: '' }
  })

  // S6: the column-meaning review (human gate ②).
  const [columnSamples, setColumnSamples] = useState<Record<string, string[]>>(
    snap.columnSamples ?? {},
  )
  const [rules, setRules] = useState<DatasetRules | null>(null)
  const [stats, setStats] = useState<DraftStats | null>(null)
  const [s6Loading, setS6Loading] = useState(false)
  const [s6Err, setS6Err] = useState('')
  const [note, setNote] = useState('')
  // 'note' = the S6 free-text reflect; 'fix' = the S5 design-stop AI fix. Both
  // ride the SAME refine → re-materialize chain; the flag only picks the
  // progress label and where a failure lands.
  const [refining, setRefining] = useState<false | 'note' | 'fix'>(false)
  const [refineErr, setRefineErr] = useState('')
  // S5 design-stop AI fix: its own error slot + attempt counter (AI 修正 n 回目).
  const [fixErr, setFixErr] = useState('')
  const [aiFixCount, setAiFixCount] = useState(0)
  const [confirmed, setConfirmed] = useState<boolean>(snap.confirmed ?? false)

  const busy = inspecting || skeletonBusy || continuing || pipeBusy || refining !== false
  useEffect(() => {
    onBusyChange(busy)
  }, [busy, onBusyChange])

  // Persist the (serializable) wizard state so a tab switch / reload is
  // recoverable. Files are not persistable — restore is best-effort by design.
  useEffect(() => {
    const snapshot: KantanSnapshot = {
      step,
      kind: kind === 'json' ? 'json' : kind === 'tabular' ? 'csv' : null,
      q1,
      q2,
      dialectOverrides,
      skeleton,
      annotations,
      inspectionMd,
      proposal,
      datasetId: kzDatasetId,
      datasetName: kzDatasetName,
      sourceAttached,
      autoFixed,
      confirmed,
      columnSamples,
    }
    try {
      sessionStorage.setItem(KZ_STORAGE, JSON.stringify(snapshot))
    } catch {
      /* sessionStorage may be unavailable — non-fatal */
    }
  }, [
    step,
    kind,
    q1,
    q2,
    dialectOverrides,
    skeleton,
    annotations,
    inspectionMd,
    proposal,
    kzDatasetId,
    kzDatasetName,
    sourceAttached,
    autoFixed,
    confirmed,
    columnSamples,
  ])

  // Hand the finished design to the detail tier: a WB_STORAGE-compatible
  // snapshot (mirrors WorkbenchView's WorkbenchSnapshot shape) opening on the
  // review step. Written as soon as the proposal exists, so "詳細モードで確認"
  // works from every later screen — including the S5 stop cards (K11).
  useEffect(() => {
    if (!proposal) return
    const detailSnapshot = {
      mode: 'new',
      step: 2,
      source: kind === 'json' ? 'json' : 'csv',
      fk: '',
      markdown: inspectionMd,
      domainFree: '',
      // Q2 said the ID recurs elsewhere (or unknown = safe side): the detail
      // tier shows the same composite-key hint pre-ticked.
      presetIds: q2 === 'elsewhere' || q2 === 'unknown' ? ['composite-key'] : [],
      proposal,
      materialized: null,
      dialectOverrides,
      stagedSkeleton: null,
      stagedAnnotations: null,
    }
    try {
      sessionStorage.setItem(WB_STORAGE, JSON.stringify(detailSnapshot))
    } catch {
      /* non-fatal */
    }
  }, [proposal, inspectionMd, kind, q2, dialectOverrides])

  // Resume an in-flight continue job after a reload (same SSE replay recovery
  // as the detail tier; the tiers never mount together, so no double-resume).
  // Skeleton jobs are deliberately NOT persisted — a reload just re-runs S3.
  useEffect(() => {
    const job = loadSavedJob()
    if (!job || job.kind !== 'propose') return // 'refine' belongs to the detail tier
    const handle = resumeJob(job.jobId, {
      onPulse: () => {
        setContinuing(true)
        setLastPulseAt(Date.now())
      },
      onStatus: (m) => {
        setContinuing(true)
        setStatus(plainStatus(m))
      },
      onDone: (result) => {
        const r = result as ProposeResult
        setInspectionMd(r.inspection_md)
        setSkeleton(null)
        setAnnotations(null)
        setStatus('')
        setContinuing(false)
        clearJob()
        setAutoFixed((r.autocorrect?.rounds?.length ?? 0) > 0)
        setProposal(r.proposal_md)
        // ADR K3: continue straight into the auto chain (after a reload the
        // File objects are gone — the chain stops at the re-drop card then).
        void runPipeline('materialize', r.proposal_md)
      },
      onError: (message) => {
        setErrMsg(t('kantan:job.resumedFailed', { message }))
        setStatus('')
        setContinuing(false)
        clearJob()
        setStep(1) // files are gone after a reload — restart from the drop zone
      },
      onCancelled: () => {
        setJobNotice(t('workbench:job.cancelled'))
        setStatus('')
        setContinuing(false)
        clearJob()
        setStep(1)
      },
    })
    jobRef.current = handle
    return () => handle.close()
    // Mount-only: resume whatever job was persisted before this mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // S5/S6 reload recovery (best-effort, ADR K3/K11): a still-running draft
  // ingest is re-attached through the same SSE replay the catalog uses
  // (StrictMode-safe — re-subscribing twice is harmless, unlike re-POSTing;
  // the no-live-job case became a stop card at state init above). S6 just
  // re-fetches its read-only data.
  useEffect(() => {
    if (!kzDatasetId) return
    if (step === 6 && !confirmed) {
      void loadS6(kzDatasetId)
      return
    }
    if (step !== 5) return
    const saved = loadIngestJob()
    if (!saved || saved.kind !== 'ingest' || saved.datasetId !== kzDatasetId) return
    const handle = resumeIngestJob(saved.jobId, kzDatasetId, setIngestProgress, () =>
      setLastPulseAt(Date.now()),
    )
    void trackIngest(handle, kzDatasetId)
    return () => handle.close() // release the stream; the server job keeps running
    // Mount-only: recover whatever the snapshot says was in flight.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Map raw SSE statuses to plain language — never surface backend phase
  // strings in this tier (unknown phases read as "解析中…").
  function plainStatus(m: string): string {
    if (!m || m === 'done') return ''
    if (/start/i.test(m)) return t('kantan:job.preparing')
    return t('kantan:job.analyzing')
  }

  function errText(e: unknown): string {
    return e instanceof Error ? e.message : String(e)
  }

  // The one domain hint this tier can produce: Q2 said the ID column recurs
  // outside this file (or the user doesn't know = safe side) → composite key.
  function composedDomain(): string {
    if (q2 === 'elsewhere' || q2 === 'unknown') {
      return PRESET_HINTS.find((h) => h.id === 'composite-key')?.text ?? ''
    }
    return ''
  }

  // ---- S1: files in ---------------------------------------------------------

  function onFilesChosen(list: FileList | File[] | null) {
    const arr = Array.from(list ?? [])
    if (arr.length === 0) return
    const kinds = new Set(arr.map((f) => kindOf(f.name)))
    if (kinds.has(null)) {
      setPickError(t('kantan:s1.unsupported'))
      return
    }
    if (kinds.size > 1) {
      setPickError(t('kantan:s1.mixed'))
      return
    }
    const k = [...kinds][0] as KantanKind
    setPickError('')
    setErrMsg('')
    setJobNotice('')
    setInspectErr('')
    setFiles(arr)

    if (k === 'document') {
      // Documents need no AI design — the existing panel handles the whole
      // upload → ingest → publish chain; it renders inline below the drop zone.
      setKind(k)
      return
    }

    // Best-effort resume: a restored skeleton + a re-drop of the same-kind
    // files goes straight back to the gate (with a fresh evidence re-check).
    if (skeleton && k === kind) {
      setStep(4)
      setAnnotationsBusy(true)
      validateSkeleton(arr, skeleton, dialectOverrides)
        .then(setAnnotations)
        .catch(() => {
          /* evidence is enrichment */
        })
        .finally(() => setAnnotationsBusy(false))
      return
    }

    // A fresh (or different) file set: drop everything downstream and inspect.
    setKind(k)
    setQ1(null)
    setQ2(null)
    setDialectOverrides({})
    setSkeleton(null)
    setAnnotations(null)
    setProposal('')
    setInspectionMd('')
    resetPipelineState()
    void runInspect(arr)
  }

  /** Drop every S5/S6 leftover when a fresh design starts. */
  function resetPipelineState() {
    setKzDatasetId(null)
    setKzDatasetName(null)
    setSourceAttached(false)
    setAutoFixed(false)
    setConfirmed(false)
    setColumnSamples({})
    setRules(null)
    setStats(null)
    setStop(null)
    setNote('')
    setS6Err('')
    setRefineErr('')
    setFixErr('')
    setAiFixCount(0)
    setIngestProgress(null)
  }

  async function runInspect(arr: File[]) {
    setInspecting(true)
    setInspectErr('')
    try {
      const result = await inspectCsvs(arr, [])
      setInspection(result)
      setInspectionMd(result.markdown)
      const cards = await buildPreviews(arr, result)
      setPreviews(cards)
      setColumnSamples(deriveColumnSamples(cards))
      setStep(2)
    } catch (e) {
      setInspectErr(e instanceof Error ? e.message : String(e))
    } finally {
      setInspecting(false)
    }
  }

  // ---- S2: the two questions -----------------------------------------------

  // Q1 applies when detection found preamble lines before the table.
  const preambleSources = Object.entries(inspection?.dialects ?? {}).filter(
    ([, d]) => d.skip_rows > 0,
  )
  const q1Needed = preambleSources.length > 0
  const preambleRowCount = preambleSources.reduce((acc, [, d]) => acc + d.skip_rows, 0)

  // Q2 applies when a header column looks like a serial-number ID.
  const idColumn =
    previews.flatMap((p) => p.header ?? []).find((c) => ID_COLUMN_RE.test(c.trim())) ?? null
  const q2Needed = idColumn !== null

  const questionsAnswered = (!q1Needed || q1 !== null) && (!q2Needed || q2 !== null)

  function answerQ1(a: Q1Answer) {
    setQ1(a)
    // The answer becomes per-source read overrides: keep = broadcast the
    // preamble metadata onto every row; drop = table only.
    setDialectOverrides(() => {
      const next: Record<string, SourceDialect> = {}
      for (const [name, det] of preambleSources) {
        next[name] = {
          encoding: det.encoding,
          delimiter: det.delimiter,
          collapse: det.collapse,
          skip_rows: det.skip_rows,
          preamble: a === 'keep' ? 'keyvalue' : 'drop',
        }
      }
      return next
    })
  }

  function onProceed() {
    if (!isReady || files.length === 0) return
    setStep(3)
    void runSkeleton()
  }

  function backToPick() {
    setFiles([])
    setKind(null)
    setPreviews([])
    setInspection(null)
    setSkeleton(null)
    setAnnotations(null)
    setQ1(null)
    setQ2(null)
    setDialectOverrides({})
    setErrMsg('')
    setJobNotice('')
    resetPipelineState()
    setStep(1)
  }

  // ---- S3: staged skeleton propose (always the staged path, never one-shot) --

  async function runSkeleton() {
    setErrMsg('')
    setJobNotice('')
    setStatus('')
    setSkeletonBusy(true)
    setLastPulseAt(null)
    jobRef.current?.close()
    try {
      jobRef.current = await proposeSkeleton(
        files,
        composedDomain(),
        [],
        getActiveCredentials(),
        {
          onStatus: (m) => setStatus(plainStatus(m)),
          onPulse: () => setLastPulseAt(Date.now()),
          onDone: (result) => {
            setSkeleton(result.skeleton)
            setAnnotations(result.annotations ?? null)
            setInspectionMd(result.inspection_md)
            setStatus('')
            setSkeletonBusy(false)
            setStep(4)
          },
          onError: (m) => {
            setErrMsg(m)
            setStatus('')
            setSkeletonBusy(false)
          },
          onCancelled: () => {
            setJobNotice(t('workbench:job.cancelled'))
            setStatus('')
            setSkeletonBusy(false)
            setStep(2)
          },
        },
        i18n.language,
        dialectOverrides,
      )
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e))
      setSkeletonBusy(false)
    }
  }

  // ---- S4: the row-counting gate → continue ----------------------------------

  // A human edit re-checks the evidence server-side (no LLM) after a short
  // debounce — same contract as the detail tier's onSkeletonEdited.
  function onSkeletonEdited(edited: MappingSkeleton) {
    setSkeleton(edited)
    if (revalidateTimer.current !== null) window.clearTimeout(revalidateTimer.current)
    if (files.length === 0) return // nothing to check against (gate shows a hint)
    revalidateTimer.current = window.setTimeout(async () => {
      setAnnotationsBusy(true)
      try {
        setAnnotations(await validateSkeleton(files, edited, dialectOverrides))
      } catch {
        // Evidence is enrichment — a failed re-check never blocks editing.
      } finally {
        setAnnotationsBusy(false)
      }
    }, 700)
  }

  async function runContinue() {
    if (!skeleton) return
    if (files.length === 0) {
      setErrMsg(t('kantan:s4.needFiles'))
      return
    }
    setErrMsg('')
    setJobNotice('')
    setStatus('')
    setContinuing(true)
    setLastPulseAt(null)
    jobRef.current?.close()
    try {
      jobRef.current = await proposeContinue(
        files,
        skeleton,
        composedDomain(),
        [],
        getActiveCredentials(),
        {
          onStart: (jobId) => saveJob(jobId),
          onStatus: (m) => setStatus(plainStatus(m)),
          onPulse: () => setLastPulseAt(Date.now()),
          onDone: (result) => {
            setInspectionMd(result.inspection_md)
            setSkeleton(null)
            setAnnotations(null)
            setStatus('')
            setContinuing(false)
            clearJob()
            setAutoFixed((result.autocorrect?.rounds?.length ?? 0) > 0)
            setProposal(result.proposal_md)
            // ADR K3: no approval button between "design done" and the draft —
            // the chain continues automatically into S5.
            void runPipeline('materialize', result.proposal_md)
          },
          onError: (m) => {
            setErrMsg(m)
            setStatus('')
            setContinuing(false)
            clearJob()
          },
          onCancelled: () => {
            setJobNotice(t('workbench:job.cancelled'))
            setStatus('')
            setContinuing(false)
            clearJob()
          },
        },
        i18n.language,
        undefined, // autocorrect: server default
        dialectOverrides,
      )
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e))
      setContinuing(false)
    }
  }

  // ---- S5: the automatic save → draft-ingest chain (ADR K3) ------------------
  // No approval button here BY DESIGN (this PR revises phase5 D1): execution
  // safety is machine-guaranteed — unsafe or invalid RML is refused with a 422
  // hard gate BEFORE any job runs — and un-promoted data is invisible to Ask
  // (draft isolation + promoted flag). The human gates that remain are the two
  // only a human can answer: S4 (row counting) and S6 (column meanings).

  async function runPipeline(from: PipeStage, proposalMdArg?: string, filesArg?: File[]) {
    const md = proposalMdArg ?? proposal
    if (!md || pipeBusy) return
    const fs = filesArg ?? files
    setStop(null)
    setFixErr('')
    setJobNotice('')
    setPipeBusy(true)
    setStep(5)
    let datasetId = kzDatasetId
    let attached = sourceAttached
    try {
      // 1) Save the design: split the reviewed Markdown into the artifact
      //    bundle + run the trap validator (no LLM — seconds). A retry targets
      //    the SAME adopted record, so it never mints a duplicate dataset.
      if (from === 'materialize') {
        setPipePhase('save')
        let result: MaterializeResult
        try {
          try {
            result = await materializeSchema(md, kzDatasetName ?? 'dataset', datasetId ?? undefined)
          } catch (e) {
            // The adopted record vanished (deleted in the catalog meanwhile) —
            // recreate once instead of dead-ending the chain on a stale id.
            if (!datasetId || !(e instanceof ApiError) || e.status !== 404) throw e
            datasetId = null
            attached = false
            setKzDatasetId(null)
            setSourceAttached(false)
            result = await materializeSchema(md, 'dataset')
          }
        } catch (e) {
          setStop({ kind: 'materialize', detail: errText(e), retryFrom: 'materialize' })
          return
        }
        if (result.dataset?.id && result.dataset.id !== datasetId) {
          // Adopt the minted record — its source dir starts empty.
          datasetId = result.dataset.id
          attached = false
          setKzDatasetId(datasetId)
          setKzDatasetName(result.dataset.name ?? null)
          setSourceAttached(false)
        }
        if (!result.complete || result.exit_code !== 0) {
          // Problems the self-correction could not clear (truncated output /
          // failing traps): a human decision now — the card's PRIMARY exit is
          // the same one-click AI fix the detail tier has. Each failing trap
          // ships its deterministic repair recipe (`fix`): hand it to the AI
          // grouped with its symptom, like the detail tier's composeFixComment
          // (symptom-only comments loop weak models forever). The api merges
          // mapping_ir_issues into `validation_issues`, so appending that list
          // covers the mapping-spec compile problems too.
          const fails = result.traps.filter((tr) => tr.status === 'fail')
          const lines = [
            ...(result.complete ? [] : ['incomplete design output (truncated)']),
            ...fails.map((tr) => {
              const head = `${tr.id} ${tr.name}: ${tr.detail}`
              return tr.fix ? `${head}\n  ↳ ${tr.fix.split('\n').join('\n    ')}` : head
            }),
            ...result.warnings,
            ...(result.validation_issues ?? []),
          ]
          setStop({ kind: 'design', detail: lines.join('\n'), fixLines: lines })
          return
        }
      }
      if (!datasetId) {
        setStop({ kind: 'materialize', detail: 'dataset id missing', retryFrom: 'materialize' })
        return
      }
      // 2) Persist the S1 files as the dataset's source, so this ingest — and
      //    every later re-ingest (S6 refine loop, catalog) — needs no re-attach.
      if (!attached) {
        if (fs.length === 0) {
          setStop({ kind: 'files', detail: '' })
          return
        }
        setPipePhase('save')
        try {
          await attachSource(datasetId, fs)
          setSourceAttached(true)
        } catch (e) {
          setStop({ kind: 'attach', detail: errText(e), retryFrom: 'attach' })
          return
        }
      }
      // 3) Draft ingest — the same background job + SSE progress machinery the
      //    catalog uses. The server re-validates the design (422 hard gate)
      //    before any job runs; the draft graph stays out of the Ask scope.
      setPipePhase('ingest')
      setIngestProgress(null)
      let handle: IngestJobHandle
      try {
        handle = await startIngestJob(datasetId, [], setIngestProgress, () =>
          setLastPulseAt(Date.now()),
        )
      } catch (e) {
        if (e instanceof IngestValidationError) {
          setStop({ kind: 'design', detail: e.issues.join('\n'), fixLines: e.issues })
        } else {
          setStop({ kind: 'ingest', detail: errText(e), retryFrom: 'ingest' })
        }
        return
      }
      await trackIngest(handle, datasetId)
    } finally {
      setPipeBusy(false)
      setPipePhase(null)
    }
  }

  // Await one draft-ingest job to its end and settle the wizard — shared by the
  // fresh-start and the reload-recovery paths (same split as the catalog).
  async function trackIngest(handle: IngestJobHandle, datasetId: string) {
    saveIngestJob({ jobId: handle.jobId, datasetId, kind: 'ingest' })
    setIngestHandle(handle)
    setPipeBusy(true)
    setPipePhase('ingest')
    try {
      await handle.result
      setAiFixCount(0) // the fix loop (if any) landed — reset the counter
      setStep(6)
      void loadS6(datasetId)
    } catch (e) {
      if (e instanceof IngestCancelledError || e instanceof StaleIngestJobError) {
        // Clean stop (user cancel) or a job id re-minted by an api restart —
        // nothing was committed; offer a clean resume of the same stage.
        setStop({ kind: 'interrupted', detail: '', retryFrom: 'ingest' })
      } else if (e instanceof IngestValidationError) {
        setStop({ kind: 'design', detail: e.issues.join('\n'), fixLines: e.issues })
      } else {
        setStop({ kind: 'ingest', detail: errText(e), retryFrom: 'ingest' })
      }
    } finally {
      clearIngestJob(handle.jobId)
      setIngestHandle(null)
      setIngestProgress(null)
      setPipeBusy(false)
      setPipePhase(null)
    }
  }

  // ---- S6: the column-meaning review (human gate ②) --------------------------

  async function loadS6(datasetId: string) {
    setS6Loading(true)
    setS6Err('')
    try {
      const [r, s] = await Promise.all([
        getDatasetRules(datasetId),
        fetchDraftStats(datasetId).catch(() => null), // the count card is enrichment
      ])
      setRules(r)
      setStats(s)
    } catch (e) {
      setS6Err(errText(e))
    } finally {
      setS6Loading(false)
    }
  }

  // The shared refine → re-materialize chain: the S6 note ("AI に反映して作り
  // 直す") and the S5 design-stop AI fix both ride it — when the refined design
  // lands, the SAME auto chain re-runs (the source is already persisted, so the
  // re-ingest needs no re-attach). `restoreStop` puts the original stop card
  // back when a 'fix' attempt itself fails or is cancelled.
  async function startRefineChain(comments: string[], mode: 'note' | 'fix', restoreStop?: StopCard) {
    if (!proposal || refining) return
    setRefineErr('')
    setFixErr('')
    setJobNotice('')
    setStatus('')
    setRefining(mode)
    setLastPulseAt(null)
    jobRef.current?.close()
    const fail = (message: string) => {
      if (mode === 'fix') {
        setFixErr(message)
        if (restoreStop) setStop(restoreStop) // back to the same stop card
      } else {
        setRefineErr(message)
      }
      setStatus('')
      setRefining(false)
    }
    try {
      // Deliberately NOT persisted to JOB_STORAGE: this tier only resumes
      // 'propose' jobs, and a saved 'refine' would wedge the tier toggle —
      // after a reload the user simply sends the note / clicks the fix again.
      jobRef.current = await refineSchema(
        proposal,
        comments,
        getActiveCredentials(),
        {
          onStatus: (m) => setStatus(plainStatus(m)),
          onPulse: () => setLastPulseAt(Date.now()),
          onDone: (result) => {
            setStatus('')
            setRefining(false)
            setNote('')
            setRules(null)
            setStats(null)
            setProposal(result.refined_md)
            void runPipeline('materialize', result.refined_md)
          },
          onError: fail,
          onCancelled: () => {
            setJobNotice(t('workbench:job.cancelled'))
            if (mode === 'fix' && restoreStop) setStop(restoreStop)
            setStatus('')
            setRefining(false)
          },
        },
        i18n.language,
      )
    } catch (e) {
      fail(errText(e))
    }
  }

  // "AI に反映して作り直す" (S6): the one free-text note rides a structured
  // refine comment through the shared chain → S6 again.
  async function runRefine() {
    const trimmed = note.trim()
    if (!trimmed) return
    await startRefineChain([t('kantan:s6.refineWrap', { note: trimmed })], 'note')
  }

  // "AI に直してもらう" (S5 design stop): the same one-click fix the detail
  // tier has — the card's failure lines (trap details + repair recipes +
  // warnings + validation/mapping issues) become the corrective refine
  // comment, then the refined design re-runs the auto chain from materialize.
  function runAiFix() {
    if (!stop || stop.kind !== 'design' || !proposal || pipeBusy) return
    const card = stop
    const lines = card.fixLines?.length ? card.fixLines : card.detail ? [card.detail] : []
    const comment = `${t('workbench:fix.commentIntro')}\n${lines.map((l) => `- ${l}`).join('\n')}`
    setAiFixCount((c) => c + 1)
    setStop(null)
    void startRefineChain([comment], 'fix', card)
  }

  function openDetail() {
    // The WB_STORAGE handoff snapshot is already written (effect above); drop
    // the kantan snapshot so a later return to this tier starts fresh.
    try {
      sessionStorage.removeItem(KZ_STORAGE)
    } catch {
      /* non-fatal */
    }
    onHandoffToDetail()
  }

  // The S5 "files missing" card (a reload dropped the File objects): re-dropping
  // the same files resumes the chain from the source-persist step.
  function onStopFilesDropped(list: FileList | null) {
    const arr = Array.from(list ?? [])
    if (arr.length === 0) return
    const kinds = new Set(arr.map((f) => kindOf(f.name)))
    if (kinds.has(null)) {
      setPickError(t('kantan:s1.unsupported'))
      return
    }
    if (kinds.size > 1) {
      setPickError(t('kantan:s1.mixed'))
      return
    }
    setPickError('')
    setFiles(arr)
    void runPipeline('attach', undefined, arr)
  }

  function openDatasetFromDone() {
    if (!kzDatasetId) return
    try {
      sessionStorage.removeItem(KZ_STORAGE) // the wizard's job is done
    } catch {
      /* non-fatal */
    }
    onOpenDataset?.(kzDatasetId)
  }

  // Completion-card escape hatch: begin a brand-new design without leaving the
  // wizard (the registered draft stays in the catalog untouched).
  function startFresh() {
    try {
      sessionStorage.removeItem(KZ_STORAGE)
    } catch {
      /* non-fatal */
    }
    setFiles([])
    setKind(null)
    setPreviews([])
    setInspection(null)
    setInspectionMd('')
    setSkeleton(null)
    setAnnotations(null)
    setQ1(null)
    setQ2(null)
    setDialectOverrides({})
    setProposal('')
    setErrMsg('')
    setJobNotice('')
    resetPipelineState()
    setStep(1)
  }

  // ---- render -----------------------------------------------------------------

  const recipePos: 1 | 2 | 3 = step <= 2 ? 1 : step === 3 ? 2 : 3
  const resumeAvailable = !!skeleton && files.length === 0 && !proposal && step === 1
  const showS5 = pipeBusy || refining !== false || step === 5

  const up = ingestProgress
  const uploadPct =
    up?.phase === 'upload' && up.total ? Math.floor((100 * (up.done ?? 0)) / up.total) : null

  const s6Maps = rules?.maps ?? []
  const multiMap = s6Maps.length > 1
  const linkRows = s6Maps.flatMap((m) =>
    m.properties.filter((p) => p.kind !== 'reference').map((p) => ({ map: m, prop: p })),
  )
  const totalSourceRows = Object.values(stats?.source_rows ?? {}).reduce((a, b) => a + b, 0)

  function classLabel(iri: string): string {
    return rules?.labels?.[iri] ?? localName(iri)
  }

  function mapCaption(m: RuleMap): string {
    const iri = m.subject.class_iris?.[0]
    if (iri) return classLabel(iri)
    return m.subject.classes?.[0] ?? m.id
  }

  function otherKindKey(k: RuleProperty['kind']): string {
    return k === 'template' || k === 'constant' || k === 'join' || k === 'function' ? k : 'other'
  }

  return (
    <div className="kz-wizard">
      <RecipeCard current={recipePos} currentDone={confirmed && step === 6} />

      {confirmed && step === 6 ? (
        <section className="kz-card kz-done">
          <h3 className="kz-done-title">✓ {t('kantan:s6.doneTitle')}</h3>
          <p className="kz-note">{t('kantan:s6.doneBody')}</p>
          <div className="kz-actions">
            {kzDatasetId && onOpenDataset && (
              <button type="button" onClick={openDatasetFromDone}>
                {t('kantan:s6.openDataset')}
              </button>
            )}
            <button type="button" className="btn btn--ghost btn--sm" onClick={startFresh}>
              {t('kantan:s6.startNew')}
            </button>
          </div>
          <p className="kz-note">{t('kantan:s6.prepNote')}</p>
          <p className="kz-note">{t('kantan:s6.nextSteps')}</p>
        </section>
      ) : stop ? (
        <section className="kz-card kz-stop" role="alert">
          <h3 className="kz-title">{t(`kantan:s5.stop.${stop.kind}`)}</h3>
          {stop.kind === 'design' && <p className="kz-note">{t('kantan:s5.stop.designBody')}</p>}
          {stop.kind === 'interrupted' && (
            <p className="kz-note">{t('kantan:s5.stop.interruptedBody')}</p>
          )}
          {stop.kind === 'files' && (
            <>
              <p className="kz-note">{t('kantan:s5.stop.filesBody')}</p>
              <DropZone onFiles={onStopFilesDropped} />
              {pickError && <p className="kz-note kz-pick-error">{pickError}</p>}
            </>
          )}
          {stop.detail && (
            <details className="kz-stop-detail">
              <summary>{t('kantan:s5.stop.detailSummary')}</summary>
              <pre className="error">{stop.detail}</pre>
            </details>
          )}
          {stop.kind === 'design' && aiFixCount > 0 && (
            <p className="kz-note">{t('kantan:s5.fix.attempted', { n: aiFixCount })}</p>
          )}
          {stop.kind === 'design' && fixErr && (
            <pre className="error">{t('kantan:s5.fix.failed', { message: fixErr })}</pre>
          )}
          <div className="kz-actions">
            {stop.kind === 'design' && (
              <button type="button" onClick={runAiFix} disabled={!isReady || !proposal}>
                {t('kantan:s5.fix.button')}
              </button>
            )}
            {stop.retryFrom && (
              <button
                type="button"
                onClick={() => {
                  if (stop.retryFrom) void runPipeline(stop.retryFrom)
                }}
              >
                {t('kantan:s5.stop.retry')}
              </button>
            )}
            <button type="button" className="btn btn--ghost" onClick={openDetail}>
              {t('kantan:s5.stop.openDetail')}
            </button>
          </div>
          {stop.kind === 'design' && !isReady && (
            <p className="kz-note">{t('kantan:s1.aiNotReady')}</p>
          )}
          {jobNotice && (
            <p className="job-cancelled-note" role="status">
              {jobNotice}
            </p>
          )}
        </section>
      ) : showS5 ? (
        <section className="kz-card">
          <h3 className="kz-title">{t('kantan:s5.title')}</h3>
          {refining ? (
            <JobProgress
              label={
                refining === 'fix'
                  ? t('kantan:s5.fix.progress', { n: aiFixCount })
                  : t('kantan:s6.reflecting')
              }
              status={status}
              lastPulseAt={lastPulseAt}
              onCancel={() => jobRef.current?.cancel() ?? Promise.resolve()}
            />
          ) : (
            <>
              <div className="kz-live" role="status" aria-live="polite">
                <p className="kz-live-line done">
                  <span className="kz-live-mark" aria-hidden="true">
                    ✓
                  </span>
                  {t('kantan:s5.meanings')}
                </p>
                {autoFixed && (
                  <p className="kz-live-line done">
                    <span className="kz-live-mark" aria-hidden="true">
                      ✓
                    </span>
                    {t('kantan:s5.quality')}
                  </p>
                )}
                <p className="kz-live-line active">
                  <span className="kz-live-mark" aria-hidden="true">
                    <span className="spinner" />
                  </span>
                  {pipePhase === 'save'
                    ? t('kantan:s5.saving')
                    : uploadPct !== null && up?.total
                      ? t('kantan:s5.ingestingCount', {
                          done: (up.done ?? 0).toLocaleString(),
                          total: up.total.toLocaleString(),
                          pct: uploadPct,
                        })
                      : t('kantan:s5.ingesting')}
                </p>
                {uploadPct !== null && (
                  <div className="ingest-progress-track">
                    <span style={{ width: `${uploadPct}%` }} />
                  </div>
                )}
              </div>
              {ingestHandle && (
                <div className="kz-actions">
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => {
                      // A failed cancel request must not surface as an
                      // unhandled rejection — the stream outcome settles the UI.
                      ingestHandle.cancel().catch(() => {})
                    }}
                  >
                    {t('workbench:job.cancel')}
                  </button>
                </div>
              )}
              <p className="kz-note">{t('kantan:s5.closeNote')}</p>
            </>
          )}
          {jobNotice && (
            <p className="job-cancelled-note" role="status">
              {jobNotice}
            </p>
          )}
        </section>
      ) : step === 6 ? (
        <section className="kz-card">
          <h3 className="kz-title">{t('kantan:s6.title')}</h3>
          <p className="kz-note">{t('kantan:s6.lead')}</p>
          {s6Loading && (
            <p className="kz-note" role="status">
              <span className="spinner" />
              {t('kantan:s6.loading')}
            </p>
          )}
          {s6Err && (
            <>
              <pre className="error">{t('kantan:s6.loadFailed', { message: s6Err })}</pre>
              <div className="kz-actions">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => {
                    if (kzDatasetId) void loadS6(kzDatasetId)
                  }}
                >
                  {t('kantan:s6.reload')}
                </button>
              </div>
            </>
          )}
          {stats && stats.classes.length > 0 && (
            <div className="kz-map-card">
              {totalSourceRows > 0 && (
                <>
                  <span className="kz-map-part">
                    {t('kantan:s6.mapRows', { rows: totalSourceRows.toLocaleString() })}
                  </span>
                  <span className="kz-map-arrow" aria-hidden="true">
                    →
                  </span>
                </>
              )}
              {stats.classes.map((c) => (
                <span key={c.iri} className="kz-map-class">
                  {t('kantan:s6.classCount', {
                    label: classLabel(c.iri),
                    n: c.n.toLocaleString(),
                  })}
                </span>
              ))}
              <span className="kz-map-note">{t('kantan:s6.mapDraftNote')}</span>
            </div>
          )}
          {s6Maps.map((m) => {
            const refs = m.properties.filter((p) => p.kind === 'reference')
            if (refs.length === 0) return null
            return (
              <div key={m.id} className="kz-cols">
                {multiMap && <div className="kz-cols-caption">{mapCaption(m)}</div>}
                <div className="kz-preview-tablewrap">
                  <table className="kz-preview-table kz-cols-table">
                    <thead>
                      <tr>
                        <th>{t('kantan:s6.colColumn')}</th>
                        <th>{t('kantan:s6.colMeaning')}</th>
                        <th>{t('kantan:s6.colUnit')}</th>
                        <th>{t('kantan:s6.colExamples')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {refs.map((p, i) => {
                        // Meaning: IR label (K8) → model.yaml label → local name.
                        // A missing label only gets the amber marker — a nudge
                        // for the eye, never a gate.
                        const meaning = p.label || rules?.labels?.[p.predicate_iri] || ''
                        const missing = !meaning
                        const samples = columnSamples[p.reference ?? ''] ?? []
                        return (
                          <tr
                            key={`${m.id}-${i}`}
                            className={missing ? 'kz-attn' : undefined}
                            title={missing ? t('kantan:s6.missingMeaning') : undefined}
                          >
                            <td className="kz-cols-name">{p.reference}</td>
                            <td>
                              {meaning || localName(p.predicate_iri || p.predicate)}
                              {missing && ' ⚠'}
                            </td>
                            <td>{p.unit ?? ''}</td>
                            <td className="kz-cols-samples">{samples.join('、')}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })}
          {linkRows.length > 0 && (
            <details className="kz-links">
              <summary>{t('kantan:s6.othersSummary', { n: linkRows.length })}</summary>
              <ul className="kz-links-list">
                {linkRows.map(({ map, prop }, i) => (
                  <li key={`${map.id}-${i}`}>
                    <code>{prop.label || rules?.labels?.[prop.predicate_iri] || prop.predicate}</code>
                    {' — '}
                    {t(`kantan:s6.otherKind.${otherKindKey(prop.kind)}`)}
                    {(prop.template || prop.constant || prop.parent_map || prop.function) && (
                      <code className="kz-links-detail">
                        {prop.template ?? prop.constant ?? prop.parent_map ?? prop.function}
                      </code>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <div className="kz-q">
            <label className="kz-q-text" htmlFor="kz-s6-note">
              {t('kantan:s6.noteLabel')}
            </label>
            <textarea
              id="kz-s6-note"
              className="kz-s6-note"
              rows={2}
              placeholder={t('kantan:s6.notePlaceholder')}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <div className="kz-actions">
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => void runRefine()}
                disabled={!note.trim() || refining !== false || !isReady}
              >
                {t('kantan:s6.reflect')}
              </button>
            </div>
            {!isReady && note.trim() !== '' && <p className="kz-note">{t('kantan:s1.aiNotReady')}</p>}
            {refineErr && (
              <pre className="error">{t('kantan:s6.reflectFailed', { message: refineErr })}</pre>
            )}
          </div>
          <div className="kz-actions">
            <button
              type="button"
              onClick={() => setConfirmed(true)}
              disabled={s6Loading || refining !== false}
            >
              {t('kantan:s6.confirm')}
            </button>
          </div>
        </section>
      ) : step === 1 ? (
        <>
          {!isReady && (
            <section className="kz-card kz-warn" role="status">
              <p className="kz-note">{t('kantan:s1.aiNotReady')}</p>
              <div className="kz-actions">
                <button type="button" className="btn btn--ghost btn--sm" onClick={openSettings}>
                  {t('kantan:s1.openSettings')}
                </button>
              </div>
            </section>
          )}
          <section className="kz-card">
            <h3 className="kz-title">{t('kantan:s1.title')}</h3>
            <DropZone onFiles={onFilesChosen} />
            <p className="kz-note">{t('kantan:s1.privacy')}</p>
            {resumeAvailable && <p className="kz-note kz-resume">{t('kantan:s1.resumeNote')}</p>}
            {pickError && <p className="kz-note kz-pick-error">{pickError}</p>}
            {inspecting && (
              <p className="kz-note" role="status">
                <span className="spinner" />
                {t('kantan:s1.reading')}
              </p>
            )}
            {inspectErr && <pre className="error">{inspectErr}</pre>}
          </section>
          {kind === 'document' && (
            <section className="kz-card">
              <p className="kz-note">{t('kantan:s1.documentNote')}</p>
              <DocumentPanel />
            </section>
          )}
        </>
      ) : step === 2 ? (
        <section className="kz-card">
          <h3 className="kz-title">{t('kantan:s2.title')}</h3>
          <p className="kz-note">
            {q1Needed || q2Needed ? t('kantan:s2.lead') : t('kantan:s2.leadNoQuestions')}
          </p>
          <PreviewList previews={previews} />
          {q1Needed && (
            <div className="kz-q">
              <p className="kz-q-text">{t('kantan:s2.q1', { count: preambleRowCount })}</p>
              <div className="kz-q-options">
                <button
                  type="button"
                  className={`kz-pill${q1 === 'keep' ? ' selected' : ''}`}
                  onClick={() => answerQ1('keep')}
                >
                  {t('kantan:s2.q1Yes')}
                </button>
                <button
                  type="button"
                  className={`kz-pill${q1 === 'drop' ? ' selected' : ''}`}
                  onClick={() => answerQ1('drop')}
                >
                  {t('kantan:s2.q1No')}
                </button>
              </div>
            </div>
          )}
          {q2Needed && (
            <div className="kz-q">
              <p className="kz-q-text">{t('kantan:s2.q2', { column: idColumn })}</p>
              <div className="kz-q-options">
                <button
                  type="button"
                  className={`kz-pill${q2 === 'only' ? ' selected' : ''}`}
                  onClick={() => setQ2('only')}
                >
                  {t('kantan:s2.q2Only')}
                </button>
                <button
                  type="button"
                  className={`kz-pill${q2 === 'elsewhere' ? ' selected' : ''}`}
                  onClick={() => setQ2('elsewhere')}
                >
                  {t('kantan:s2.q2Elsewhere')}
                </button>
                <button
                  type="button"
                  className={`kz-pill${q2 === 'unknown' ? ' selected' : ''}`}
                  onClick={() => setQ2('unknown')}
                >
                  {t('kantan:s2.q2Unknown')}
                </button>
              </div>
            </div>
          )}
          <div className="kz-actions">
            <button type="button" onClick={onProceed} disabled={!questionsAnswered || !isReady}>
              {t('kantan:s2.proceed')}
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={backToPick}>
              {t('kantan:s2.repick')}
            </button>
          </div>
          {!questionsAnswered && <p className="kz-note">{t('kantan:s2.needAnswers')}</p>}
          {!isReady && <p className="kz-note">{t('kantan:s1.aiNotReady')}</p>}
        </section>
      ) : step === 3 ? (
        <section className="kz-card">
          <PreviewList previews={previews} />
          {skeletonBusy && (
            <JobProgress
              label={t('kantan:s3.jobLabel')}
              status={status}
              lastPulseAt={lastPulseAt}
              onCancel={() => jobRef.current?.cancel() ?? Promise.resolve()}
            />
          )}
          <p className="kz-note">{t('kantan:s3.closeNote')}</p>
          {jobNotice && (
            <p className="job-cancelled-note" role="status">
              {jobNotice}
            </p>
          )}
          {errMsg && (
            <>
              <pre className="error">{errMsg}</pre>
              <div className="kz-actions">
                <button type="button" className="btn btn--ghost" onClick={() => setStep(2)}>
                  {t('kantan:s3.back')}
                </button>
              </div>
            </>
          )}
        </section>
      ) : (
        <section className="kz-card">
          {skeleton && (
            <SkeletonGate
              skeleton={skeleton}
              annotations={annotations}
              annotationsBusy={annotationsBusy}
              canRevalidate={files.length > 0}
              busy={continuing}
              onChange={onSkeletonEdited}
              onContinue={runContinue}
              onDiscard={() => {
                setSkeleton(null)
                setAnnotations(null)
                setStep(files.length > 0 ? 2 : 1)
              }}
              titleKey="kantan:s4.gateTitle"
              hintKey="kantan:s4.gateHint"
              continueKey="kantan:s4.continue"
              continuingKey="kantan:s4.continuing"
            />
          )}
          {continuing && (
            <>
              <JobProgress
                label={t('kantan:s4.continuing')}
                status={status}
                lastPulseAt={lastPulseAt}
                onCancel={() => jobRef.current?.cancel() ?? Promise.resolve()}
              />
              <p className="kz-note">{t('kantan:s3.closeNote')}</p>
            </>
          )}
          {jobNotice && (
            <p className="job-cancelled-note" role="status">
              {jobNotice}
            </p>
          )}
          {errMsg && <pre className="error">{errMsg}</pre>}
        </section>
      )}
    </div>
  )
}

// The big S1 drop target: click opens the picker; drag & drop works too.
function DropZone({ onFiles }: { onFiles: (list: FileList | null) => void }) {
  const { t } = useTranslation()
  const [dragOver, setDragOver] = useState(false)
  return (
    <label
      className={`kz-drop${dragOver ? ' drag' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragOver(false)
        onFiles(e.dataTransfer.files)
      }}
    >
      <input
        type="file"
        multiple
        accept={DROP_ACCEPT}
        onChange={(e) => {
          onFiles(e.target.files)
          e.target.value = '' // allow re-picking the same file after an error
        }}
      />
      <span className="kz-drop-main">{t('kantan:s1.dropTitle')}</span>
      <span className="kz-drop-sub">{t('kantan:s1.dropFormats')}</span>
    </label>
  )
}

// The S2/S3 preview block: a first-rows table per parsed file, a plain
// file-name card for the rest (json / xlsx / unreadable).
function PreviewList({ previews }: { previews: PreviewCard[] }) {
  const { t } = useTranslation()
  if (previews.length === 0) return null
  return (
    <div className="kz-preview">
      {previews.map((p) =>
        p.header ? (
          <div key={p.name} className="kz-preview-item">
            <div className="kz-preview-name">
              {p.name}
              <span className="kz-preview-caption"> — {t('kantan:s2.previewCaption')}</span>
            </div>
            <div className="kz-preview-tablewrap">
              <table className="kz-preview-table">
                <thead>
                  <tr>
                    {p.header.map((h, i) => (
                      <th key={i}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {p.rows.map((row, ri) => (
                    <tr key={ri}>
                      {p.header!.map((_, ci) => (
                        <td key={ci}>{row[ci] ?? ''}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div key={p.name} className="kz-preview-item">
            <div className="kz-preview-name">{p.name}</div>
            <p className="kz-note">{t('kantan:s2.fileCard')}</p>
          </div>
        ),
      )}
    </div>
  )
}
