import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  inspectCsvs,
  proposeContinue,
  proposeSkeleton,
  resumeJob,
  validateSkeleton,
  type InspectResult,
  type JobHandle,
  type MappingSkeleton,
  type ProposeResult,
  type SkeletonAnnotations,
  type SourceDialect,
} from '../api'
import { TABULAR_ACCEPT } from '../datasetsApi'
import { DocumentPanel } from '../DocumentPanel'
import { PRESET_HINTS } from '../domainHints'
import { JobProgress } from '../JobProgress'
import { useLlmSettings } from '../settings/context'
import { SkeletonGate } from '../SkeletonGate'
import { RecipeCard } from './RecipeCard'

// The kantan (かんたん) tier wizard — ADR kantan-mode-two-tier-ux.md, S1-S4.
// A linear, plain-language flow over the SAME backend calls the detail tier
// uses: drop files → auto inspect → two "only you know this" questions →
// staged skeleton propose → the human row-counting gate → continue → hand the
// finished proposal to the detail tier (WB_STORAGE-compatible snapshot).
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
type KzStep = 1 | 2 | 3 | 4

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
}: {
  /** Reports whether a job is in flight (the tier toggle locks while true). */
  onBusyChange: (busy: boolean) => void
  /** Called when the user opens the finished design in the detail tier. */
  onHandoffToDetail: () => void
}) {
  const { t, i18n } = useTranslation()
  const { isReady, getActiveCredentials, openSettings } = useLlmSettings()

  const [snap] = useState(loadSnapshot)
  // A continue job that survived a reload keeps S4 alive; otherwise every
  // restore lands on S1 (files are gone) — the skeleton, if any, is kept so a
  // re-drop of the same files resumes at the gate.
  const [step, setStep] = useState<KzStep>(() =>
    snap.skeleton && loadSavedJob()?.kind === 'propose' ? 4 : 1,
  )
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

  const busy = inspecting || skeletonBusy || continuing
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
    }
    try {
      sessionStorage.setItem(KZ_STORAGE, JSON.stringify(snapshot))
    } catch {
      /* sessionStorage may be unavailable — non-fatal */
    }
  }, [step, kind, q1, q2, dialectOverrides, skeleton, annotations, inspectionMd, proposal])

  // Hand the finished design to the detail tier: a WB_STORAGE-compatible
  // snapshot (mirrors WorkbenchView's WorkbenchSnapshot shape) opening on the
  // review step. Written as soon as the proposal exists, so the "open detail"
  // button works even after a reload of the completion card.
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
        setProposal(r.proposal_md)
        setInspectionMd(r.inspection_md)
        setSkeleton(null)
        setAnnotations(null)
        setStatus('')
        setContinuing(false)
        clearJob()
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

  // Map raw SSE statuses to plain language — never surface backend phase
  // strings in this tier (unknown phases read as "解析中…").
  function plainStatus(m: string): string {
    if (!m || m === 'done') return ''
    if (/start/i.test(m)) return t('kantan:job.preparing')
    return t('kantan:job.analyzing')
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
    void runInspect(arr)
  }

  async function runInspect(arr: File[]) {
    setInspecting(true)
    setInspectErr('')
    try {
      const result = await inspectCsvs(arr, [])
      setInspection(result)
      setInspectionMd(result.markdown)
      setPreviews(await buildPreviews(arr, result))
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
            setProposal(result.proposal_md)
            setInspectionMd(result.inspection_md)
            setSkeleton(null)
            setAnnotations(null)
            setStatus('')
            setContinuing(false)
            clearJob()
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

  // ---- render -----------------------------------------------------------------

  const recipePos: 1 | 2 | 3 = proposal ? 3 : step <= 2 ? 1 : step === 3 ? 2 : 3
  const resumeAvailable = !!skeleton && files.length === 0 && !proposal && step === 1

  return (
    <div className="kz-wizard">
      <RecipeCard current={recipePos} />

      {proposal ? (
        <section className="kz-card kz-done">
          <h3 className="kz-done-title">✓ {t('kantan:done.title')}</h3>
          <p className="kz-note">{t('kantan:done.body')}</p>
          <div className="kz-actions">
            <button type="button" onClick={openDetail}>
              {t('kantan:done.open')}
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
