import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ApiError,
  attachSource,
  fetchDraftStats,
  fetchTrialQueries,
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
  type RefineResult,
  type SkeletonAnnotations,
  type SkeletonResult,
  type SourceDialect,
  type TrialDetail,
  type TrialQueries,
} from '../api'
import { plainAdvisories } from '../advisoryPlain'
import { TABULAR_ACCEPT } from '../datasetsApi'
import { detectDatasetNamespace } from '../datasetNamespace'
import { DocumentPanel } from '../DocumentPanel'
import { PRESET_HINTS } from '../domainHints'
import {
  alignmentWordSplit,
  deleteDataset,
  getAlignment,
  getCatalogDatasets,
  getDatasetRules,
  promoteDataset,
  renameDataset,
  type AlignmentReport,
  type DatasetRules,
  type RuleMap,
  type RuleProperty,
  type RuleTerm,
} from '../galleryApi'
import type { DetailTab } from '../GalleryView'
import type { RedesignTarget } from '../WorkbenchView'
import { clearIngestJob, loadIngestJob, saveIngestJob } from '../ingestJob'
import { JobProgress } from '../JobProgress'
import { useLlmSettings } from '../settings/context'
import { fetchInstanceInfo, type WriteGate } from '../settings/instanceApi'
import { SkeletonGate } from '../SkeletonGate'
import { clearSourceFiles, loadSourceFiles, saveSourceFiles } from '../sourceFileStore'
import { stageSources, stagingAlive, unstageSources } from '../api'
import { localName } from '../vocab'
import { plainError } from './errorMessages'
import { RecipeCard } from './RecipeCard'

// The kantan (かんたん) tier wizard — ADR kantan-mode-two-tier-ux.md, S1-S9.
// A linear, plain-language flow over the SAME backend calls the detail tier
// uses: drop files → auto inspect → two "only you know this" questions →
// staged skeleton propose → the human row-counting gate (S4, human gate ①) →
// continue → S5 auto chain (save → source persist → DRAFT ingest, no approval
// button by design — ADR K3) → S6 column-meaning review (human gate ②) →
// S7 auto try-it-out queries (K9 — run, never offered as a button) →
// S8 publish (rename + word summary + promote in ONE screen, human gate ③ —
// K10) → S9 done (Ask-prefill question chips + the grow-your-dataset exits).
// No jargon may appear in this layer (no RML/IRI/namespace/canonical wording).

// Storage keys shared with the detail tier (WorkbenchView.tsx). Duplicated by
// value on purpose — exporting them from WorkbenchView would grow its diff.
const WB_STORAGE = 'asterism.workbench'
const JOB_STORAGE = 'asterism.workbench.job'
// Kantan's own snapshot (File objects can't persist — restore is best-effort).
const KZ_STORAGE = 'asterism.kantan'
// Kantan's own in-flight job. The S3 skeleton job lives here rather than in
// JOB_STORAGE so the detail tier never adopts a job of a kind it cannot finish
// (WorkbenchTier's toggle lock reads BOTH keys).
const KZ_JOB_STORAGE = 'asterism.kantan.job'

type KantanKind = 'tabular' | 'json' | 'document'
/** Which of the two "grow this dataset" intents S9 was clicked with: add the
 *  new measurements, or replace everything. Passed to the catalog so the
 *  landing page can open on that control (KZ-B-02). */
export type GrowFocus = 'append' | 'reingest'
type Q1Answer = 'keep' | 'drop'
type Q2Answer = 'only' | 'elsewhere' | 'unknown'
type KzStep = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9

/** Where the S5 auto chain (save → source persist → draft ingest) restarts. */
type PipeStage = 'materialize' | 'attach' | 'ingest'

/** The S5 stop card (K11 minimal): one plain-language headline per failure
 *  kind, the raw technical detail folded, and at most two exits — retry (same
 *  stage) and "check in detail mode". The 'design' kind adds a primary third
 *  exit: the same one-click "ask AI to fix" the detail tier has. The full
 *  error-family → plain-question translation table is K11 proper (a later
 *  task). */
interface StopCard {
  /** 'weakness' = the design is VALID but poorly connected / incomplete. It is
   *  the only kind the user may wave through ("このまま進む"): everything else
   *  is a defect that would dead-end later. Kept a separate kind rather than a
   *  flag on 'design' so no existing branch silently gains an escape hatch.
   *  'refineTruncated' = the AI's fix came back cut off; the PREVIOUS design is
   *  still in hand, so the exits are "ask again" and "continue without it". */
  kind:
    | 'materialize'
    | 'attach'
    | 'ingest'
    | 'design'
    | 'weakness'
    | 'files'
    | 'interrupted'
    | 'refineTruncated'
  detail: string
  /** Present → "もう一度試す" re-runs the chain from this stage. */
  retryFrom?: PipeStage
  /** 'design'/'weakness': the lines (trap details + repair recipes + warnings
   *  + validation/mapping issues, or the advisories) handed verbatim to the
   *  one-click AI fix — mirrors the detail tier's composeFixComment. */
  fixLines?: string[]
  /** 'design'/'weakness': the K11 plain-language face of the same findings
   *  (ADR §5.1) — canonical one-liners for known trap ids, free-form issues
   *  folded into one count line. Display only; the AI fix gets fixLines. */
  plainLines?: string[]
}

/** One S7 question card / S9 chip: plain question, plain answer, and (when the
 *  answer is a single entity) its IRI as the citation + the disclosed SPARQL. */
interface TrialQA {
  q: string
  a: string
  citeIri?: string
  /** The other fields of the SAME record, so "where does this number come from"
   *  is answerable in place (S7 runs on the still-unpublished draft, which no
   *  citation landing page can resolve yet — DEREF-LANDING-01). */
  citeDetails?: TrialDetail[]
  sparql?: string
}

// Locale-aware display of a SPARQL numeric lexical ("300", "1.42e0"): Number()
// first so canonical exponent forms render as plain figures; a non-finite
// parse falls back to the raw lexical unchanged.
function formatNum(raw: string, lng: string): string {
  const n = Number(raw)
  return Number.isFinite(n) ? n.toLocaleString(lng, { maximumFractionDigits: 6 }) : raw
}

// Extension → kind. Tabular comes from the shared TABULAR_ACCEPT constant so a
// later extension of that list (e.g. .xlsx) is picked up here automatically.
const TABULAR_EXTS = TABULAR_ACCEPT.split(',')
const JSON_EXTS = ['.json', '.geojson']
const DOCUMENT_EXTS = ['.xml', '.docx', '.pdf']
const DROP_ACCEPT = [...TABULAR_EXTS, ...JSON_EXTS, ...DOCUMENT_EXTS].join(',')

// Columns that look like a per-file serial ID (the Q2 trigger).
const ID_COLUMN_RE = /(^|[_-])(id|no|code)$/i

// The S2 "the table is not being read right" corrections. Values are the
// canonical tokens the server pins (never localized text); the labels are what
// a person calls them. Same tokens the detail tier's read-settings panel uses.
const DELIMITER_CHOICES: { value: string; key: string }[] = [
  { value: ',', key: 'kantan:s2.delimComma' },
  { value: '\t', key: 'kantan:s2.delimTab' },
  { value: ';', key: 'kantan:s2.delimSemicolon' },
  { value: 'whitespace', key: 'kantan:s2.delimSpace' },
]
// Encoding as a person meets it: "as it was read", "the one Excel writes in
// Japanese", "the international one". The codec names stay out of the tier —
// only the values travel to the server.
function encodingChoices(detected: string): { value: string; key: string }[] {
  const all = [
    { value: detected, key: 'kantan:s2.encAuto' },
    { value: 'cp932', key: 'kantan:s2.encExcelJa' },
    { value: 'utf-8', key: 'kantan:s2.encUtf8' },
  ]
  // "As it was read" IS one of the other two when detection agreed with it —
  // two pills selected at once would be a lie about what is in force.
  return all.filter((c, i) => all.findIndex((x) => x.value === c.value) === i)
}

/** Traps about the DOCUMENT wrapped around the design — its diagram (T5), its
 *  worked examples (T6), its rationale (T7), its example searches (T10). None
 *  of them changes a single triple, and a weak model fails them routinely, so
 *  in this tier they are a weakness the human may wave through rather than a
 *  full stop (KZ-A-24). The detail tier is unchanged: it still shows the grid. */
const DOC_ONLY_TRAPS = new Set(['T5', 'T6', 'T7', 'T10'])

/** The name the auto chain registers the draft under. "dataset" made every
 *  abandoned run look identical in the catalog list (KZ-A-28); the first file's
 *  stem is what the person who dropped it will recognise. */
function draftStem(files: File[]): string {
  const first = files[0]?.name ?? ''
  const dot = first.lastIndexOf('.')
  const stem = (dot > 0 ? first.slice(0, dot) : first).trim()
  return stem
}

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

/** What a re-drop is allowed to be: the design was written against ONE kind of
 *  file, so the resume drop zone must not accept a PDF for a table (RESUME-18). */
function acceptFor(k: KantanKind | null): string {
  if (k === 'tabular') return TABULAR_EXTS.join(',')
  if (k === 'json') return JSON_EXTS.join(',')
  if (k === 'document') return DOCUMENT_EXTS.join(',')
  return DROP_ACCEPT
}

/** An English identifier as a readable phrase: `hasSeebeckCoefficient` →
 *  "Seebeck Coefficient", `sample_name` → "Sample name". The LAST deterministic
 *  resort when neither the reviewed label nor the user's own column name is
 *  available — an identifier is never shown raw in this tier (K4). */
function humanizeLocal(name: string): string {
  // A shorthand that never made it through localName (`ex:hasZT`): the prefix
  // is machine notation and has no place in this tier (K13).
  const bare = /^[A-Za-z][\w-]*:[^/\s]+$/.test(name) ? name.slice(name.indexOf(':') + 1) : name
  const stripped = bare.replace(/^(has|is)(?=[A-Z_])/, '')
  const spaced = stripped
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim()
  if (!spaced) return name
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

// Read settings the preview falls back to when detection reported nothing for a
// source (a clean CSV is simply absent from `inspect.dialects`).
function defaultDialect(name: string): SourceDialect {
  return {
    encoding: 'utf-8-sig',
    delimiter: extOf(name) === '.tsv' ? '\t' : ',',
    collapse: false,
    skip_rows: 0,
    preamble: 'drop',
  }
}

/** Decode the preview slice with the read settings in force. Python codec names
 *  are mapped to the labels TextDecoder knows; anything unsupported falls back
 *  to UTF-8 — the preview is display material, never the read of record. */
function decodeSlice(buf: ArrayBuffer, encoding?: string): string {
  const raw = (encoding ?? 'utf-8').toLowerCase().replace(/[_ ]/g, '-')
  const label =
    raw === 'cp932' || raw === 'ms932' || raw === 'shift-jis'
      ? 'shift_jis'
      : raw === 'utf-8-sig'
        ? 'utf-8'
        : raw
  try {
    return new TextDecoder(label).decode(buf)
  } catch {
    return new TextDecoder().decode(buf)
  }
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
 *  parse (json / xlsx / unreadable) — the UI shows a file-name card instead.
 *  `preambleLines` = the ACTUAL lines detected before the table (so the S2
 *  metadata question can show what it is asking about — read client-side from
 *  the user's own file, same as the table preview: display, not publication). */
interface PreviewCard {
  name: string
  /** The slugged name the read settings are keyed by (S2's read-fix panel). */
  canonical: string
  header: string[] | null
  rows: string[][]
  preambleLines?: string[]
}

async function buildPreviews(
  files: File[],
  inspect: InspectResult,
  overrides: Record<string, SourceDialect> = {},
): Promise<PreviewCard[]> {
  const out: PreviewCard[] = []
  for (let i = 0; i < files.length; i++) {
    const file = files[i]
    const ext = extOf(file.name)
    // Canonical (slugged) source names come back in upload order, so zip by
    // index; fall back to the raw file name when the counts don't line up.
    const canonical =
      inspect.sourceNames.length === files.length ? inspect.sourceNames[i] : file.name
    if (kindOf(file.name) !== 'tabular' || ext === '.xlsx') {
      out.push({ name: file.name, canonical, header: null, rows: [] })
      continue
    }
    // A human correction from the S2 read-fix panel wins over detection, so the
    // table on screen shows the read the design will actually use (KZ-A-10).
    const dialect = overrides[canonical] ?? inspect.dialects[canonical]
    try {
      const buf = await file.slice(0, PREVIEW_BYTES).arrayBuffer()
      const text = decodeSlice(buf, dialect?.encoding)
      let lines = text.split(/\r\n|\r|\n/)
      if (file.size > PREVIEW_BYTES) lines = lines.slice(0, -1) // drop the cut-off tail
      const skip = dialect?.skip_rows ?? 0
      const preambleLines = lines.slice(0, skip).filter((l) => l.trim() !== '')
      lines = lines.slice(skip).filter((l) => l.trim() !== '')
      const delim = dialect?.delimiter ?? (ext === '.tsv' ? '\t' : ',')
      const cells = lines.slice(0, PREVIEW_ROWS + 1).map((l) => splitRow(l, delim))
      const [header, ...rows] = cells
      out.push({
        name: file.name,
        canonical,
        header: header ?? null,
        rows,
        ...(preambleLines.length > 0 ? { preambleLines } : {}),
      })
    } catch {
      // preview is enrichment
      out.push({ name: file.name, canonical, header: null, rows: [] })
    }
  }
  return out
}

/** The `{column}` placeholders inside an ID template — a column that only
 *  builds an ID is still a column the design reads (DETAIL-GAP-12). */
function templateColumns(template?: string): string[] {
  if (!template) return []
  return [...template.matchAll(/\{([^{}]+)\}/g)].map((m) => m[1].trim()).filter(Boolean)
}

/** Every column name the S2 preview could read, in order. Kept in the snapshot
 *  so S6 can name the columns the design left out — `columnSamples` cannot do
 *  that job (it drops columns whose first rows are empty) — DETAIL-GAP-12. */
function deriveSourceColumns(cards: PreviewCard[]): string[] {
  const out: string[] = []
  for (const card of cards) {
    for (const col of card.header ?? []) {
      const name = col.trim()
      if (name !== '' && !out.includes(name)) out.push(name)
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
  /** Source kind in the detail tier's vocabulary ('csv' | 'json' | 'document').
   *  Documents need no AI design — but their panel only renders while the kind
   *  says so, and its own resume effect lives inside it (RESUME-19). */
  kind: 'csv' | 'json' | 'document' | null
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
  // S8/S9: the publish name being edited and whether promote landed — a reload
  // on S9 must come back as "published", not re-offer the publish button.
  pubName: string
  published: boolean
  // かんたん見直し (catalog 見直す → S6): banner state + whether THIS session
  // has re-ingested a draft. A no-change review must exit to the catalog, not
  // to publish — there is no staged graph to promote until a refine ran.
  redesigning: boolean
  reingested: boolean
  /** The server-side staged copy of the source (ADR source-staging.md). */
  stagingId?: string | null
  /** The stop card the user was looking at, verbatim. Without it a reload turns
   *  "row IDs would overlap + [have the AI fix it]" into a generic "the work was
   *  interrupted" and the reason is gone for good (RESUME-03 / KZ-A-29). */
  stop?: StopCard | null
  /** How many AI fixes have been tried on this stop (shown on the card). */
  aiFixCount?: number
  /** Why the catalog sent the user into 見直す — the findings card and its
   *  one-click fix must survive a reload too (RESUME-10). */
  carriedAdvisories?: string[]
  /** The files this design was written against. "Drop the file again" is only
   *  answerable days later if the machine says WHICH file (RESUME-17). */
  sourceNames?: { name: string; size: number }[]
  /** Every column S2 could read — S6 names the ones the design left out. */
  sourceColumns?: string[]
  /** The row-counting gate the human confirmed, kept so S6/S7 can go BACK to
   *  it when the counts look wrong (KZ-B-03 / DETAIL-GAP-08). */
  gateSkeleton?: MappingSkeleton | null
  /** Set while the design is open in the detail tier: coming back must restore
   *  the same screen, and must adopt whatever the detail tier saved there
   *  (DETAIL-GAP-04 / DETAIL-GAP-05). */
  handedToDetail?: boolean
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

/** The design as the DETAIL tier last left it, when this snapshot was handed
 *  over to it for the same dataset. Read synchronously at mount, before the
 *  handoff effect can write the (older) kantan copy back over it: otherwise a
 *  round trip through the detail tier silently loses everything fixed there
 *  and the two tiers publish different designs (DETAIL-GAP-05). */
function detailProposalFor(datasetId: string | null | undefined): string | null {
  if (!datasetId) return null
  try {
    const raw = sessionStorage.getItem(WB_STORAGE)
    if (!raw) return null
    const wb = JSON.parse(raw) as { proposal?: string; redesignId?: string }
    if (wb.redesignId !== datasetId) return null
    return wb.proposal && wb.proposal.trim() !== '' ? wb.proposal : null
  } catch {
    return null
  }
}

// The AI jobs this tier owns, under kantan's OWN key: "この画面を離れても続き
// ます" is only true if they survive leaving the screen (KZ-A-06 for the S3
// skeleton, KZ-A-30 for the AI fix / S6 reflect). Kept out of JOB_STORAGE
// because the detail tier resumes that one as a 'propose'.
interface KantanJob {
  jobId: string
  kind: 'skeleton' | 'refine'
  /** refine only: which chain asked for it (progress label + where it lands). */
  mode?: 'note' | 'fix'
  /** refine only: the stop card to put back if the fix itself fails. */
  restore?: StopCard | null
}
function loadKantanJob(): KantanJob | null {
  try {
    const raw = sessionStorage.getItem(KZ_JOB_STORAGE)
    if (!raw) return null
    const job = JSON.parse(raw) as KantanJob
    return job.jobId ? job : null
  } catch {
    return null
  }
}
function saveKantanJob(job: KantanJob) {
  try {
    sessionStorage.setItem(KZ_JOB_STORAGE, JSON.stringify(job))
  } catch {
    /* non-fatal */
  }
}
function clearKantanJob() {
  sessionStorage.removeItem(KZ_JOB_STORAGE)
}

export function KantanWizard({
  onBusyChange,
  onHandoffToDetail,
  onOpenDataset,
  onOpenAsk,
  redesignTarget,
  onRedesignConsumed,
  onRedesignDetail,
  onCreateCrosswalk,
}: {
  /** Reports whether a job is in flight (the tier toggle locks while true). */
  onBusyChange: (busy: boolean) => void
  /** Called when the user opens the finished design in the detail tier. */
  onHandoffToDetail: () => void
  /** Opens the catalog detail for a dataset (S9's grow-the-dataset exits land
   *  on the ファイル tab, where the append / re-ingest controls live). `focus`
   *  names which of the two the person chose — a receiver that does not use it
   *  yet simply ignores it (KZ-B-02). */
  onOpenDataset?: (id: string, tab?: DetailTab, focus?: GrowFocus) => void
  /** Opens the Ask view with the question prefilled (the S9 chips). */
  onOpenAsk?: (question: string) => void
  /** Catalog 見直す: reopen this dataset's stored design as the kantan
   *  re-check flow (seeds the wizard at S6 — the column meanings). */
  redesignTarget?: RedesignTarget | null
  onRedesignConsumed?: () => void
  /** "構造から見直す": hand the (possibly refined) design to the detail tier
   *  as a redesign target — the full structural review lives there. */
  onRedesignDetail?: (target: RedesignTarget) => void
  /** Opens the guided "connect your data" flow. Offered on S9 only once a SECOND
   *  dataset is published — that is the moment connecting first becomes possible,
   *  and the moment the value of it is easiest to see. */
  onCreateCrosswalk?: () => void
}) {
  const { t, i18n } = useTranslation()
  const { isReady, getActiveCredentials, openSettings, activeUsesServerKey } = useLlmSettings()

  const [snap] = useState(loadSnapshot)
  // Restore priority: S5-S9 survive on their persisted dataset id (an S5
  // restore additionally needs the proposal — the chain restarts from it; an
  // S9 restore additionally needs the published flag — never re-offer the
  // publish button for a promote that already landed); a continue job that
  // survived a reload keeps S4 alive; otherwise every restore lands on S1
  // (files are gone) — the skeleton, if any, is kept so a re-drop of the same
  // files resumes at the gate.
  const [step, setStep] = useState<KzStep>(() => {
    // An AI fix / reflect that was still running comes back as the S5 progress
    // screen (the mount effect below re-attaches to its stream) — KZ-A-30.
    if (loadKantanJob()?.kind === 'refine' && snap.proposal) return 5
    if (snap.datasetId) {
      if (snap.step === 9 && snap.published) return 9
      if (snap.step === 8 || (snap.step === 9 && !snap.published)) return 8
      if (snap.step === 7) return 7
      if (snap.step === 6) return 6
      if (snap.step === 5 && snap.proposal) return 5
    }
    if (snap.skeleton && loadSavedJob()?.kind === 'propose') return 4
    // A skeleton job that was still running when the screen was left comes back
    // as S3 (the mount effect below re-attaches to its stream).
    if (loadKantanJob()?.kind === 'skeleton') return 3
    return 1
  })
  const [files, setFiles] = useState<File[]>([])
  // The staged copy on the server (ADR source-staging.md). Once set, design
  // calls pass the id and nothing is re-uploaded; a reload keeps working
  // because the id is a string the snapshot can hold. `null` = not staged
  // (an old server, a closed write gate, or the record expired) → the files in
  // this tab are the only copy, exactly the legacy path.
  const [stagingId, setStagingId] = useState<string | null>(snap.stagingId ?? null)
  const hasSource = files.length > 0 || !!stagingId
  const [kind, setKind] = useState<KantanKind | null>(
    snap.kind === 'json'
      ? 'json'
      : snap.kind === 'csv'
        ? 'tabular'
        : snap.kind === 'document'
          ? 'document'
          : null,
  )
  const [pickError, setPickError] = useState('')
  // A document run that was still going when the screen was left: the panel
  // below re-attaches on its own, but say so rather than showing a bare form.
  const [documentResumed] = useState(() => snap.kind === 'document')
  // The write gate, asked once at the door. `token_required` / `closed` means
  // nothing can be saved on this install, so say it BEFORE 1-3 minutes of AI
  // work rather than at the first save (KZ-A-42).
  const [writeGate, setWriteGate] = useState<WriteGate | null>(null)
  // Coming back (reload / tab switch) is a state the reader can see: silently
  // landing on S4 reads as "it lost my work", and an empty drop zone while the
  // source is still being fetched reads as "it is gone" (RESUME-01).
  const [restoring, setRestoring] = useState(
    () => !!snap.stagingId || (snap.step ?? 1) > 1 || !!snap.skeleton,
  )
  const [resumed, setResumed] = useState(false)
  const [keptAnswers, setKeptAnswers] = useState(false)
  /** A file this browser still holds from a finished run: offered as a choice
   *  ("use it" / "drop a new one") instead of being read again behind the
   *  person's back — the silent double-registration path (RESUME-11). */
  const [pendingRestore, setPendingRestore] = useState<File[] | null>(null)
  /** True once the human has chosen files themselves — a slower restore must
   *  then not overwrite what they just did (RESUME-A2). */
  const userActedRef = useRef(false)

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
  // The gate the human already confirmed. Kept past S4 so "the counts are
  // wrong" has somewhere to go BACK to (KZ-B-03) — `skeleton` itself is
  // cleared at continue, because it also drives the S4 screen.
  const [gateSkeleton, setGateSkeleton] = useState<MappingSkeleton | null>(
    snap.gateSkeleton ?? null,
  )
  const [annotations, setAnnotations] = useState<SkeletonAnnotations | null>(
    snap.annotations ?? null,
  )
  const [annotationsBusy, setAnnotationsBusy] = useState(false)
  // A skeleton job persisted across the last unmount is picked up on mount, so
  // the screen must already read as busy on the first paint (KZ-A-06).
  const [skeletonBusy, setSkeletonBusy] = useState(() => loadKantanJob()?.kind === 'skeleton')
  const [continuing, setContinuing] = useState(false)
  const [status, setStatus] = useState('')
  const [lastPulseAt, setLastPulseAt] = useState<number | null>(null)
  const [jobNotice, setJobNotice] = useState('')
  const [errMsg, setErrMsg] = useState('')
  // Coming back from the detail tier: whatever is saved THERE for this same
  // dataset is the newer design (DETAIL-GAP-05). Read at init, never in an
  // effect — the handoff effect below would otherwise write this snapshot's
  // older copy over it, and the fixes made in detail mode would vanish with
  // nobody told. Applies to BOTH ways over: the tier toggle and the stop
  // card's "詳細モードで確認する".
  const [initialDesign] = useState(() => {
    const own = snap.proposal ?? ''
    const fromDetail = detailProposalFor(snap.datasetId)
    return { text: fromDetail ?? own, adopted: !!fromDetail && fromDetail !== own }
  })
  const [proposal, setProposal] = useState(initialDesign.text)
  // Say it out loud, once: this screen came back from the detail tier, and any
  // change made there is what is on screen now (RESUME-12).
  const [returnedFromDetail, setReturnedFromDetail] = useState<boolean>(
    () => initialDesign.adopted || (!!snap.handedToDetail && !!snap.datasetId),
  )
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
    // A refine that is still running owns the screen (its own card comes back
    // when it lands / fails) — never greet its return with a stop card.
    if (loadKantanJob()?.kind === 'refine') return null
    if (snap.step !== 5 || !snap.datasetId || !snap.proposal) return null
    const saved = loadIngestJob()
    if (saved && saved.kind === 'ingest' && saved.datasetId === snap.datasetId) return null
    // The card the user was actually looking at (design / weakness / …) wins:
    // rebuilding a generic one throws away the reason AND its AI-fix button.
    if (snap.stop) return snap.stop
    return snap.sourceAttached
      ? { kind: 'interrupted', detail: '', retryFrom: 'ingest' }
      : { kind: 'files', detail: '' }
  })

  // S6: the column-meaning review (human gate ②).
  const [columnSamples, setColumnSamples] = useState<Record<string, string[]>>(
    snap.columnSamples ?? {},
  )
  // Which files this design belongs to, and every column they showed.
  const [sourceNames, setSourceNames] = useState<{ name: string; size: number }[]>(
    snap.sourceNames ?? [],
  )
  const [sourceColumns, setSourceColumns] = useState<string[]>(snap.sourceColumns ?? [])
  const [rules, setRules] = useState<DatasetRules | null>(null)
  const [stats, setStats] = useState<DraftStats | null>(null)
  const [s6Loading, setS6Loading] = useState(false)
  const [s6Err, setS6Err] = useState('')
  const [note, setNote] = useState('')
  // 'note' = the S6 free-text reflect; 'fix' = the S5 design-stop AI fix. Both
  // ride the SAME refine → re-materialize chain; the flag only picks the
  // progress label and where a failure lands. A refine that outlived the last
  // unmount is re-attached on mount, so the screen must read as busy at once.
  const [refining, setRefining] = useState<false | 'note' | 'fix'>(() => {
    const job = loadKantanJob()
    return job?.kind === 'refine' ? (job.mode ?? 'note') : false
  })
  const [refineErr, setRefineErr] = useState('')
  // S5 design-stop AI fix: its own error slot + attempt counter (AI 修正 n 回目).
  const [fixErr, setFixErr] = useState('')
  const [aiFixCount, setAiFixCount] = useState(snap.aiFixCount ?? 0)
  const [confirmed, setConfirmed] = useState<boolean>(snap.confirmed ?? false)
  // Whether the LAST design round's self-correction shrank the mapping while
  // repairing it (the columns it dropped are what S6 must be checked against).
  const [coverageDropped, setCoverageDropped] = useState(false)
  // The refine (AI fix / S6 note) that is in flight, so a truncated answer can
  // offer "ask again" without making the human retype anything.
  const lastRefineRef = useRef<{ comments: string[]; mode: 'note' | 'fix'; restore?: StopCard } | null>(
    null,
  )

  // S7: the automatic try-it-out queries (ADR K9 — auto-run, never a button).
  const [trial, setTrial] = useState<TrialQueries | null>(null)
  const [trialLoading, setTrialLoading] = useState(false)
  const [trialErr, setTrialErr] = useState('')

  // S8: publish = name + per-kind counts + word summary + promote, ONE screen
  // (human gate ③ — K10). The name defaults empty: the auto chain registered
  // the draft under a throwaway name, and an empty name disables the button.
  const [pubName, setPubName] = useState<string>(snap.pubName ?? '')
  const [alignment, setAlignment] = useState<AlignmentReport | null>(null)
  const [s8Loading, setS8Loading] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [pubErr, setPubErr] = useState('')
  const [published, setPublished] = useState<boolean>(snap.published ?? false)
  // How many datasets are published (this one included). Fetched only once S9 is
  // reached, so it costs nothing during the wizard. null = unknown → the connect
  // offer stays hidden (fail closed: never point at a dead end).
  const [publishedCount, setPublishedCount] = useState<number | null>(null)

  // かんたん見直し (catalog 見直す): the wizard reopens an existing dataset at
  // S6. `reingested` = whether THIS session ran the refine → re-ingest chain;
  // until then there is no staged draft, so "confirm" exits to the catalog
  // instead of leading to a publish that would 400.
  const [redesigning, setRedesigning] = useState<boolean>(snap.redesigning ?? false)
  const [reingested, setReingested] = useState<boolean>(snap.reingested ?? true)

  // Catalog 見直す → seed the wizard at S6 on the stored design. Same
  // adjust-during-render consumption as WorkbenchView's seededTarget, so the
  // re-check flow opens on this very render pass. Any leftover snapshot state
  // (a previous run) is dropped first — the redesign intent wins.
  // Findings that sent the user here, so the review screen can show WHAT to fix
  // and hand it to the AI in one click. Cleared once a fix round starts (the
  // next materialize re-derives them from the design as it then stands).
  const [carriedAdvisories, setCarriedAdvisories] = useState<string[]>(
    snap.carriedAdvisories ?? [],
  )
  const [seededRedesign, setSeededRedesign] = useState<string | null>(null)
  if (redesignTarget && redesignTarget.datasetId !== seededRedesign) {
    setSeededRedesign(redesignTarget.datasetId)
    resetPipelineState()
    setFiles([])
    setSourceNames([])
    void clearSourceFiles() // the redesign's source is persisted server-side
    setStagingId(null)
    setKind('tabular')
    setSkeleton(null)
    setAnnotations(null)
    setInspectionMd('')
    setErrMsg('')
    setJobNotice('')
    setProposal(redesignTarget.proposalMd)
    setKzDatasetId(redesignTarget.datasetId)
    setKzDatasetName(redesignTarget.datasetName)
    setSourceAttached(true) // design-time source is persisted server-side
    setPubName(redesignTarget.datasetName) // republish keeps the current name
    setRedesigning(true)
    setReingested(false)
    setCarriedAdvisories(redesignTarget.advisories ?? [])
    setStep(6)
    onRedesignConsumed?.()
  }

  const busy =
    inspecting || skeletonBusy || continuing || pipeBusy || refining !== false || publishing
  useEffect(() => {
    onBusyChange(busy)
  }, [busy, onBusyChange])

  // Ask the write gate at the door, not after two AI rounds (KZ-A-42). An old
  // api (no write_gate field) answers nothing and the flow is unchanged.
  useEffect(() => {
    let off = false
    void fetchInstanceInfo().then((info) => {
      if (!off && info?.write_gate) setWriteGate(info.write_gate)
    })
    return () => {
      off = true
    }
  }, [])
  const writeBlocked = writeGate === 'token_required' || writeGate === 'closed'

  // Count published datasets when S9 is reached, to decide whether connecting is
  // even possible yet. A failure leaves it null and the offer simply does not
  // appear — a broken count must not produce a button that leads nowhere.
  useEffect(() => {
    if (step !== 9) return
    let off = false
    getCatalogDatasets()
      .then((all) => {
        if (off) return
        setPublishedCount(all.filter((d) => d.statusKind === 'pub' && !d.isCrosswalk).length)
      })
      .catch(() => !off && setPublishedCount(null))
    return () => {
      off = true
    }
  }, [step])

  // Persist the (serializable) wizard state so a tab switch / reload is
  // recoverable. Files are not persistable — restore is best-effort by design.
  useEffect(() => {
    const snapshot: KantanSnapshot = {
      step,
      kind:
        kind === 'json'
          ? 'json'
          : kind === 'tabular'
            ? 'csv'
            : kind === 'document'
              ? 'document'
              : null,
      q1,
      q2,
      dialectOverrides,
      skeleton,
      annotations,
      inspectionMd,
      proposal,
      datasetId: kzDatasetId,
      datasetName: kzDatasetName,
      stagingId,
      sourceAttached,
      autoFixed,
      confirmed,
      columnSamples,
      pubName,
      published,
      redesigning,
      reingested,
      stop,
      aiFixCount,
      carriedAdvisories,
      sourceNames,
      sourceColumns,
      gateSkeleton,
      // Consumed at mount: the flag is re-armed by openDetail (which writes it
      // synchronously) and must not survive as a standing "we are in detail".
      handedToDetail: false,
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
    stagingId,
    sourceAttached,
    autoFixed,
    confirmed,
    columnSamples,
    pubName,
    published,
    redesigning,
    reingested,
    stop,
    aiFixCount,
    carriedAdvisories,
    sourceNames,
    sourceColumns,
    gateSkeleton,
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
      // The server-side copy of the source: without it, "fix it yourself in
      // detail mode" arrives with no files at all in the browsers that cannot
      // keep them (private windows, old WebViews) — RESUME-13.
      ...(stagingId ? { stagingId } : {}),
      // WHAT to fix, pre-filled into the detail tier's refine box. The AI-facing
      // technical lines are handed over verbatim (the plain sentences are for
      // this screen only), so the reader can press 修正を依頼 straight away.
      ...(stop?.fixLines?.length
        ? {
            comment: `${t('workbench:fix.commentIntro')}\n${stop.fixLines
              .map((l) => `- ${l}`)
              .join('\n')}`,
          }
        : {}),
      // The wizard's registered record (once the auto chain minted / reopened
      // one): the detail tier must UPDATE this dataset in place on save —
      // without this, a handoff after S5 would re-mint a duplicate record.
      ...(kzDatasetId
        ? {
            redesignId: kzDatasetId,
            redesignName: kzDatasetName ?? undefined,
            redesignOrigin: redesigning ? 'catalog' : 'adopted',
          }
        : {}),
    }
    try {
      sessionStorage.setItem(WB_STORAGE, JSON.stringify(detailSnapshot))
    } catch {
      /* non-fatal */
    }
  }, [
    proposal,
    inspectionMd,
    kind,
    q2,
    dialectOverrides,
    kzDatasetId,
    kzDatasetName,
    redesigning,
    stagingId,
    stop,
    t,
  ])

  // Resume an in-flight job this tier owns (S3 skeleton, or the AI fix / S6
  // reflect) after leaving the screen: the same SSE replay, under kantan's own
  // key. Without this, "ほかの画面を見に行っても続きます" was false and 1-3
  // minutes of AI work died on a tab switch (KZ-A-06 / KZ-A-30).
  useEffect(() => {
    const job = loadKantanJob()
    if (!job) return
    if (job.kind === 'refine') {
      const mode = job.mode ?? 'note'
      const restore = job.restore ?? undefined
      lastRefineRef.current = { comments: [], mode, restore }
      const refineHandle = resumeJob(job.jobId, {
        onPulse: () => setLastPulseAt(Date.now()),
        onStatus: (m) => setStatus(plainStatus(m)),
        onDone: (result) => {
          clearKantanJob()
          finishRefine(result)
        },
        onError: (m) => {
          clearKantanJob()
          failRefine(m, mode, restore)
          landAfterResumedRefine(mode, restore)
        },
        onCancelled: () => {
          clearKantanJob()
          setJobNotice(t('workbench:job.cancelled'))
          if (mode === 'fix' && restore) setStop(restore)
          setStatus('')
          setRefining(false)
          landAfterResumedRefine(mode, restore)
        },
      })
      jobRef.current = refineHandle
      return () => refineHandle.close()
    }
    const handle = resumeJob(job.jobId, {
      onPulse: () => setLastPulseAt(Date.now()),
      onStatus: (m) => setStatus(plainStatus(m)),
      onDone: (result) => {
        const r = result as SkeletonResult
        setSkeleton(r.skeleton)
        setAnnotations(r.annotations ?? null)
        setInspectionMd(r.inspection_md)
        setStatus('')
        setSkeletonBusy(false)
        clearKantanJob()
        setStep(4)
      },
      onError: (m) => {
        setErrMsg(m)
        setStatus('')
        setSkeletonBusy(false)
        clearKantanJob()
      },
      onCancelled: () => {
        setJobNotice(t('workbench:job.cancelled'))
        setStatus('')
        setSkeletonBusy(false)
        clearKantanJob()
        setStep(hasSource ? 2 : 1)
      },
    })
    jobRef.current = handle
    return () => handle.close()
    // Mount-only: resume whatever kantan job was persisted before this mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Resume an in-flight continue job after a reload (same SSE replay recovery
  // as the detail tier; the tiers never mount together, so no double-resume).
  useEffect(() => {
    if (loadKantanJob()) return // the effect above owns this mount
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
        setGateSkeleton((cur) => cur ?? skeleton)
        setSkeleton(null)
        setAnnotations(null)
        setStatus('')
        setContinuing(false)
        clearJob()
        const remaining = adoptAutocorrect(r)
        setProposal(r.proposal_md)
        if (remaining.length > 0) {
          setStopFromRemaining(remaining)
          setStep(5)
          return
        }
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

  // Mount-only: bring the source back. Two copies may exist — the server's
  // staged record (ADR source-staging.md; its id is a string the snapshot
  // holds) and this tab's own files (IndexedDB) — and either is enough:
  //   S1 with a kept skeleton → straight to the gate (S4) with a fresh evidence
  //     check, reading whichever copy is there (the live staging, else the
  //     local files); S1 without one → a normal re-inspect;
  //   S4-S9 → the files are just there again (re-check, rethink, re-ingest).
  // A remembered id that died (expired, consumed) is forgotten; a fresh drop
  // will stage anew. Nothing here re-stages a source the server still holds.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const remembered = snap.stagingId ?? null
      const [restored, live] = await Promise.all([
        loadSourceFiles(),
        remembered ? stagingAlive(remembered) : Promise.resolve(false),
      ])
      if (cancelled) return
      setRestoring(false)
      // The human got there first (dropped a file while this was awaiting a
      // server round-trip): their intent wins — a late restore must not
      // overwrite it and start a second read of the OLD files (RESUME-A2).
      if (userActedRef.current) return
      const sid = live ? remembered : null
      if (!live) setStagingId(null)
      if (restored.length === 0 && !sid) return
      setResumed(true)
      if (step !== 1) {
        setFiles(restored)
        // The source is back, so "ファイルが見つかりません" is simply untrue:
        // turn it into the one-click resume the design already deserves.
        setStop((cur) =>
          cur?.kind === 'files' ? { kind: 'interrupted', detail: '', retryFrom: 'attach' } : cur,
        )
        return
      }
      if (skeleton && kind) {
        // The resume branch, without re-staging what the server already has.
        setFiles(restored)
        setStep(4)
        setAnnotationsBusy(true)
        validateSkeleton(restored, skeleton, dialectOverrides, sid)
          .then(setAnnotations)
          .catch(() => {
            /* evidence is enrichment */
          })
          .finally(() => setAnnotationsBusy(false))
        return
      }
      if (restored.length > 0) {
        // A file left over from a run that is OVER (published and grown, or a
        // dropped snapshot) — reading it again on its own would quietly put the
        // same measurements in twice. Ask instead (RESUME-11).
        if (!snap.step || snap.step <= 1) {
          setPendingRestore(restored)
          setResumed(false)
          return
        }
        if (sid) void unstageSources(sid) // onFilesChosen stages afresh
        // These are the very files this snapshot's answers were given for, so
        // the two "only you know this" questions are NOT asked again (RESUME-09).
        onFilesChosen(restored, { restored: true })
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // S5-S9 reload recovery (best-effort, ADR K3/K11): a still-running draft
  // ingest is re-attached through the same SSE replay the catalog uses
  // (StrictMode-safe — re-subscribing twice is harmless, unlike re-POSTing;
  // the no-live-job case became a stop card at state init above). S6-S9 just
  // re-fetch their read-only data (S9 only the chips' question source).
  useEffect(() => {
    if (!kzDatasetId) return
    if (step === 6 && !confirmed) {
      void loadS6(kzDatasetId)
      return
    }
    if (step === 7) {
      void loadS7(kzDatasetId)
      return
    }
    if (step === 8) {
      void loadS8(kzDatasetId)
      return
    }
    if (step === 9) {
      void loadS7(kzDatasetId) // chips reuse the S7 questions; enrichment only
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

  // "AI に直してもらう" that changes nothing: a weak model hands the same
  // problem back round after round, and the automatic loop already stops on
  // no-progress — the button a human presses had no such stop, so it invited
  // an endless retry (WEAK-MODEL-24). One repeat of the SAME findings is
  // enough to say so and step the AI fix down to a secondary offer.
  const prevStopSig = useRef<string | null>(null)
  const [fixStuck, setFixStuck] = useState(false)

  /** Raise a findings card (design / weakness) and note whether it is the very
   *  same one the last AI fix was asked to clear. */
  function setDesignStop(card: StopCard) {
    const sig = (card.plainLines ?? card.fixLines ?? []).join('\n')
    setFixStuck(aiFixCount > 0 && prevStopSig.current === sig)
    prevStopSig.current = sig
    setStop(card)
  }

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

  /** The plain body of a raw error message, for the places that used to print
   *  the English string inline (the AI-fix failure line). */
  function plainBody(raw: string): string {
    const plain = plainError(raw)
    return t(plain.body, plain.vars)
  }

  function defaultDraftName(fs: File[]): string {
    const stem = draftStem(fs.length > 0 ? fs : files)
    return stem ? t('kantan:s5.draftName', { stem }) : t('kantan:s5.draftName', { stem: 'data' })
  }

  /** Stop on what the self-correction could not clear, BEFORE the auto chain
   *  runs: the same design card, with the machine's own remaining issues both
   *  as plain sentences and as the AI-fix input. */
  function setStopFromRemaining(remaining: string[]) {
    setDesignStop({
      kind: 'design',
      detail: remaining.join('\n'),
      fixLines: remaining,
      plainLines: issuePlainLines(remaining),
    })
  }

  /** Read the self-correction summary the design job returns: whether it
   *  converged, what it could not clear, and whether it shrank the mapping on
   *  the way (WEAK-MODEL-13 / -15, DETAIL-GAP-22). Shared by the fresh and the
   *  resumed propose paths so both tell the same story. */
  function adoptAutocorrect(r: ProposeResult): string[] {
    const ac = r.autocorrect
    const rounds = ac?.rounds?.length ?? 0
    // The server always records round 0, so "it ran" is not "it fixed
    // something" — claim a fix only when a later round actually converged.
    setAutoFixed(!!ac?.converged && rounds > 1)
    setCoverageDropped(!!ac?.coverage_dropped)
    // What it could not clear becomes the stop card (WEAK-MODEL-13): the run
    // must not walk into an ingest 422 over problems already known here.
    return ac && !ac.converged ? (ac.remaining_issues ?? []) : []
  }

  // K11 (ADR §5.1): the plain-language face of a design stop. Known trap ids
  // get their canonical one-liner from the locale; everything free-form
  // (warnings, validation / mapping issues, future trap ids) folds into ONE
  // count line — full technical text stays in the folded details AND in the
  // AI-fix input (plain words alone would strand weak models; the repair
  // recipes must keep flowing to the fix loop untranslated).
  function designPlainLines(
    failIds: string[],
    othersCount: number,
    incomplete: boolean,
  ): string[] {
    const known = new Set(['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8', 'T9', 'T10'])
    const out: string[] = []
    if (incomplete) out.push(t('kantan:s5.trap.incomplete'))
    let others = othersCount
    for (const id of failIds) {
      if (known.has(id)) out.push(t(`kantan:s5.trap.${id}`))
      else others += 1
    }
    if (others > 0) {
      // "このほか、" only reads right when something came before it.
      out.push(t(out.length > 0 ? 'kantan:s5.trap.others' : 'kantan:s5.trap.othersOnly', {
        count: others,
      }))
    }
    return out
  }

  /** The plain face of free-form validation / mapping issues.
   *
   *  These are NOT model prose: they come from the deterministic generators
   *  (`asterism.rml_validate`, `mapping_ir`), so their fixed phrases are stable
   *  enough to translate one by one — and they carry the single most common weak
   *  model failure (a column that isn't in the file), for which K11 already has
   *  a canonical sentence. Counting them all as "その他" hid exactly that.
   *  Unrecognised lines still fold into the count, so a new phrase degrades to
   *  the old behaviour rather than to a wrong sentence. `fixLines` keeps the raw
   *  English — display and AI input stay separate (ADR §5.1). */
  function issuePlainLines(issues: string[]): string[] {
    const markers: { marker: string; key: string }[] = [
      { marker: 'referenced by the mapping is not in', key: 'kantan:s5.trap.T8' }, // rml_validate
      { marker: "' is not in ", key: 'kantan:s5.trap.T8' }, // mapping_ir compile
      { marker: 'does not accept parameter', key: 'kantan:s5.trap.function' },
      { marker: 'is missing required parameter', key: 'kantan:s5.trap.function' },
      { marker: 'referenced by rml:source does not exist', key: 'kantan:s5.trap.source' },
      { marker: 'no compiled RML mapping', key: 'kantan:s5.trap.uncompiled' },
      { marker: 'is not parseable YAML', key: 'kantan:s5.trap.mieBroken' },
    ]
    const out: string[] = []
    const seen = new Set<string>()
    let others = 0
    for (const issue of issues) {
      const hit = markers.find((m) => issue.includes(m.marker))
      if (!hit) {
        others += 1
        continue
      }
      if (seen.has(hit.key)) continue // one sentence per family, not per line
      seen.add(hit.key)
      out.push(t(hit.key))
    }
    if (others > 0) {
      out.push(t(out.length > 0 ? 'kantan:s5.trap.others' : 'kantan:s5.trap.othersOnly', {
        count: others,
      }))
    }
    return out
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

  /** `restored` = these files came back from this browser's own store for the
   *  snapshot that is being resumed, so they ARE the files the S2 answers were
   *  given for; a human pick is a fresh start and drops everything downstream. */
  function onFilesChosen(list: FileList | File[] | null, opts?: { restored?: boolean }) {
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
    if (!opts?.restored) {
      userActedRef.current = true
      setResumed(false) // the resume banner has done its job
    }
    setPendingRestore(null)
    setPickError('')
    setErrMsg('')
    setJobNotice('')
    setInspectErr('')
    setFiles(arr)
    // Remember WHICH files this design is being written against: days later,
    // "drop the same file again" is only answerable if we can name it.
    setSourceNames(arr.map((f) => ({ name: f.name, size: f.size })))
    void saveSourceFiles(arr) // survive a reload (sessionStorage cannot hold a File)
    // And give them a server-side home right away (ADR source-staging.md).
    // Later calls prefer the id; until it lands (or if it never does) they
    // upload the files as before, so nothing waits on this.
    stageSources(arr)
      .then((r) => setStagingId(r.stagingId))
      .catch((e) => {
        setStagingId(null)
        // A closed write gate reaches us here first — say so now rather than
        // after two AI rounds (KZ-A-42).
        if (e instanceof ApiError && (e.status === 401 || e.status === 403 || e.status === 503)) {
          setWriteGate('token_required')
        }
      })

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

    setKind(k)
    if (opts?.restored) {
      // Resuming: keep the two answers only this person could give (K2/K13 —
      // never ask the same question twice), re-read the files, land on S2.
      setKeptAnswers(q1 !== null || q2 !== null)
      void runInspect(arr)
      return
    }
    // A fresh (or different) file set: drop everything downstream and inspect.
    setQ1(null)
    setQ2(null)
    setKeptAnswers(false)
    setDialectOverrides({})
    setSkeleton(null)
    setAnnotations(null)
    setProposal('')
    setInspectionMd('')
    resetPipelineState()
    void runInspect(arr)
  }

  /** Whether the registered draft in hand was minted by THIS run — the only
   *  case in which "start over" may remove it. A dataset opened for review
   *  (見直す) or already published belongs to the catalog, not to this run. */
  function ownsThrowawayDraft(): boolean {
    return !!kzDatasetId && !redesigning && !published
  }

  /** Drop every S5-S9 leftover when a fresh design starts. */
  function resetPipelineState() {
    setKzDatasetId(null)
    setKzDatasetName(null)
    setGateSkeleton(null)
    setSourceColumns([])
    setReturnedFromDetail(false)
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
    setCoverageDropped(false)
    setIngestProgress(null)
    setTrial(null)
    setTrialErr('')
    setAlignment(null)
    setPubName('')
    setPubErr('')
    setPublished(false)
    setRedesigning(false)
    setReingested(true)
  }

  async function runInspect(arr: File[]) {
    setInspecting(true)
    setInspectErr('')
    try {
      const result = await inspectCsvs(arr, [])
      setInspection(result)
      setInspectionMd(result.markdown)
      const cards = await buildPreviews(arr, result, dialectOverrides)
      setPreviews(cards)
      setColumnSamples(deriveColumnSamples(cards))
      setSourceColumns(deriveSourceColumns(cards))
      setStep(2)
    } catch (e) {
      setInspectErr(e instanceof Error ? e.message : String(e))
    } finally {
      setInspecting(false)
    }
  }

  /** Keep the file this browser still holds (the resume choice, RESUME-11). */
  function usePendingRestore() {
    const arr = pendingRestore
    if (!arr) return
    setPendingRestore(null)
    onFilesChosen(arr, { restored: true })
  }

  /** Let it go and start with a new file — the store is cleared so the next
   *  visit does not offer it again. */
  function dropPendingRestore() {
    setPendingRestore(null)
    void clearSourceFiles()
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
    // preamble metadata onto every row (in the SHAPE the inspector detected —
    // `key: value` lines vs `key=value` cells vs bare lines; hardcoding
    // 'keyvalue' collapsed a ZEM-style tab meta line into one giant column);
    // drop = table only. MERGED into whatever the read-fix panel already set,
    // or answering this question would silently undo those corrections.
    setDialectOverrides((prev) => {
      const next: Record<string, SourceDialect> = { ...prev }
      for (const [name, det] of preambleSources) {
        const base = prev[name] ?? {
          encoding: det.encoding,
          delimiter: det.delimiter,
          collapse: det.collapse,
          skip_rows: det.skip_rows,
          preamble: 'drop',
        }
        // The read-fix panel may have set "no rows before the table" since:
        // keeping a preamble that is no longer there is refused server-side.
        const keep = a === 'keep' && base.skip_rows > 0
        next[name] = { ...base, preamble: keep ? (det.preamble_hint ?? 'keyvalue') : 'drop' }
      }
      return next
    })
  }

  /** The read settings in force for one source: the human's correction, else
   *  what detection reported, else the plain defaults. */
  function dialectFor(canonical: string): SourceDialect {
    const override = dialectOverrides[canonical]
    if (override) return override
    const det = inspection?.dialects[canonical]
    if (det) {
      return {
        encoding: det.encoding,
        delimiter: det.delimiter,
        collapse: det.collapse,
        skip_rows: det.skip_rows,
        preamble: det.preamble ?? 'drop',
      }
    }
    return defaultDialect(canonical)
  }

  /** S2 "表が正しく読めていない？": a correction the person makes by eye —
   *  column separator, how many rows come before the table, which reading to
   *  open the characters with. It rewrites the preview on the spot (no server
   *  call) and rides the existing per-source overrides into the design
   *  (KZ-A-10 / DETAIL-GAP-13). */
  function applyDialectFix(canonical: string, patch: Partial<SourceDialect>) {
    const merged = { ...dialectFor(canonical), ...patch }
    // No preamble rows means no preamble handling — the server rejects the
    // combination, and leaving a stale 'keyvalue' there would 422 the design.
    if (merged.skip_rows === 0) merged.preamble = 'drop'
    const next = { ...dialectOverrides, [canonical]: merged }
    setDialectOverrides(next)
    if (!inspection || files.length === 0) return
    void buildPreviews(files, inspection, next).then((cards) => {
      setPreviews(cards)
      setColumnSamples(deriveColumnSamples(cards))
      setSourceColumns(deriveSourceColumns(cards))
    })
  }

  function onProceed() {
    if (!isReady || !hasSource || writeBlocked) return
    setResumed(false)
    setStep(3)
    void runSkeleton()
  }

  function backToPick() {
    setFiles([])
    void clearSourceFiles()
    if (stagingId) void unstageSources(stagingId)
    setStagingId(null)
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

  // `rethinkNote` = the S4 "AI にもう一度考えさせる" note: a plain-language
  // instruction (e.g. 「試料と測定値を別の種類に分けて」) folded into the
  // domain hint, so the regeneration actually hears the human's objection —
  // same generic human-hint channel the preset hints ride.
  async function runSkeleton(rethinkNote?: string) {
    setErrMsg('')
    setJobNotice('')
    setStatus('')
    setSkeletonBusy(true)
    setLastPulseAt(null)
    jobRef.current?.close()
    const domain = [
      composedDomain(),
      rethinkNote ? t('kantan:s4.rethinkWrap', { note: rethinkNote }) : '',
    ]
      .filter(Boolean)
      .join('\n')
    try {
      jobRef.current = await proposeSkeleton(
        files,
        domain,
        [],
        getActiveCredentials(),
        {
          onStart: (jobId) => saveKantanJob({ jobId, kind: 'skeleton' }),
          onStatus: (m) => setStatus(plainStatus(m)),
          onPulse: () => setLastPulseAt(Date.now()),
          onDone: (result) => {
            setSkeleton(result.skeleton)
            setAnnotations(result.annotations ?? null)
            setInspectionMd(result.inspection_md)
            setStatus('')
            setSkeletonBusy(false)
            clearKantanJob()
            setStep(4)
          },
          onError: (m) => {
            setErrMsg(m)
            setStatus('')
            setSkeletonBusy(false)
            clearKantanJob()
          },
          onCancelled: () => {
            setJobNotice(t('workbench:job.cancelled'))
            setStatus('')
            setSkeletonBusy(false)
            clearKantanJob()
            setStep(2)
          },
        },
        i18n.language,
        dialectOverrides,
        stagingId,
      )
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e))
      setSkeletonBusy(false)
      clearKantanJob()
    }
  }

  // ---- S4: the row-counting gate → continue ----------------------------------

  // A human edit re-checks the evidence server-side (no LLM) after a short
  // debounce — same contract as the detail tier's onSkeletonEdited.
  function onSkeletonEdited(edited: MappingSkeleton) {
    setSkeleton(edited)
    if (revalidateTimer.current !== null) window.clearTimeout(revalidateTimer.current)
    if (!hasSource) return // nothing to check against (gate shows a hint)
    revalidateTimer.current = window.setTimeout(async () => {
      setAnnotationsBusy(true)
      try {
        setAnnotations(await validateSkeleton(files, edited, dialectOverrides, stagingId))
      } catch {
        // Evidence is enrichment — a failed re-check never blocks editing.
      } finally {
        setAnnotationsBusy(false)
      }
    }, 700)
  }

  async function runContinue() {
    if (!skeleton) return
    if (!hasSource) {
      setErrMsg(t('kantan:s4.needFiles'))
      return
    }
    // The human just settled the row counting: keep that gate in hand so S6/S7
    // can come back to it if the counts turn out wrong (KZ-B-03).
    setGateSkeleton(skeleton)
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
            const remaining = adoptAutocorrect(result)
            setProposal(result.proposal_md)
            if (remaining.length > 0) {
              // The machine already knows these are unresolved: say so here
              // rather than letting the run reach the 422 at ingest and show a
              // bare count (WEAK-MODEL-13).
              setStopFromRemaining(remaining)
              setStep(5)
              return
            }
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
        stagingId,
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
    setResumed(false)
    setReturnedFromDetail(false)
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
        // A draft the user can recognise in the catalog list: the literal name
        // "dataset" made every abandoned run indistinguishable (KZ-A-28).
        const draftName = kzDatasetName ?? defaultDraftName(fs)
        try {
          try {
            result = await materializeSchema(md, draftName, datasetId ?? undefined)
          } catch (e) {
            // The adopted record vanished (deleted in the catalog meanwhile) —
            // recreate once instead of dead-ending the chain on a stale id.
            if (!datasetId || !(e instanceof ApiError) || e.status !== 404) throw e
            datasetId = null
            attached = false
            setKzDatasetId(null)
            setSourceAttached(false)
            result = await materializeSchema(md, draftName)
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
        const allFails = result.traps.filter((tr) => tr.status === 'fail')
        // Traps about the DOCUMENT around the design (its diagram, its worked
        // examples, its rationale, its example searches) say nothing about
        // whether the data can be read correctly. A weak model trips them
        // constantly, and stopping the whole run there hands the human an AI
        // round they cannot influence. They are still shown — as a weakness the
        // human may wave through — and still recorded as failing checks (K11).
        const docFails = allFails.filter((tr) => DOC_ONLY_TRAPS.has(tr.id))
        const hardFails = allFails.filter((tr) => !DOC_ONLY_TRAPS.has(tr.id))
        const hardStop =
          !result.complete ||
          (result.validation_issues ?? []).length > 0 ||
          hardFails.length > 0 ||
          (result.exit_code !== 0 && allFails.length === 0)
        if (hardStop) {
          // (weaknesses are handled separately below — they do not dead-end)
          // Problems the self-correction could not clear (truncated output /
          // failing traps): a human decision now — the card's PRIMARY exit is
          // the same one-click AI fix the detail tier has. Each failing trap
          // ships its deterministic repair recipe (`fix`): hand it to the AI
          // grouped with its symptom, like the detail tier's composeFixComment
          // (symptom-only comments loop weak models forever). The api merges
          // mapping_ir_issues into `validation_issues`, so appending that list
          // covers the mapping-spec compile problems too — and a non-empty
          // list STOPS the chain (a spec that did not compile leaves NO RML;
          // continuing would dead-end at ingest with an opaque "no declarative
          // RML mapping" error — the ZEM x gpt-oss live failure, 2026-07-23).
          const lines = [
            ...(result.complete ? [] : ['incomplete design output (truncated)']),
            ...allFails.map((tr) => {
              const head = `${tr.id} ${tr.name}: ${tr.detail}`
              return tr.fix ? `${head}\n  ↳ ${tr.fix.split('\n').join('\n    ')}` : head
            }),
            ...result.warnings,
            ...(result.validation_issues ?? []),
          ]
          // Trap ids get their canonical sentence; the free-form issues get the
          // deterministic-phrase classifier (so "a column that isn't in your
          // file" is named, not counted).
          const issueLines = issuePlainLines([
            ...result.warnings,
            ...(result.validation_issues ?? []),
          ])
          setDesignStop({
            kind: 'design',
            detail: lines.join('\n'),
            fixLines: lines,
            plainLines: [
              ...designPlainLines(
                allFails.map((tr) => tr.id),
                0,
                !result.complete,
              ),
              ...issueLines,
            ],
          })
          return
        }
        // The design is VALID but may be weak: entities with no link between
        // them, columns nobody mapped. Those must not be silently published (a
        // disconnected mapping answers no cross-entity question — the live ZEM
        // case, 2026-07-24) but they are also not defects, and a design can be
        // legitimately single-purpose. So: show it, and let the human choose —
        // "AI に直してもらう" or "このまま進む" (which resumes at attach).
        const advisories = result.advisories ?? []
        const docLines = docFails.map((tr) => {
          const head = `${tr.id} ${tr.name}: ${tr.detail}`
          return tr.fix ? `${head}\n  ↳ ${tr.fix.split('\n').join('\n    ')}` : head
        })
        if (advisories.length > 0 || docLines.length > 0) {
          setDesignStop({
            kind: 'weakness',
            detail: [...advisories, ...docLines].join('\n'),
            fixLines: [...advisories, ...docLines],
            // Same plain sentences the catalog shows — one vocabulary for the
            // same finding, wherever the user meets it. Labels come from the
            // dataset's own terms so the names match the S6 table (K4).
            plainLines: [
              ...plainAdvisories(advisories, rules?.labels).map((a) => a.text),
              ...designPlainLines(
                docFails.map((tr) => tr.id),
                0,
                false,
              ),
            ],
          })
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
        if (fs.length === 0 && !stagingId) {
          setStop({ kind: 'files', detail: '' })
          return
        }
        setPipePhase('save')
        try {
          // The staged copy is consumed here (it becomes the dataset's
          // source/); the files in this tab are the fallback.
          await attachSource(datasetId, fs, stagingId)
          setStagingId(null)
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
          setDesignStop({
            kind: 'design',
            detail: e.issues.join('\n'),
            fixLines: e.issues,
            plainLines: issuePlainLines(e.issues),
          })
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
      setReingested(true) // a staged draft now exists → 確定 leads to publish
      setStep(6)
      void loadS6(datasetId)
    } catch (e) {
      if (e instanceof IngestCancelledError || e instanceof StaleIngestJobError) {
        // Clean stop (user cancel) or a job id re-minted by an api restart —
        // nothing was committed; offer a clean resume of the same stage.
        setStop({ kind: 'interrupted', detail: '', retryFrom: 'ingest' })
      } else if (e instanceof IngestValidationError) {
        setDesignStop({
          kind: 'design',
          detail: e.issues.join('\n'),
          fixLines: e.issues,
          plainLines: issuePlainLines(e.issues),
        })
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

  // ---- S7: ためす — the automatic try-it-out queries (ADR K9) ----------------
  // Deterministic aggregates over the user's own draft, run for them (never a
  // button — first-timers don't press optional buttons). The screen is a soft
  // gate: its exits are "looks right → publish" and "something is off → back
  // to the column meanings".

  async function loadS7(datasetId: string) {
    setTrialLoading(true)
    setTrialErr('')
    try {
      setTrial(await fetchTrialQueries(datasetId))
    } catch (e) {
      setTrialErr(errText(e))
    } finally {
      setTrialLoading(false)
    }
  }

  // S6 確定 → straight into S7 with the queries already running.
  function confirmMeanings() {
    setResumed(false)
    setReturnedFromDetail(false)
    setConfirmed(true)
    setStep(7)
    if (kzDatasetId) void loadS7(kzDatasetId)
  }

  /** The S6 primary button. A typed-but-unsent note is a correction only the
   *  human could make — walking past it publishes the wrong meaning and nobody
   *  is ever told (KZ-B-33). */
  function onConfirmMeanings() {
    if (note.trim() !== '' && !window.confirm(t('kantan:s6.noteUnappliedConfirm'))) return
    if (redesigning && !reingested) {
      exitRedesign()
      return
    }
    confirmMeanings()
  }

  // The S7 "something is off" exit: back to the column-meaning review.
  function backToMeanings() {
    setConfirmed(false)
    setStep(6)
    if (kzDatasetId) void loadS6(kzDatasetId)
  }

  /** "行の数えかたに戻る" (S6 / S7): the counts card is where a collapsed key
   *  becomes visible (500 rows → 1 sample), and the place that decision was
   *  made is the S4 gate — so go back THERE with the same skeleton, instead of
   *  leaving "start over" as the only way out (KZ-B-03 / DETAIL-GAP-08). */
  function backToGate() {
    if (!gateSkeleton || !hasSource) return
    if (!window.confirm(t('kantan:s6.backToGateConfirm'))) return
    setStop(null)
    setConfirmed(false)
    setSkeleton(gateSkeleton)
    setAnnotations(null)
    setStep(4)
    setAnnotationsBusy(true)
    validateSkeleton(files, gateSkeleton, dialectOverrides, stagingId)
      .then(setAnnotations)
      .catch(() => {
        /* evidence is enrichment */
      })
      .finally(() => setAnnotationsBusy(false))
  }

  /** The name of one item, in this order: the reviewed IR label (K8) → the
   *  label the published terms carry → THE USER'S OWN COLUMN HEADING → the
   *  identifier made readable. A weak model routinely writes no label at all,
   *  and this tier must never answer with `hasSeebeckCoefficient` (K4) — the
   *  column the number came from is a name the reader already knows
   *  (KZ-B-07 / WEAK-MODEL-19). */
  function termLabel(iri: string, label?: string): string {
    if (label) return label
    const fromRules = rules?.labels?.[iri]
    if (fromRules) return fromRules
    const column = columnFor(iri)
    if (column) return column
    return humanizeLocal(localName(iri))
  }

  /** The source column one term was read from, per the dataset's own rules. */
  function columnFor(iri: string): string | null {
    for (const map of rules?.maps ?? []) {
      for (const p of map.properties) {
        if (p.predicate_iri === iri && p.kind === 'reference' && p.reference) return p.reference
      }
    }
    return null
  }

  /** The S7 cards and the S9 ask-prefill chips share this assembly: the
   *  deterministic numbers come from the API, the sentences from the locale.
   *  Labels prefer the reviewed IR label (K8), then fall back to the term's
   *  local name — never jargon, never an extra AI pass. */
  function buildTrialQAs(tr: TrialQueries): TrialQA[] {
    const lng = i18n.language
    const join = t('kantan:s7.join')
    const out: TrialQA[] = []
    const clsLabel = (c: { iri: string; label?: string }) => c.label ?? classLabel(c.iri)
    if (tr.classes.length > 0) {
      out.push({
        q:
          tr.classes.length === 1
            ? t('kantan:s7.qCountOne', { label: clsLabel(tr.classes[0]) })
            : t('kantan:s7.qCountMany'),
        a: tr.classes
          .map((c) => t('kantan:s7.aCount', { label: clsLabel(c), n: c.n.toLocaleString(lng) }))
          .join(join),
        sparql: tr.count_sparql ?? undefined,
      })
    } else if (tr.entities) {
      // No declared kinds (a legal shape) → the plain record count.
      out.push({
        q: t('kantan:s7.qCountAny'),
        a: t('kantan:s7.aCountAny', { n: tr.entities.n.toLocaleString(lng) }),
        sparql: tr.entities.sparql,
      })
    }
    if (tr.range) {
      out.push({
        q: t('kantan:s7.qRange', {
          label: termLabel(tr.range.predicate_iri, tr.range.label),
        }),
        a: t('kantan:s7.aRange', {
          min: formatNum(tr.range.min, lng),
          max: formatNum(tr.range.max, lng),
          unit: tr.range.unit ? ` ${tr.range.unit}` : '',
        }),
        sparql: tr.range.sparql,
      })
    }
    if (tr.top) {
      // Context values go through the same locale formatting as the answer —
      // formatNum leaves non-numeric strings (e.g. a sample name) untouched.
      const context = tr.top.subject_details
        .slice(0, 2)
        .map(
          (d) =>
            `${termLabel(d.predicate_iri, d.label)}: ${formatNum(d.value, lng)}${
              d.unit ? ` ${d.unit}` : ''
            }`,
        )
        .join(join)
      out.push({
        q: t('kantan:s7.qTop', { label: termLabel(tr.top.predicate_iri, tr.top.label) }),
        a:
          formatNum(tr.top.value, lng) +
          (tr.top.unit ? ` ${tr.top.unit}` : '') +
          (context ? t('kantan:s7.aTopContext', { context }) : ''),
        citeIri: tr.top.subject_iri,
        citeDetails: tr.top.subject_details,
        sparql: tr.top.sparql,
      })
    }
    if (tr.samples) {
      const label =
        tr.samples.label ?? (tr.samples.class_iri ? classLabel(tr.samples.class_iri) : null)
      out.push({
        q: label ? t('kantan:s7.qSamples', { label }) : t('kantan:s7.qSamplesAny'),
        a: tr.samples.iris.map((iri) => localName(iri)).join(join),
        citeIri: tr.samples.iris[0],
        sparql: tr.samples.sparql,
      })
    }
    return out
  }

  // ---- S8: 公開する — name + counts + word summary + promote (gate ③, K10) ---

  /** The name to offer at S8. K13 says a person names this data ONCE: they
   *  already did, at the row-counting gate ("データセットの名前"). Reuse it
   *  rather than opening an empty box and asking the same thing again
   *  (KZ-B-12); it stays editable. */
  function derivePublishName(): string {
    const fromGate = gateSkeleton ? detectDatasetNamespace(gateSkeleton)?.slug : null
    const fromRules = Object.values(rules?.prefixes ?? {})
      .map((iri) => /\/datasets\/([^/#?]+)\/(?:ontology#|resource\/)$/.exec(iri)?.[1])
      .find((slug): slug is string => !!slug)
    return fromGate ?? fromRules ?? draftStem(files)
  }

  function goPublish() {
    setStep(8)
    if (!pubName.trim()) setPubName(derivePublishName())
    if (kzDatasetId) void loadS8(kzDatasetId)
  }

  async function loadS8(datasetId: string) {
    setS8Loading(true)
    setPubErr('')
    if (!pubName.trim()) setPubName(derivePublishName())
    // Display material only: the counts card may already be loaded (S6), and
    // the word summary is enrichment — its absence never blocks publishing.
    const [s, a] = await Promise.all([
      stats ? Promise.resolve(stats) : fetchDraftStats(datasetId).catch(() => null),
      getAlignment(datasetId).catch(() => null),
    ])
    if (s) setStats(s)
    setAlignment(a)
    setS8Loading(false)
  }

  async function runPublish() {
    const name = pubName.trim()
    if (!kzDatasetId || !name || publishing) return
    setPublishing(true)
    setPubErr('')
    try {
      // The publish name is part of the publish act (the auto chain registered
      // the draft under a throwaway name): rename first so the public catalog
      // card carries the human-chosen name.
      if (name !== kzDatasetName) {
        await renameDataset(kzDatasetId, name)
        setKzDatasetName(name)
      }
      const res = await promoteDataset(kzDatasetId)
      setAlignment(res.alignment)
      setPublished(true)
      setStep(9)
    } catch (e) {
      setPubErr(errText(e))
    } finally {
      setPublishing(false)
    }
  }

  // ---- S9: できあがり — ask chips + the grow-the-dataset exits ----------------

  // Both grow exits land on the catalog's ファイル tab, where the append and
  // re-ingest controls live — `focus` carries WHICH of the two the person
  // asked for, so the catalog can open on that control instead of a wall of
  // both (KZ-B-02). The wizard's run is complete — drop its snapshot AND the
  // file this browser was holding, or the next "add data" would silently read
  // the published file again and register it twice (RESUME-11).
  function openGrow(tab?: DetailTab, focus?: GrowFocus) {
    if (!kzDatasetId) return
    try {
      sessionStorage.removeItem(KZ_STORAGE)
    } catch {
      /* non-fatal */
    }
    void clearSourceFiles()
    onOpenDataset?.(kzDatasetId, tab, focus)
  }

  // ---- かんたん見直し (catalog 見直す → the S6 re-check flow) -----------------

  /** Leave the review and land back on the dataset's catalog page. Nothing is
   *  lost: refines (if any) were saved server-side at materialize. Used by the
   *  no-change confirm AND the banner's やめる. */
  function exitRedesign() {
    const id = kzDatasetId
    try {
      sessionStorage.removeItem(KZ_STORAGE)
    } catch {
      /* non-fatal */
    }
    resetWizardToStart()
    if (id) onOpenDataset?.(id)
  }

  function cancelRedesign() {
    if (!window.confirm(t('kantan:redesign.cancelConfirm'))) return
    exitRedesign()
  }

  // "構造から見直す": hand the CURRENT (possibly refined) design to the detail
  // tier as a proper redesign target — same consumption path as the catalog's
  // 見直す, so a later save updates THIS dataset, never a duplicate.
  function openStructural() {
    if (!kzDatasetId || !proposal) return
    try {
      sessionStorage.removeItem(KZ_STORAGE)
    } catch {
      /* non-fatal */
    }
    onRedesignDetail?.({
      datasetId: kzDatasetId,
      datasetName: kzDatasetName ?? '',
      proposalMd: proposal,
    })
  }

  /** Land a finished refine — shared by the live chain and the resumed one, so
   *  a refine that outlived a tab switch continues exactly as it would have. */
  function finishRefine(result: unknown) {
    // The server already guards truncation: `refined_md` is the raw answer,
    // `effective_schema_md` is what is safe to build on (the PREVIOUS complete
    // design when the answer was cut off). The UI used to overwrite its design
    // with the cut-off text and materialize it, so the next "have the AI fix
    // it" fed the AI an ever shorter document (WEAK-MODEL-16).
    const r = result as RefineResult & {
      complete?: boolean
      effective_schema_md?: string
    }
    const effective = r.effective_schema_md || r.refined_md
    setStatus('')
    setRefining(false)
    if (r.complete === false) {
      setStop({ kind: 'refineTruncated', detail: '' })
      setStep(5)
      return
    }
    setNote('')
    setRules(null)
    setStats(null)
    setProposal(effective)
    void runPipeline('materialize', effective)
  }

  /** Where a RESUMED refine leaves the screen when it does not land. The
   *  restore came back as step 5 (its progress), so a failure there would
   *  otherwise spin forever on a chain that is no longer running: the reflect
   *  belongs back on the review screen, and a fix with no card to restore gets
   *  the plain "it was interrupted" one (KZ-A-30). */
  function landAfterResumedRefine(mode: 'note' | 'fix', restoreStop?: StopCard) {
    if (mode === 'note') {
      setStep(6)
      if (kzDatasetId) void loadS6(kzDatasetId)
      return
    }
    if (!restoreStop) setStop({ kind: 'interrupted', detail: '', retryFrom: 'materialize' })
  }

  function failRefine(message: string, mode: 'note' | 'fix', restoreStop?: StopCard) {
    if (mode === 'fix') {
      setFixErr(message)
      if (restoreStop) setStop(restoreStop) // back to the same stop card
    } else {
      setRefineErr(message)
    }
    setStatus('')
    setRefining(false)
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
    lastRefineRef.current = { comments, mode, restore: restoreStop }
    const fail = (message: string) => failRefine(message, mode, restoreStop)
    try {
      // Persisted under kantan's OWN key (never JOB_STORAGE — the detail tier
      // resumes that one as a 'propose'): 1-3 minutes of AI work must survive
      // leaving the screen, exactly like the S3 skeleton job (KZ-A-30).
      jobRef.current = await refineSchema(
        proposal,
        comments,
        getActiveCredentials(),
        {
          onStart: (jobId) =>
            saveKantanJob({ jobId, kind: 'refine', mode, restore: restoreStop ?? null }),
          onStatus: (m) => setStatus(plainStatus(m)),
          onPulse: () => setLastPulseAt(Date.now()),
          onDone: (result) => {
            clearKantanJob()
            finishRefine(result)
          },
          onError: (m) => {
            clearKantanJob()
            fail(m)
          },
          onCancelled: () => {
            clearKantanJob()
            setJobNotice(t('workbench:job.cancelled'))
            if (mode === 'fix' && restoreStop) setStop(restoreStop)
            setStatus('')
            setRefining(false)
          },
        },
        i18n.language,
      )
    } catch (e) {
      clearKantanJob()
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

  // "AI に直してもらう" (S5 design/weakness stop): the same one-click fix the
  // detail tier has — the card's failure lines (trap details + repair recipes +
  // warnings + validation/mapping issues; for a weakness card the raw
  // advisories) become the corrective refine comment, then the refined design
  // re-runs the auto chain from materialize.
  /** Fix the carried findings — the review screen's counterpart of the stop
   *  card's AI fix. The AI gets the RAW advisories (they already spell out the
   *  join keys and which side must declare the link); the screen shows the
   *  plain sentences. No stop card to restore: a failure surfaces as fixErr. */
  function fixCarriedAdvisories() {
    if (carriedAdvisories.length === 0 || !proposal || pipeBusy || refining) return
    const comment = `${t('workbench:fix.commentIntro')}\n${carriedAdvisories
      .map((l) => `- ${l}`)
      .join('\n')}`
    setAiFixCount((c) => c + 1)
    setCarriedAdvisories([])
    void startRefineChain([comment], 'fix')
  }

  /** Which stop cards get the one-click AI fix. Beyond the two dedicated kinds,
   *  an HTTP-error card whose plain body SAYS "try 『AI に直してもらう』" must
   *  actually have that button — otherwise the text points at nothing (KZ-A-20). */
  function stopAllowsAiFix(card: StopCard | null): boolean {
    if (!card) return false
    if (card.kind === 'design' || card.kind === 'weakness') return true
    if (card.kind !== 'materialize' && card.kind !== 'attach' && card.kind !== 'ingest') return false
    return plainError(card.detail).hint === 'fix'
  }

  function runAiFix() {
    if (!stop || !stopAllowsAiFix(stop) || !proposal || pipeBusy) return
    const card = stop
    const lines = card.fixLines?.length ? card.fixLines : card.detail ? [card.detail] : []
    const comment = `${t('workbench:fix.commentIntro')}\n${lines.map((l) => `- ${l}`).join('\n')}`
    setAiFixCount((c) => c + 1)
    setStop(null)
    void startRefineChain([comment], 'fix', card)
  }

  /** The truncated-refine card's two exits: ask again with the same request, or
   *  go on with the design as it stood before the (lost) fix. */
  function retryLastRefine() {
    const last = lastRefineRef.current
    if (!last || refining) return
    setStop(null)
    if (last.mode === 'fix') setAiFixCount((c) => c + 1)
    void startRefineChain(last.comments, last.mode, last.restore)
  }

  function continueWithoutFix() {
    setStop(null)
    void runPipeline('materialize', proposal)
  }

  function openDetail() {
    // The WB_STORAGE handoff snapshot is already written (effect above). The
    // kantan snapshot STAYS — switching tiers is a look, not a goodbye: coming
    // back must reopen the same screen rather than an empty drop zone that
    // reads as "everything is gone" (DETAIL-GAP-04). The flag is written
    // synchronously (this component unmounts on the very next render, so a
    // state update would never reach the persist effect).
    try {
      const raw = sessionStorage.getItem(KZ_STORAGE)
      const current = raw ? (JSON.parse(raw) as Partial<KantanSnapshot>) : {}
      sessionStorage.setItem(KZ_STORAGE, JSON.stringify({ ...current, handedToDetail: true }))
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
    // The design was written for ONE kind of file. A PDF dropped here used to
    // sail through and die inside the ingest as an English error whose real
    // cause (wrong kind of file) was never named — a machine check, not a
    // judgement to push onto the reader (RESUME-18).
    const dropped = [...kinds][0] as KantanKind
    if (kind && dropped !== kind) {
      setPickError(t(`kantan:s5.stop.wrongKind.${kind}`))
      return
    }
    // Days later nobody remembers which file this was. If the names differ,
    // say so and let the person decide — the design is written against the
    // columns of the ORIGINAL file (RESUME-17).
    if (sourceNames.length > 0) {
      const before = sourceNames.map((f) => f.name).join('、')
      const after = arr.map((f) => f.name).join('、')
      if (before !== after) {
        const ok = window.confirm(t('kantan:s5.stop.filesDifferentConfirm', { before, after }))
        if (!ok) return
      }
    }
    setPickError('')
    userActedRef.current = true
    setFiles(arr)
    setSourceNames(arr.map((f) => ({ name: f.name, size: f.size })))
    void saveSourceFiles(arr)
    stageSources(arr)
      .then((r) => setStagingId(r.stagingId))
      .catch(() => setStagingId(null))
    void runPipeline('attach', undefined, arr)
  }

  // The full component-state wipe shared by the completion-card "新しいデータを
  // 追加する" (startFresh) and the stop-card / recipe-① "最初からやり直す"
  // (doRestart). Only component state — callers own sessionStorage + the confirm.
  function resetWizardToStart() {
    setFiles([])
    void clearSourceFiles()
    if (stagingId) void unstageSources(stagingId)
    setStagingId(null)
    setKind(null)
    setSourceNames([])
    setPendingRestore(null)
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
    // Re-arm the redesign seed: a LATER 見直す on the same dataset must seed
    // again (the id-equality guard would otherwise swallow it).
    setSeededRedesign(null)
    setStep(1)
  }

  // Completion-card escape hatch: begin a brand-new design without leaving the
  // wizard (the registered draft stays in the catalog untouched). Only the
  // kantan snapshot is dropped — the detail-tier handoff stays available.
  function startFresh() {
    try {
      sessionStorage.removeItem(KZ_STORAGE)
    } catch {
      /* non-fatal */
    }
    resetWizardToStart()
  }

  // The #9 escape hatch proper: from ANY stop card (or recipe ①) wipe every
  // persisted trace — this tier's snapshot, the detail-tier handoff, and any
  // saved propose/ingest job — detach a still-open stream, and drop back to S1.
  // The one exit that always works when a run wedges. Callers gate it with a
  // confirm (it discards the dropped files and every result so far).
  function doRestart() {
    jobRef.current?.close()
    const saved = loadIngestJob()
    if (saved) clearIngestJob(saved.jobId)
    // "すべて消して" must be true: the draft THIS run registered is deleted too,
    // or the catalog fills with half-finished records nobody can identify
    // (KZ-A-28). A draft that was already published, or one this run only
    // reopened for review, is never touched.
    if (kzDatasetId && ownsThrowawayDraft()) {
      void deleteDataset(kzDatasetId).catch(() => {
        /* best-effort cleanup — never block the escape hatch */
      })
    }
    try {
      sessionStorage.removeItem(KZ_STORAGE)
      sessionStorage.removeItem(WB_STORAGE)
      sessionStorage.removeItem(JOB_STORAGE)
      sessionStorage.removeItem(KZ_JOB_STORAGE)
    } catch {
      /* non-fatal */
    }
    resetWizardToStart()
  }

  function restartFromScratch() {
    // The confirm says what actually happens: the draft is removed when this
    // run created it, and stays listed when it does not belong to this run.
    const message =
      kzDatasetId && !ownsThrowawayDraft()
        ? t('kantan:s5.stop.restartConfirmKeep', { name: kzDatasetName ?? '' })
        : t('kantan:s5.stop.restartConfirm')
    if (!window.confirm(message)) return
    doRestart()
  }

  // Recipe ① click (#9): the guaranteed way back to the drop zone. Confirm
  // before discarding in-flight work or any result so far (a superset of "a job
  // is running"). ②+ are inert (RecipeCard renders only ① as a button).
  function onRecipeStep(target: 1 | 2 | 3 | 4 | 5) {
    if (target !== 1 || step === 1) return
    const dirty = busy || hasSource || !!proposal || !!skeleton || !!kzDatasetId
    if (dirty && !window.confirm(t('kantan:s5.stop.restartConfirm'))) return
    doRestart()
  }

  // ---- render -----------------------------------------------------------------

  // Recipe position: ①②③ as before; S7 = ④ ためす, S8/S9 = ⑤ 公開する
  // (S9 renders ⑤ as done — the run is complete).
  const recipePos: 1 | 2 | 3 | 4 | 5 =
    step <= 2 ? 1 : step === 3 ? 2 : step <= 6 ? 3 : step === 7 ? 4 : 5
  const resumeAvailable = !!skeleton && !hasSource && !proposal && step === 1
  const showS5 = pipeBusy || refining !== false || step === 5

  // S7 cards / S9 chips: assembled sentences over the deterministic results.
  const trialQAs = trial?.available ? buildTrialQAs(trial) : []
  const trialFailed = !trialLoading && (!!trialErr || (trial !== null && !trial.available))
  // The questions ran and found NOTHING: an empty draft (KZ-B-33).
  const trialEmpty = !trialLoading && !!trial?.available && trialQAs.length === 0
  // One published record to hand to someone else (S9's "show this to people").
  const shareIri = published ? (trialQAs.find((qa) => qa.citeIri)?.citeIri ?? null) : null
  // "Back to the row counting" is offered only when it can actually re-run:
  // the confirmed gate in hand, the source still readable, and not in a
  // catalog review (whose structural rework lives in the detail tier).
  const canBackToGate = !!gateSkeleton && hasSource && !redesigning && !busy
  // S8: word summary (structural terms are plumbing, not words) + plain error.
  const words = alignment ? alignmentWordSplit(alignment) : null
  const pubPlain = pubErr ? plainError(pubErr) : null

  // Stop-card plain-language translation (#7): only the HTTP-error kinds carry a
  // raw technical detail worth translating. The design / files / interrupted
  // kinds keep their own dedicated bodies + buttons (design's runAiFix stays).
  const stopPlain =
    stop && (stop.kind === 'materialize' || stop.kind === 'attach' || stop.kind === 'ingest')
      ? plainError(stop.detail)
      : null
  const stopHint = stopPlain?.hint
  const aiFixable = stopAllowsAiFix(stop)
  // Whether a more specific primary already exists — retry is then demoted to a
  // secondary (ghost) button so a card never shows two filled CTAs.
  const stopPrimaryElsewhere =
    !!stop &&
    (aiFixable ||
      stop.kind === 'refineTruncated' ||
      stopHint === 'settings' ||
      stopHint === 'restart')
  // The weakness card's headline counts what it is about to list, and its body
  // only claims "questions across things" when a disconnect is actually there.
  const weaknessCount = stop?.plainLines?.length ?? 0
  const weaknessDisconnected = (stop?.fixLines ?? []).some((l) => l.includes('DISCONNECTED groups'))
  // Only the settings key-holder can act on "check the AI setup" (ADR K5: the
  // model is not this tier's decision when the administrator owns the key).
  const canOpenAiSettings = !activeUsesServerKey

  // The S3/S4 job failure, in the same plain vocabulary as the S5 stop card.
  const jobPlain = errMsg ? plainError(errMsg) : null
  // …and the very first one, on the drop screen.
  const inspectPlain = inspectErr ? plainError(inspectErr) : null

  // S2's read-fix panel. Only files this browser could parse can be corrected
  // here (a preview is the whole point), and the panel only appears when the
  // automatic read LOOKS wrong: one giant column, ragged rows, replacement
  // characters, or a preamble that had to be detected. A clean CSV keeps the
  // screen as short as it is today (K13 — never ask what is already settled).
  const fixableCards = previews.filter((p) => p.header !== null)
  const previewSuspect = fixableCards.some((p) => {
    const header = p.header ?? []
    if (header.length <= 1) return true
    if (p.rows.some((r) => r.length !== header.length)) return true
    const garbled = (cells: string[]) => cells.some((c) => c.includes('�'))
    return garbled(header) || p.rows.some(garbled)
  })
  const showReadFix = fixableCards.length > 0 && (previewSuspect || q1Needed)

  const up = ingestProgress
  const uploadPct =
    up?.phase === 'upload' && up.total ? Math.floor((100 * (up.done ?? 0)) / up.total) : null

  const s6Maps = rules?.maps ?? []
  const multiMap = s6Maps.length > 1
  const linkRows = s6Maps.flatMap((m) =>
    m.properties.filter((p) => p.kind !== 'reference').map((p) => ({ map: m, prop: p })),
  )
  const totalSourceRows = Object.values(stats?.source_rows ?? {}).reduce((a, b) => a + b, 0)
  // Which columns of the file the design actually reads. "Reads" is more than
  // "has its own row in the table below": a column can also build an ID
  // (`{Sample name}` inside a template), feed a conversion, or be a join key —
  // counting only the plain ones would accuse the design of dropping columns
  // it does use (DETAIL-GAP-12).
  const mappedColumns = new Set<string>()
  for (const m of s6Maps) {
    const terms: RuleTerm[] = [m.subject, ...m.properties]
    while (terms.length > 0) {
      const term = terms.pop()!
      if (term.reference) mappedColumns.add(term.reference)
      for (const col of templateColumns(term.template)) mappedColumns.add(col)
      for (const cond of term.conditions ?? []) mappedColumns.add(cond.child)
      terms.push(...(term.args ?? []))
    }
  }
  // How many columns came back with no meaning at all (the ⚠ rows).
  const missingMeanings = s6Maps
    .flatMap((m) => m.properties.filter((p) => p.kind === 'reference'))
    .filter((p) => !(p.label || rules?.labels?.[p.predicate_iri])).length
  // …and the ones the design left out entirely. Only computable when S2 could
  // read the column list — with no list, nothing is said (never "nothing was
  // dropped" out of missing information).
  const droppedColumns =
    rules && sourceColumns.length > 0
      ? sourceColumns.filter((col) => !mappedColumns.has(col))
      : []
  // Plain faces of the two S6 failures (load / reflect) — same table as S5.
  const s6Plain = s6Err ? plainError(s6Err) : null
  const refinePlain = refineErr ? plainError(refineErr) : null

  // The name of one KIND of thing. Same deterministic ladder as termLabel,
  // minus the column step (a class has no single column) — an English
  // identifier is made readable rather than shown raw (KZ-B-06).
  function classLabel(iri: string): string {
    return rules?.labels?.[iri] ?? humanizeLocal(localName(iri))
  }

  function mapCaption(m: RuleMap): string {
    const iri = m.subject.class_iris?.[0]
    if (iri) return classLabel(iri)
    // No expanded IRI (a legacy projection): the shorthand and the map id are
    // both identifiers, so they are made readable rather than shown raw (K4).
    return humanizeLocal(localName(m.subject.classes?.[0] ?? m.id))
  }

  function otherKindKey(k: RuleProperty['kind']): string {
    return k === 'template' || k === 'constant' || k === 'join' || k === 'function' ? k : 'other'
  }

  return (
    <div className="kz-wizard">
      <RecipeCard current={recipePos} currentDone={step === 9} onStepClick={onRecipeStep} />

      {/* Coming back must be said out loud: without this the wizard silently
          lands on S4 (reads as "it lost my work") or shows an empty drop zone
          while it is still fetching (reads as "it is gone") — RESUME-01. */}
      {restoring && (
        <p className="kz-note kz-resume" role="status">
          <span className="spinner" />
          {t('kantan:s1.resumeLoading')}
        </p>
      )}
      {!restoring && resumed && !busy && step !== 9 && (
        <p className="kz-note kz-resume" role="status">
          {t('kantan:s1.resumeReady')}
        </p>
      )}
      {/* Back from the detail tier: the screen is the same one as before, and
          anything saved over there is what is being shown (RESUME-12). */}
      {returnedFromDetail && !busy && (
        <p className="kz-note kz-resume" role="status">
          {t('kantan:s1.detailReturnNote')}
        </p>
      )}

      {/* Nothing on this install can be saved: say it at the door, not after
          two AI rounds (KZ-A-42). */}
      {writeBlocked && (
        <section className="kz-card kz-warn" role="status">
          <h3 className="kz-title">
            {t(writeGate === 'closed' ? 'kantan:s1.writeClosedTitle' : 'kantan:s1.writeTokenTitle')}
          </h3>
          <p className="kz-note">
            {t(writeGate === 'closed' ? 'kantan:s1.writeClosedBody' : 'kantan:s1.writeTokenBody')}
          </p>
          {writeGate === 'token_required' && (
            <div className="kz-actions">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => openSettings('server-token')}
              >
                {t('kantan:s1.openSettings')}
              </button>
            </div>
          )}
        </section>
      )}

      {/* かんたん見直し: what is being reviewed + the two escape hatches
          (structural rework in detail mode / stop reviewing). Hidden on stop
          cards (they carry their own detail-mode exit) and after publish. */}
      {redesigning && !stop && !showS5 && step >= 6 && step <= 8 && (
        <section className="kz-card kz-redesign" role="note">
          <div className="kz-redesign-row">
            <span className="kz-redesign-name">
              {t('kantan:redesign.banner', { name: kzDatasetName ?? '' })}
            </span>
            <span className="kz-redesign-actions">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={openStructural}
                disabled={busy || !proposal}
              >
                {t('kantan:redesign.structural')}
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={cancelRedesign}
                disabled={busy}
              >
                {t('kantan:redesign.cancel')}
              </button>
            </span>
          </div>
          <p className="kz-note">{t('kantan:redesign.bannerNote')}</p>
        </section>
      )}

      {/* What sent the user here. Without this the review opens on the
          column-meanings table and the finding is gone — the reviewer is left
          to guess what to type into the free-text box (user feedback,
          2026-07-24). Plain sentences + one button that hands the AI the raw,
          already-actionable advisory text. */}
      {redesigning && !stop && !showS5 && refining === false && carriedAdvisories.length > 0 && (
        <section className="kz-card kz-carried" role="note">
          <h3 className="kz-title">{t('kantan:redesign.findingsTitle')}</h3>
          <ul className="kz-stop-plainlist">
            {/* Names under the same labels the S6 table uses (K4). */}
            {plainAdvisories(carriedAdvisories, rules?.labels).map((a, i) => (
              <li key={i}>{a.text}</li>
            ))}
          </ul>
          <p className="kz-note">{t('kantan:redesign.findingsBody')}</p>
          <div className="kz-actions">
            <button
              type="button"
              onClick={fixCarriedAdvisories}
              disabled={!isReady || !proposal || busy}
            >
              {t('kantan:s5.fix.button')}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setCarriedAdvisories([])}
              disabled={busy}
            >
              {t('kantan:redesign.findingsDismiss')}
            </button>
          </div>
          {!isReady && <p className="kz-note">{t('kantan:s1.aiNotReady')}</p>}
          {fixErr && <FixFailure raw={fixErr} plainBody={plainBody(fixErr)} />}
          <details className="kz-carried-raw">
            <summary>{t('gallery:advisory.rawSummary')}</summary>
            <ul className="kz-stop-plainlist">
              {carriedAdvisories.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          </details>
        </section>
      )}

      {stop ? (
        <section className="kz-card kz-stop" role="alert">
          {/* Plain headline: the translated one when the raw detail was
              recognised, else the per-stage fallback ("…でエラーが起きました"). */}
          <h3 className="kz-title">
            {stopPlain?.title
              ? t(stopPlain.title)
              : stop.kind === 'weakness'
                ? t('kantan:s5.stop.weakness', { count: weaknessCount })
                : t(`kantan:s5.stop.${stop.kind}`)}
          </h3>
          {/* Plain body for the HTTP-error kinds (#7); the technical string stays
              folded below. The other kinds keep their own dedicated bodies. */}
          {stopPlain && <p className="kz-note">{t(stopPlain.body, stopPlain.vars)}</p>}
          {stop.kind === 'design' && <p className="kz-note">{t('kantan:s5.stop.designBody')}</p>}
          {stop.kind === 'weakness' && (
            <>
              <p className="kz-note">{t('kantan:s5.stop.weaknessBody')}</p>
              {weaknessDisconnected && (
                <p className="kz-note">{t('kantan:s5.stop.weaknessDisconnected')}</p>
              )}
            </>
          )}
          {stop.kind === 'refineTruncated' && (
            <p className="kz-note">{t('kantan:s5.stop.refineTruncatedBody')}</p>
          )}
          {/* K11 (ADR §5.1): the plain-language list of what stopped the run.
              The technical text stays in the folded details below. */}
          {(stop.kind === 'design' || stop.kind === 'weakness') &&
            stop.plainLines &&
            stop.plainLines.length > 0 && (
              <ul className="kz-stop-plainlist">
                {stop.plainLines.map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            )}
          {stop.kind === 'interrupted' && (
            <p className="kz-note">{t('kantan:s5.stop.interruptedBody')}</p>
          )}
          {stop.kind === 'files' && (
            <>
              {/* Name the file. "The one you dropped first" is unanswerable
                  days later, and the design only fits THAT file (RESUME-17). */}
              <p className="kz-note">
                {sourceNames.length > 0
                  ? t('kantan:s5.stop.filesBodyNamed', {
                      names: sourceNames.map((f) => f.name).join('、'),
                    })
                  : t('kantan:s5.stop.filesBody')}
              </p>
              <DropZone onFiles={onStopFilesDropped} accept={acceptFor(kind)} />
              {pickError && <p className="kz-note kz-pick-error">{pickError}</p>}
            </>
          )}
          {stop.detail && (
            <details className="kz-stop-detail">
              <summary>{t('kantan:s5.stop.detailSummary')}</summary>
              <pre className="error">{stop.detail}</pre>
            </details>
          )}
          {aiFixCount > 0 && (aiFixable || stop.kind === 'refineTruncated') && (
            <p className="kz-note">{t('kantan:s5.fix.attempted', { n: aiFixCount })}</p>
          )}
          {aiFixable && fixErr && <FixFailure raw={fixErr} plainBody={plainBody(fixErr)} />}
          {/* The same findings came back after an AI fix: saying so — and
              stepping the button down — is what stops the endless retry
              (WEAK-MODEL-24). */}
          {aiFixable && fixStuck && <p className="kz-note">{t('kantan:s5.fix.stuck')}</p>}
          <div className="kz-actions">
            {/* Primary action, one per card. design → AI fix; token/timeout →
                open settings; 404 → start over; otherwise → retry (below). */}
            {aiFixable && (
              <button
                type="button"
                // Demoted once the AI has handed the same problem back: the
                // remaining exits (weakness continue / detail mode / start
                // over) are the ones that can still move (WEAK-MODEL-24).
                className={fixStuck ? 'btn btn--ghost' : undefined}
                onClick={runAiFix}
                disabled={!isReady || !proposal}
              >
                {t('kantan:s5.fix.button')}
              </button>
            )}
            {/* A cut-off fix: ask again (same request), or go on with the design
                exactly as it was before it (WEAK-MODEL-16). */}
            {stop.kind === 'refineTruncated' && (
              <>
                <button
                  type="button"
                  onClick={retryLastRefine}
                  disabled={!isReady || !proposal || !lastRefineRef.current}
                >
                  {t('kantan:s5.stop.refineRetry')}
                </button>
                <button type="button" className="btn btn--ghost" onClick={continueWithoutFix}>
                  {t('kantan:s5.stop.refineSkip')}
                </button>
              </>
            )}
            {/* Only a weakness may be waved through. Resumes at `attach` — the
                design is already materialized and adopted, so continuing must
                NOT re-run materialize (that would re-raise the same card). */}
            {stop.kind === 'weakness' && (
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => void runPipeline('attach')}
              >
                {t('kantan:s5.weakness.continue')}
              </button>
            )}
            {stopHint === 'settings' && (
              <button type="button" onClick={() => openSettings('server-token')}>
                {t('kantan:s1.openSettings')}
              </button>
            )}
            {stopHint === 'restart' && (
              <button type="button" onClick={restartFromScratch}>
                {t('kantan:s5.stop.restart')}
              </button>
            )}
            {stop.retryFrom && (
              <button
                type="button"
                className={stopPrimaryElsewhere ? 'btn btn--ghost' : undefined}
                onClick={() => {
                  if (stop.retryFrom) void runPipeline(stop.retryFrom)
                }}
              >
                {t('kantan:s5.stop.retry')}
              </button>
            )}
            {/* #9 escape hatch: always available (secondary), unless it is
                already the primary above (the 404 case). */}
            {stopHint !== 'restart' && (
              <button type="button" className="btn btn--ghost btn--sm" onClick={restartFromScratch}>
                {t('kantan:s5.stop.restart')}
              </button>
            )}
            <button
              type="button"
              className={fixStuck ? undefined : 'btn btn--ghost'}
              onClick={openDetail}
            >
              {t('kantan:s5.stop.openDetail')}
            </button>
            {/* The two failures that ARE about the AI setup, offered only to
                whoever can change it — with an administrator's key there is
                nothing for the reader to do here (ADR K5). */}
            {canOpenAiSettings &&
              (stopPlain?.title === 'kantan:s5.plain.llmAuthTitle' ||
                stopPlain?.title === 'kantan:s5.plain.modelTitle') && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => openSettings('ai')}
                >
                  {t('kantan:s1.openSettings')}
                </button>
              )}
          </div>
          {(aiFixable || stop.kind === 'refineTruncated') && !isReady && (
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
                  {/* Percent only: the raw done/total are triples, and a bare
                      five-digit number reads as "rows of my file" (K12). */}
                  {pipePhase === 'save'
                    ? t('kantan:s5.saving')
                    : uploadPct !== null && up?.total
                      ? t('kantan:s5.ingestingCount', { pct: uploadPct })
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
              {/* Only the INGEST runs on the server; the save before it is this
                  tab's own call. Say which of the two is true right now — and,
                  for the safe one, where to come back to (RESUME-05). */}
              <p className="kz-note">
                {t(pipePhase === 'ingest' ? 'kantan:s5.closeNote' : 'kantan:s5.savingNote')}
              </p>
            </>
          )}
          {jobNotice && (
            <p className="job-cancelled-note" role="status">
              {jobNotice}
            </p>
          )}
        </section>
      ) : step === 7 ? (
        <section className="kz-card">
          <h3 className="kz-title">{t('kantan:s7.title')}</h3>
          <p className="kz-note">{t('kantan:s7.lead')}</p>
          {trialLoading && (
            <p className="kz-note" role="status">
              <span className="spinner" />
              {t('kantan:s7.loading')}
            </p>
          )}
          {trialFailed && (
            <>
              {/* The queries are enrichment (K9): a failure offers a retry but
                  never blocks the road to publish — the human gates are S4/S6/S8. */}
              <p className="kz-note">{t('kantan:s7.failed')}</p>
              {trialErr && (
                <details className="kz-stop-detail">
                  <summary>{t('kantan:s5.stop.detailSummary')}</summary>
                  <pre className="error">{trialErr}</pre>
                </details>
              )}
              <div className="kz-actions">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => {
                    if (kzDatasetId) void loadS7(kzDatasetId)
                  }}
                >
                  {t('kantan:s7.retry')}
                </button>
              </div>
            </>
          )}
          {!trialLoading && trialQAs.length > 0 && (
            <>
              {trialQAs.map((qa, i) => (
                <div key={i} className="kz-qa">
                  <div className="kz-qa-q">{qa.q}</div>
                  <div className="kz-qa-a">{qa.a}</div>
                  {qa.citeIri && (
                    // The citation the whole screen exists to show — opened HERE,
                    // not as a link. S7 runs on the still-unpublished draft, and
                    // the citation landing page only answers for published data,
                    // so a link would 404 on every first run (DEREF-LANDING-01).
                    // The ID becomes a real web address at S8; until then it is
                    // shown as text you can copy.
                    <details className="kz-stop-detail">
                      <summary>{t('kantan:s7.citeOpen')}</summary>
                      {qa.citeDetails && qa.citeDetails.length > 0 && (
                        <div className="kz-kv">
                          {qa.citeDetails.map((d, di) => (
                            <span key={di}>
                              <span className="kz-kv-key">
                                {termLabel(d.predicate_iri, d.label)}
                              </span>
                              {formatNum(d.value, i18n.language)}
                              {d.unit ? ` ${d.unit}` : ''}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="kz-kv">
                        <span className="kz-kv-key">{t('kantan:s7.citeIdLabel')}</span>
                        <code>{qa.citeIri}</code>
                      </div>
                      <div className="kz-actions">
                        <CopyIdButton iri={qa.citeIri} />
                      </div>
                    </details>
                  )}
                </div>
              ))}
              <p className="kz-note">{t('kantan:s7.traceNote')}</p>
              <details className="kz-stop-detail">
                <summary>{t('kantan:s7.techSummary')}</summary>
                {trialQAs
                  .filter((qa) => qa.sparql)
                  .map((qa, i) => (
                    <pre key={i} className="sparql-block">
                      {qa.sparql}
                    </pre>
                  ))}
              </details>
            </>
          )}
          {/* Nothing came back although the questions could run: the draft is
              EMPTY. Publishing here would ship a dataset with no facts in it,
              so the road forward stops being the primary one (KZ-B-33). */}
          {trialEmpty && (
            <p className="kz-note">
              {totalSourceRows > 0
                ? t('kantan:s7.emptyRows', { rows: totalSourceRows.toLocaleString() })
                : t('kantan:s7.empty')}
            </p>
          )}
          <div className="kz-actions">
            <button
              type="button"
              className={trialEmpty ? 'btn btn--ghost' : undefined}
              onClick={goPublish}
              disabled={trialLoading}
            >
              {trialEmpty ? t('kantan:s7.okAnyway') : t('kantan:s7.ok')}
            </button>
            <button
              type="button"
              className={trialEmpty ? undefined : 'btn btn--ghost'}
              onClick={backToMeanings}
            >
              {t('kantan:s7.back')}
            </button>
            {/* The counts card is where a collapsed key shows itself; the place
                that was decided is the S4 gate (KZ-B-03 / DETAIL-GAP-08). */}
            {canBackToGate && (
              <button type="button" className="btn btn--ghost btn--sm" onClick={backToGate}>
                {t('kantan:s6.backToGate')}
              </button>
            )}
          </div>
        </section>
      ) : step === 8 ? (
        <section className="kz-card">
          <h3 className="kz-title">{t('kantan:s8.title')}</h3>
          <div className="kz-q">
            <label className="kz-q-text" htmlFor="kz-s8-name">
              {t('kantan:s8.nameLabel')}
            </label>
            <input
              id="kz-s8-name"
              className="kz-s8-name"
              type="text"
              value={pubName}
              placeholder={t('kantan:s8.namePlaceholder')}
              onChange={(e) => setPubName(e.target.value)}
            />
            {/* K10: the publish button stays disabled while anything is
                unsettled — here, the one still-open item is the public name. */}
            {!pubName.trim() && <p className="kz-note">{t('kantan:s8.needName')}</p>}
          </div>
          {s8Loading && (
            <p className="kz-note" role="status">
              <span className="spinner" />
              {t('kantan:s6.loading')}
            </p>
          )}
          {stats && stats.classes.length > 0 ? (
            <div className="kz-kv">
              <span className="kz-kv-key">{t('kantan:s8.contentLabel')}</span>
              <span>
                {stats.classes
                  .map((c) =>
                    t('kantan:s6.classCount', {
                      label: classLabel(c.iri),
                      n: c.n.toLocaleString(),
                    }),
                  )
                  .join(t('kantan:s7.join'))}
              </span>
            </div>
          ) : trial?.entities ? (
            // No declared kinds (class-less shape): reuse the S7 record count
            // so the publish summary still says what is being published.
            <div className="kz-kv">
              <span className="kz-kv-key">{t('kantan:s8.contentLabel')}</span>
              <span>{t('kantan:s7.aCountAny', { n: trial.entities.n.toLocaleString() })}</span>
            </div>
          ) : null}
          {words && (
            <div className="kz-kv">
              <span className="kz-kv-key">{t('kantan:s8.wordsLabel')}</span>
              <span>
                {t('kantan:s8.words', { reuse: words.reuse.length, added: words.added.length })}
                <details className="kz-words">
                  <summary>{t('kantan:s8.wordsList')}</summary>
                  {words.reuse.length > 0 && (
                    <p className="kz-words-group">
                      <span className="kz-words-head">{t('kantan:s8.wordsReuse')}</span>
                      {words.reuse.map((iri) => (
                        <code key={iri} title={iri}>
                          {localName(iri)}
                        </code>
                      ))}
                    </p>
                  )}
                  {words.added.length > 0 && (
                    <p className="kz-words-group">
                      <span className="kz-words-head">{t('kantan:s8.wordsNew')}</span>
                      {words.added.map((iri) => (
                        <code key={iri} title={iri}>
                          {localName(iri)}
                        </code>
                      ))}
                    </p>
                  )}
                </details>
              </span>
            </div>
          )}
          <p className="kz-note kz-promise">{t('kantan:s8.promise')}</p>
          {pubErr && (
            <div role="alert">
              <p className="kz-note kz-pub-err">
                {pubPlain?.title ? t(pubPlain.title) : t('kantan:s8.failed')}
              </p>
              {pubPlain && <p className="kz-note">{t(pubPlain.body, pubPlain.vars)}</p>}
              <details className="kz-stop-detail">
                <summary>{t('kantan:s5.stop.detailSummary')}</summary>
                <pre className="error">{pubErr}</pre>
              </details>
            </div>
          )}
          <div className="kz-actions">
            <button
              type="button"
              onClick={() => void runPublish()}
              disabled={!pubName.trim() || publishing}
            >
              {publishing ? t('kantan:s8.publishing') : t('kantan:s8.publish')}
            </button>
            {pubPlain?.hint === 'settings' && (
              <button type="button" onClick={() => openSettings('server-token')}>
                {t('kantan:s1.openSettings')}
              </button>
            )}
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => setStep(7)}
              disabled={publishing}
            >
              {t('kantan:s8.back')}
            </button>
          </div>
          <p className="kz-note">{t('kantan:s8.publishNote')}</p>
        </section>
      ) : step === 9 ? (
        <section className="kz-card kz-done">
          <h3 className="kz-done-title">✓ {t('kantan:s9.title')}</h3>
          {onOpenAsk && trialQAs.length > 0 && (
            <>
              <p className="kz-note">{t('kantan:s9.lead')}</p>
              <div className="kz-q-options">
                {/* The S7 questions, reborn as ask-me chips: click → the Ask
                    view opens with the question prefilled (K2's payoff). */}
                {trialQAs.map((qa, i) => (
                  <button
                    key={i}
                    type="button"
                    className="kz-pill"
                    onClick={() => onOpenAsk(qa.q)}
                  >
                    {qa.q}
                  </button>
                ))}
              </div>
              <p className="kz-note">{t('kantan:s9.askHint')}</p>
            </>
          )}
          {onCreateCrosswalk && publishedCount !== null && publishedCount >= 2 && (
            <>
              <hr className="kz-divider" />
              <p className="kz-note kz-grow-title">{t('kantan:s9.connectTitle')}</p>
              <p className="kz-note">
                {t('kantan:s9.connectBody', { count: publishedCount })}
              </p>
              <div className="kz-actions">
                <button type="button" onClick={onCreateCrosswalk}>
                  {t('kantan:s9.connectBtn')}
                </button>
              </div>
            </>
          )}
          {/* The last step of "a fact you can cite": handing someone the ID.
              Published data means the landing page answers, so here it IS a
              link — unlike S7, which runs on the unpublished draft
              (DEREF-LANDING-29). */}
          {shareIri && (
            <>
              <hr className="kz-divider" />
              <p className="kz-note kz-grow-title">{t('kantan:s9.shareTitle')}</p>
              <p className="kz-note">{t('kantan:s9.shareBody')}</p>
              <div className="kz-actions">
                {/* A button rather than an anchor: the page has no link style
                    of its own, and this belongs with the other actions. */}
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() =>
                    window.open(describeUrl(shareIri), '_blank', 'noopener,noreferrer')
                  }
                >
                  {t('kantan:s9.shareOpen')}
                </button>
                <CopyIdButton iri={describeUrl(shareIri, true)} labelKey="kantan:s9.shareCopy" />
              </div>
            </>
          )}
          <hr className="kz-divider" />
          <p className="kz-note kz-grow-title">{t('kantan:s9.growTitle')}</p>
          <div className="kz-actions">
            {/* Two different intents, two different landing spots: which one
                was chosen travels with the click (KZ-B-02). */}
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => openGrow('files', 'append')}
            >
              {t('kantan:s9.append')}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => openGrow('files', 'reingest')}
            >
              {t('kantan:s9.replace')}
            </button>
          </div>
          <p className="kz-note">{t('kantan:s9.growHint')}</p>
          <div className="kz-actions">
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => openGrow()}>
              {t('kantan:s9.openDataset')}
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={startFresh}>
              {t('kantan:s9.startNew')}
            </button>
          </div>
        </section>
      ) : step === 6 ? (
        <section className="kz-card">
          <h3 className="kz-title">{t('kantan:s6.title')}</h3>
          <p className="kz-note">{t('kantan:s6.lead')}</p>
          {/* The self-correction may have dropped mappings on its way to a
              clean design — say so where the columns are listed (DETAIL-GAP-22). */}
          {coverageDropped && <p className="kz-note">{t('kantan:s5.coverageDropped')}</p>}
          {s6Loading && (
            <p className="kz-note" role="status">
              <span className="spinner" />
              {t('kantan:s6.loading')}
            </p>
          )}
          {/* The same plain shape the stop card uses: a headline anyone can
              read, the reason in plain words, the raw server text folded
              (KZ-B-09). */}
          {s6Err && (
            <div role="alert">
              <p className="kz-title">
                {s6Plain?.title ? t(s6Plain.title) : t('kantan:s6.loadFailed')}
              </p>
              {s6Plain && <p className="kz-note">{t(s6Plain.body, s6Plain.vars)}</p>}
              <details className="kz-stop-detail">
                <summary>{t('kantan:s5.stop.detailSummary')}</summary>
                <pre className="error">{s6Err}</pre>
              </details>
              <div className="kz-actions">
                <button
                  type="button"
                  onClick={() => {
                    if (kzDatasetId) void loadS6(kzDatasetId)
                  }}
                >
                  {t('kantan:s6.reload')}
                </button>
                {s6Plain?.hint === 'settings' && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => openSettings('server-token')}
                  >
                    {t('kantan:s1.openSettings')}
                  </button>
                )}
                {s6Plain?.hint === 'restart' && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={restartFromScratch}
                  >
                    {t('kantan:s5.stop.restart')}
                  </button>
                )}
              </div>
            </div>
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
          {/* A count far below the row count means several rows became one
              record. That is sometimes right (one sample, many measurements)
              and sometimes a collapsed key — so name it, and offer the way
              back to where that was decided (KZ-B-03 / DETAIL-GAP-08). */}
          {stats && stats.classes.length > 0 && totalSourceRows > 0 && (
            <>
              <p className="kz-note">{t('kantan:s6.mapRowsNote')}</p>
              {canBackToGate && (
                <div className="kz-actions">
                  <button type="button" className="btn btn--ghost btn--sm" onClick={backToGate}>
                    {t('kantan:s6.backToGate')}
                  </button>
                </div>
              )}
            </>
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
                        // Meaning: IR label (K8) → model.yaml label. When the
                        // AI wrote neither, the cell SAYS so — the English
                        // identifier used to be shown here as if it were the
                        // meaning, and the only explanation was a hover
                        // tooltip no touch screen ever shows (KZ-B-04).
                        const meaning = p.label || rules?.labels?.[p.predicate_iri] || ''
                        const missing = !meaning
                        const samples = columnSamples[p.reference ?? ''] ?? []
                        return (
                          <tr
                            key={`${m.id}-${i}`}
                            className={missing ? 'kz-attn' : undefined}
                            title={
                              missing
                                ? humanizeLocal(localName(p.predicate_iri || p.predicate))
                                : undefined
                            }
                          >
                            <td className="kz-cols-name">{p.reference}</td>
                            <td>
                              {meaning || t('kantan:s6.meaningUnknown')}
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
          {/* Standing text, not a tooltip: whoever sees a ⚠ must be told what
              it means and that they may continue (KZ-B-04). */}
          {missingMeanings > 0 && <p className="kz-note">{t('kantan:s6.missingMeaning')}</p>}
          {/* Columns the AI mapped nowhere. A weak model drops them silently,
              and this table only ever showed what it DID map (DETAIL-GAP-12).
              Only said when the column list is actually known — never claim
              "nothing was dropped" from missing information. */}
          {droppedColumns.length > 0 && (
            <>
              <p className="kz-note">
                {t('kantan:s6.droppedColumns', { columns: droppedColumns.join('、') })}
              </p>
              <p className="kz-note">{t('kantan:s6.droppedColumnsHint')}</p>
            </>
          )}
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
            {/* Plain headline + plain reason + folded raw text, and a line
                saying the run is not stuck on this (KZ-B-10). */}
            {refineErr && (
              <div role="alert">
                <p className="kz-note">{t('kantan:s6.reflectFailed')}</p>
                <p className="kz-note">{plainBody(refineErr)}</p>
                <details className="kz-stop-detail">
                  <summary>{t('kantan:s5.stop.detailSummary')}</summary>
                  <pre className="error">{refineErr}</pre>
                </details>
                <p className="kz-note">{t('kantan:s6.reflectFailedNext')}</p>
                {refinePlain?.hint === 'settings' && (
                  <div className="kz-actions">
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => openSettings('server-token')}
                    >
                      {t('kantan:s1.openSettings')}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="kz-actions">
            {/* A review session that changed nothing has no staged draft to
                republish — its confirm exits to the catalog. Once a refine
                re-ingested (reingested), the normal ためす→公開 road applies. */}
            <button
              type="button"
              onClick={onConfirmMeanings}
              disabled={s6Loading || refining !== false}
            >
              {redesigning && !reingested
                ? t('kantan:redesign.confirmNoChange')
                : t('kantan:s6.confirm')}
            </button>
          </div>
        </section>
      ) : step === 1 ? (
        <>
          {/* K5: nothing here is a model decision. One sentence about what is
              missing, and one button to the single "connect the AI" screen
              (DETAIL-GAP-14). */}
          {!isReady && (
            <section className="kz-card kz-warn" role="status">
              <p className="kz-note">{t('kantan:s1.aiNotReady')}</p>
              <div className="kz-actions">
                <button type="button" onClick={() => openSettings('ai')}>
                  {t('kantan:s1.connectAi')}
                </button>
              </div>
            </section>
          )}
          {/* A file this browser kept from a run that already ended. Reading it
              again on its own would register the same measurements twice, so
              this is a choice, not an automatism (RESUME-11). */}
          {pendingRestore && pendingRestore.length > 0 && (
            <section className="kz-card kz-warn" role="status">
              <p className="kz-note">
                {t('kantan:s1.leftoverFile', {
                  name: pendingRestore.map((f) => f.name).join('、'),
                })}
              </p>
              <div className="kz-actions">
                <button type="button" onClick={usePendingRestore}>
                  {t('kantan:s1.leftoverUse')}
                </button>
                <button type="button" className="btn btn--ghost" onClick={dropPendingRestore}>
                  {t('kantan:s1.leftoverDrop')}
                </button>
              </div>
            </section>
          )}
          <section className="kz-card">
            <h3 className="kz-title">{t('kantan:s1.title')}</h3>
            <DropZone onFiles={onFilesChosen} />
            <p className="kz-note">{t('kantan:s1.privacy')}</p>
            {/* Not while the restore is still running — that would invite a
                re-drop of files the machine is about to hand back (RESUME-01). */}
            {resumeAvailable && !restoring && (
              <p className="kz-note kz-resume">{t('kantan:s1.resumeNote')}</p>
            )}
            {pickError && <p className="kz-note kz-pick-error">{pickError}</p>}
            {inspecting && (
              <p className="kz-note" role="status">
                <span className="spinner" />
                {t('kantan:s1.reading')}
              </p>
            )}
            {/* The first screen is the last place for an English stack trace:
                a plain headline, what to do about it, raw text folded
                (BACKEND-TEXT-02 / KZ-A-03). The drop zone above stays — the
                next move is to put a different file there. */}
            {inspectErr && (
              <div role="alert">
                <p className="kz-title">
                  {inspectPlain?.title ? t(inspectPlain.title) : t('kantan:s1.inspectFailed')}
                </p>
                {inspectPlain && (
                  <p className="kz-note">{t(inspectPlain.body, inspectPlain.vars)}</p>
                )}
                <details className="kz-stop-detail">
                  <summary>{t('kantan:s5.stop.detailSummary')}</summary>
                  <pre className="error">{inspectErr}</pre>
                </details>
              </div>
            )}
          </section>
          {kind === 'document' && (
            <section className="kz-card">
              <p className="kz-note">{t('kantan:s1.documentNote')}</p>
              {/* A document run survives a reload now (the snapshot keeps the
                  kind), and the panel reconnects on its own — say so, or the
                  reader sees a bare form and starts again (RESUME-19). */}
              {documentResumed && (
                <p className="kz-note kz-resume">{t('kantan:s1.documentResumeNote')}</p>
              )}
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
          {/* Resumed with the previous answers still in place: the two questions
              are the human's own knowledge and must not be asked twice (RESUME-09). */}
          {keptAnswers && (q1Needed || q2Needed) && (
            <p className="kz-note">{t('kantan:s2.keptAnswers')}</p>
          )}
          <PreviewList previews={previews} />
          {/* "It says it is showing me the read, but the read is wrong" — the
              three corrections a person can make by eye, in their own words.
              Hidden when the automatic read looks fine, so a clean CSV stays a
              two-click screen (KZ-A-10 / DETAIL-GAP-13). */}
          {showReadFix && (
            <details className="kz-stop-detail">
              <summary>{t('kantan:s2.fixSummary')}</summary>
              {fixableCards.map((card) => {
                const cur = dialectFor(card.canonical)
                return (
                  <div key={card.canonical} className="kz-q">
                    {fixableCards.length > 1 && <p className="kz-q-text">{card.name}</p>}
                    <p className="kz-q-text">{t('kantan:s2.fixDelimiter')}</p>
                    <div className="kz-q-options">
                      {DELIMITER_CHOICES.map((c) => (
                        <button
                          key={c.value}
                          type="button"
                          className={`kz-pill${cur.delimiter === c.value ? ' selected' : ''}`}
                          onClick={() => applyDialectFix(card.canonical, { delimiter: c.value })}
                        >
                          {t(c.key)}
                        </button>
                      ))}
                    </div>
                    {/* Same input atom the detail tier's read settings use, so
                        the number field looks like the rest of the system. */}
                    <label className="dialect-field">
                      <span>{t('kantan:s2.fixSkipRows')}</span>
                      <input
                        type="number"
                        min={0}
                        value={cur.skip_rows}
                        onChange={(e) => {
                          const n = Math.max(0, Math.trunc(Number(e.target.value) || 0))
                          applyDialectFix(card.canonical, { skip_rows: n })
                        }}
                      />
                    </label>
                    <p className="kz-q-text">{t('kantan:s2.fixEncoding')}</p>
                    <div className="kz-q-options">
                      {encodingChoices(
                        inspection?.dialects[card.canonical]?.encoding ??
                          defaultDialect(card.canonical).encoding,
                      ).map((c) => (
                        <button
                          key={c.key}
                          type="button"
                          className={`kz-pill${cur.encoding === c.value ? ' selected' : ''}`}
                          onClick={() => applyDialectFix(card.canonical, { encoding: c.value })}
                        >
                          {t(c.key)}
                        </button>
                      ))}
                    </div>
                  </div>
                )
              })}
              <p className="kz-note">{t('kantan:s2.fixNote')}</p>
            </details>
          )}
          {q1Needed && (
            <div className="kz-q">
              <p className="kz-q-text">{t('kantan:s2.q1', { count: preambleRowCount })}</p>
              {/* The actual lines the question is about — read client-side from
                  the user's own file (display only, like the table preview).
                  Without them, "was there sample info?" is unanswerable from
                  memory (dogfood 2026-07-23). */}
              {previews
                .filter((p) => (p.preambleLines?.length ?? 0) > 0)
                .map((p) => (
                  <div key={p.name} className="kz-preamble-evi">
                    <div className="kz-preamble-name">
                      {t('kantan:s2.preambleEviLabel', { name: p.name })}
                    </div>
                    <pre className="kz-preamble-lines">
                      {p.preambleLines!.slice(0, 8).join('\n')}
                      {p.preambleLines!.length > 8
                        ? `\n${t('kantan:s2.moreLines', { n: p.preambleLines!.length - 8 })}`
                        : ''}
                    </pre>
                  </div>
                ))}
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
            <button
              type="button"
              onClick={onProceed}
              disabled={!questionsAnswered || !isReady || writeBlocked}
            >
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
          {/* S3 is where the AI fails first, so it must speak the same plain
              language the S5 stop card does — the raw English/JSON goes into
              the fold, and the primary exit is "read it again" rather than a
              detour through S2 (BACKEND-TEXT-01 / KZ-A-04 / WEAK-MODEL-02). */}
          {errMsg && (
            <div role="alert">
              <p className="kz-title">
                {jobPlain?.title ? t(jobPlain.title) : t('kantan:s3.failed')}
              </p>
              {jobPlain && <p className="kz-note">{t(jobPlain.body, jobPlain.vars)}</p>}
              <details className="kz-stop-detail">
                <summary>{t('kantan:s5.stop.detailSummary')}</summary>
                <pre className="error">{errMsg}</pre>
              </details>
              <div className="kz-actions">
                <button
                  type="button"
                  onClick={() => void runSkeleton()}
                  disabled={!isReady || !hasSource || skeletonBusy}
                >
                  {t('kantan:s3.retry')}
                </button>
                <button type="button" className="btn btn--ghost" onClick={() => setStep(2)}>
                  {t('kantan:s3.back')}
                </button>
              </div>
            </div>
          )}
        </section>
      ) : (
        <section className="kz-card">
          {skeleton && (
            <SkeletonGate
              skeleton={skeleton}
              annotations={annotations}
              annotationsBusy={annotationsBusy}
              canRevalidate={hasSource}
              busy={continuing}
              plain
              onChange={onSkeletonEdited}
              onContinue={runContinue}
              onDiscard={() => {
                setSkeleton(null)
                setAnnotations(null)
                setStep(hasSource ? 2 : 1)
              }}
              onRethink={
                // A structural objection goes back to the AI (S3 rerun with the
                // note folded into the hint). Needs the files — a restore lost
                // them (the gate then keeps only the edit/discard exits).
                hasSource
                  ? (note) => {
                      setSkeleton(null)
                      setAnnotations(null)
                      setStep(3)
                      void runSkeleton(note || undefined)
                    }
                  : undefined
              }
              titleKey="kantan:s4.gateTitle"
              hintKey="kantan:s4.gateHint"
              continueKey="kantan:s4.continue"
              continuingKey="kantan:s4.continuing"
              discardKey="kantan:s4.discard"
              discardConfirmKey="kantan:s4.discardConfirm"
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
          {/* S4: the gate's own buttons are the exits (never two CTAs on one
              card), so the failure only needs to be readable — plain sentence
              here, raw text folded (BACKEND-TEXT-01). */}
          {errMsg && (
            <div role="alert">
              <p className="kz-note">
                {jobPlain?.title ? t(jobPlain.title) : t('kantan:s3.failed')}
              </p>
              {jobPlain && <p className="kz-note">{t(jobPlain.body, jobPlain.vars)}</p>}
              <details className="kz-stop-detail">
                <summary>{t('kantan:s5.stop.detailSummary')}</summary>
                <pre className="error">{errMsg}</pre>
              </details>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

/** Where a citation ID lands: the shared page the server renders for it. Kept
 *  relative for the link (same origin as this app) and absolute for the copy —
 *  a pasted link has to work in someone else's browser (DEREF-LANDING-29). */
function describeUrl(iri: string, absolute = false): string {
  const path = `/describe?iri=${encodeURIComponent(iri)}`
  return absolute ? `${window.location.origin}${path}` : path
}

// "ID をコピー": the one thing a reader can do with a citation ID before the
// data is published (the landing page only answers for published data).
function CopyIdButton({ iri, labelKey }: { iri: string; labelKey?: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="btn btn--ghost btn--sm"
      onClick={() => {
        void navigator.clipboard
          ?.writeText(iri)
          .then(() => {
            setCopied(true)
            window.setTimeout(() => setCopied(false), 2000)
          })
          .catch(() => {
            /* clipboard denied — the ID is on screen to select by hand */
          })
      }}
    >
      {copied ? t('kantan:s7.citeCopied') : t(labelKey ?? 'kantan:s7.citeCopy')}
    </button>
  )
}

/** An AI fix / reflect that failed: a plain headline, the plain reason, and the
 *  raw server text folded away — never the English string as the message
 *  (BACKEND-TEXT-08 / KZ-B-11 / KZ-B-10). */
function FixFailure({ raw, plainBody }: { raw: string; plainBody: string }) {
  const { t } = useTranslation()
  return (
    <>
      <p className="kz-note">{t('kantan:s5.fix.failed')}</p>
      <p className="kz-note">{plainBody}</p>
      <details className="kz-stop-detail">
        <summary>{t('kantan:s5.stop.detailSummary')}</summary>
        <pre className="error">{raw}</pre>
      </details>
    </>
  )
}

// The big S1 drop target: click opens the picker; drag & drop works too.
// `accept` narrows what the picker offers — the S5 resume drop zone only wants
// the kind of file this design was written for (RESUME-18).
function DropZone({
  onFiles,
  accept = DROP_ACCEPT,
}: {
  onFiles: (list: FileList | null) => void
  accept?: string
}) {
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
        accept={accept}
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
