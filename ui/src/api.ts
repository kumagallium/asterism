// Thin client for the asterism-api surface (inspect + propose/SSE).

import { authHeaders } from './authToken'
import { type LlmCredentials, llmHeaders } from './settings/store'

/**
 * How to READ one legacy source file (ADR source-dialect.md). `delimiter` is the
 * canonical token the server pins — `,` `\t` `;` `|` or the sentinel `whitespace`
 * (NOT a display label; the UI maps to/from labels). Absent/all-default = today's
 * clean-CSV read.
 */
export interface SourceDialect {
  encoding: string
  delimiter: string
  collapse: boolean
  skip_rows: number
  /** How to treat the preamble lines: `drop` (default), `keyvalue`, `lines` or
   *  `keyvalue_cells` (broadcast the parsed preamble metadata onto every row).
   *  ADR source-dialect.md. */
  preamble: string
}

/** A detected dialect, plus where it came from (auto-detected vs human-specified). */
export interface DetectedDialect extends SourceDialect {
  origin: string // 'detected' | 'specified'
  /** The preamble's detected SHAPE ('keyvalue' | 'keyvalue_cells' | 'lines') —
   *  identify-and-advise: what "keep the metadata" should pin as the parsing
   *  mode. Absent when the source has no preamble. */
  preamble_hint?: string
}

/** The structured result of /api/inspect: the Markdown body plus the sidecar
 *  headers (canonical source names + detected non-default dialects). */
export interface InspectResult {
  markdown: string
  /** Canonical (slugged) source names — the exact names rml:source must use. */
  sourceNames: string[]
  /** Detected NON-default dialects keyed by canonical name (clean sources absent). */
  dialects: Record<string, DetectedDialect>
  /** `{source: {column: [up to 3 real values]}}` as the SERVER read the file.
   *  The only preview there is for .xlsx / .json, which the browser cannot parse
   *  (KZ-A-08). Per column, never assembled into rows: the examples are picked
   *  per column, so a row of them would be a record nobody's file contains. */
  samples: Record<string, Record<string, string[]>>
  /** `{derived table: {from: workbook, sheet: worksheet title}}` — only for a
   *  workbook that produced MORE THAN ONE table, i.e. exactly when the user has
   *  to be asked which sheets to use (K6). */
  sheets: Record<string, SheetOrigin>
}

/** Where a derived table came from, in the words of the workbook (K6). */
export interface SheetOrigin {
  from: string
  sheet: string
}

/** Parse a JSON response header; an unreadable one degrades to `fallback`
 *  rather than breaking the call it rode along with. */
function jsonHeader<T>(res: Response, name: string, fallback: T): T {
  try {
    const raw = res.headers.get(name)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

/**
 * POST the given source files to /api/inspect and return the inspection Markdown
 * plus the structured sidecar (canonical source names, detected dialects — ADR
 * source-dialect.md, for the wizard "read settings" panel). `fks` are optional
 * foreign-key hint columns (e.g. ["SID"]).
 */
export async function inspectCsvs(
  files: File[],
  fks: string[],
  stagingId?: string | null,
): Promise<InspectResult> {
  const form = new FormData()
  appendSources(form, files, stagingId)
  const params = new URLSearchParams()
  for (const fk of fks) {
    params.append('fk', fk)
  }
  const query = params.toString()
  const url = query ? `/api/inspect?${query}` : '/api/inspect'

  const res = await fetch(url, { method: 'POST', body: form })
  if (!res.ok) await throwApiError(res, 'inspect')
  const markdown = await res.text()
  const namesHeader = res.headers.get('X-Asterism-Source-Names') ?? ''
  const sourceNames = namesHeader ? namesHeader.split(',').filter(Boolean) : []
  // an unreadable header must not break inspect (byte-safe fallback)
  const dialects = jsonHeader<Record<string, DetectedDialect>>(res, 'X-Asterism-Dialects', {})
  const samples = jsonHeader<Record<string, Record<string, string[]>>>(
    res,
    'X-Asterism-Samples',
    {},
  )
  const sheets = jsonHeader<Record<string, SheetOrigin>>(res, 'X-Asterism-Sheets', {})
  return { markdown, sourceNames, dialects, samples, sheets }
}

/**
 * Summary of the server-side self-correction loop (TODO ④): propose auto-fixes the
 * design against the real source + Tier-0 signatures across `rounds` refine rounds.
 * `converged` = zero remaining static issues; otherwise `remaining_issues` are the
 * messages for the RETURNED (best) schema. NOTE: convergence means "passed the static
 * gates", strictly weaker than "ingests cleanly" — the hard ingest gate is the real
 * gate. `tabular_only` false ⇒ JSON/XML field refs were NOT column-checked.
 */
export interface AutocorrectSummary {
  enabled: boolean
  converged: boolean
  terminal_reason: string
  initial_issue_count: number
  final_issue_count: number
  rounds: { n: number; issue_count: number; categories: Record<string, number> }[]
  remaining_issues: string[]
  tabular_only: boolean
  coverage_dropped: boolean
}

/** Result payload carried by the SSE `done` event for a propose job. */
export interface ProposeResult {
  proposal_md: string
  inspection_md: string
  metadata: Record<string, unknown>
  /** Present when the self-correction loop ran (TODO ④). */
  autocorrect?: AutocorrectSummary
}

/** Callbacks for the lifecycle events streamed while a propose job runs. */
export interface ProposeHandlers {
  /** Fired with the server job_id once the POST is accepted — persist it so the
   *  job can be resumed (replayed) after a reload/crash/disconnect. */
  onStart?: (jobId: string) => void
  onStatus?: (message: string) => void
  onDone: (result: ProposeResult) => void
  onError: (message: string) => void
  /** Fired on EVERY server-sent event (started/running/done/error/heartbeat/
   *  cancelled) — a pure liveness signal so the UI can show "server responded
   *  Ns ago" during a minutes-long LLM call. */
  onPulse?: () => void
  /** Fired on the terminal `cancelled` event (user-requested stop). When absent,
   *  the cancel falls back to onError('cancelled'). */
  onCancelled?: () => void
}

/**
 * Start a schema-proposal job and subscribe to its SSE stream.
 *
 * The active model's credentials (D7: user-brought, never persisted server-side)
 * are sent as `X-API-Key` + `X-LLM-*` headers on the POST only. Returns a
 * {@link JobHandle} — call `close()` on unmount or a new run, `cancel()` to stop
 * the job server-side.
 */
/**
 * Append the human's per-source dialect overrides (ADR source-dialect.md) as the
 * `dialects` JSON form field — ONLY when non-empty, so a clean-CSV design stays
 * byte-identical to today (empty ⇒ the server uses auto-detection).
 */
/**
 * The source of a design call: the files themselves, or — once they have been
 * staged (ADR source-staging.md) — the staging id, in which case nothing is
 * re-uploaded and the server reads its own copy. `stagingId` wins when set.
 */
function appendSources(form: FormData, files: File[], stagingId?: string | null): void {
  if (stagingId) {
    form.append('staging_id', stagingId)
    return
  }
  for (const file of files) form.append('files', file)
}

function appendDialects(form: FormData, dialects?: Record<string, SourceDialect>): void {
  if (dialects && Object.keys(dialects).length > 0) {
    form.append('dialects', JSON.stringify(dialects))
  }
}

export async function proposeCsvs(
  files: File[],
  domain: string,
  fks: string[],
  creds: LlmCredentials | null,
  handlers: ProposeHandlers,
  language?: string,
  dialects?: Record<string, SourceDialect>,
  stagingId?: string | null,
): Promise<JobHandle> {
  const form = new FormData()
  appendSources(form, files, stagingId)
  form.append('domain', domain)
  // Output language for the proposal's prose (i18next code, e.g. 'ja').
  // Headings / identifiers stay English server-side (materialize contract).
  if (language) form.append('language', language)
  appendDialects(form, dialects)
  const params = new URLSearchParams()
  for (const fk of fks) {
    params.append('fk', fk)
  }
  const query = params.toString()
  const url = query ? `/api/propose?${query}` : '/api/propose'

  const res = await fetch(url, {
    method: 'POST',
    body: form,
    headers: llmHeaders(creds),
  })
  if (!res.ok) await throwApiError(res, 'propose')
  const { job_id } = (await res.json()) as { job_id: string }
  handlers.onStart?.(job_id)
  return subscribeJob(job_id, handlers)
}

// ---------------------------------------------------------------------------
// Phase 2b: staged round-0 (skeleton → human gate → continue)
// ---------------------------------------------------------------------------

/** A skeleton map's subject: exactly one of template / constant. */
export interface SkeletonSubject {
  template?: string
  constant?: string
  classes?: string[]
  transform?: Record<string, string>
}

/** One skeleton map: which source becomes which class, keyed how (no properties). */
export interface SkeletonMap {
  name: string
  source: string
  iterator?: string
  subject: SkeletonSubject
  /** Free-text rationale for the subject-key choice (a hint for the human gate;
   *  dropped from the final IR at assembly). */
  note?: string
  /** Columns the HUMAN assigned to this map when splitting a shared concept
   *  out at the gate (ADR column-ownership G15). World knowledge the rows
   *  cannot hold; wins over the machine's ownership verdict. Dropped from the
   *  final IR at assembly — it steers per-map generation, it is not mapping. */
  owns?: string[]
}

/** The Mapping IR SKELETON — the early human-gate artifact. */
export interface MappingSkeleton {
  version: number
  prefixes: Record<string, string>
  maps: SkeletonMap[]
}

/** Deterministic gate evidence for ONE skeleton map (LLM-free, server-computed):
 *  does the chosen key really give every row its own ID, shown with real data. */
export interface SkeletonMapAnnotation {
  /** False when the key could not be tested (reason says why). */
  checkable: boolean
  reason?: string
  key_columns?: string[]
  missing_columns?: string[]
  undeclared_prefixes: string[]
  expanded_template?: string
  expanded_classes: { curie: string; iri: string }[]
  total_rows?: number
  rows_considered?: number
  distinct_ids?: number
  colliding_rows?: number
  is_unique?: boolean
  /** True when the adopted key is unique TODAY but built only from measurement-
   *  valued columns — an accidental identity that can collide as data grows
   *  (kantan-mode ADR K7). The gate shows an amber caution under the green band. */
  key_measurement_caution?: boolean
  /** Class names that look like a measured KEY column's name (the ZEM trap:
   *  row class `Temperature` over key `{Measurement temp.(C)}` — the row
   *  identity mislabeled as one of its measurements). Only populated when the
   *  key is measurement-only. */
  class_numeric_key_caution?: { class: string; column: string; token: string }[]
  collision_examples?: {
    key_values: Record<string, string>
    row_count: number
    line_numbers: number[]
  }[]
  /** Server verdict on what the key DOES to the rows: `unique` = every row its
   *  own ID; `singleton` = ALL rows merge into one file-scoped entity (the
   *  metadata-block pattern — merging is the point, not the accident);
   *  `partial` = some rows merge, some don't (the overwrite accident). */
  collapse_kind?: 'singleton' | 'unique' | 'partial'
  /** Citation-consequence risks of this ID recipe (machine-readable kinds,
   *  copy lives in the UI): `measurement-id` — a corrected value mints a new
   *  ID and strands citations of the old one; `scope-missing` — unique in
   *  this file only, appended files can merge a different parent's rows. */
  reference_risks?: {
    kind: string
    columns?: string[]
    parent_map?: string
    parent_columns?: string[]
    parent_classes?: string[]
  }[]
  /** One representative entity rendered as the CARD the mapping would build —
   *  the consequence of the ID recipe shown with real values. Conflict
   *  properties carry the fighting values with file line numbers; on a
   *  singleton, per-row columns are named in varying_columns instead. */
  entity_preview?: {
    id: string
    row_count: number
    entity_count: number
    properties: {
      column: string
      value?: string
      conflict?: boolean
      values?: { value: string; line: number }[]
      more_values?: number
      /** Set when another map's key decides this value (ADR
       *  column-ownership-and-growth G2): the row physically carries it, but
       *  storing it here copies one fact onto every row. */
      owner_map?: string
    }[]
    varying_columns: string[]
    omitted_columns: number
  } | null
  /** Columns this map would carry that another map OWNS (its key determines
   *  them and it mints fewer entities). The parent's key column is exempt —
   *  carrying it is how the join is declared. */
  borrowed_columns?: { column: string; owner_map: string }[]
  /** The mirror on the coarse side: columns this card CANNOT carry (they vary
   *  inside its group — `entity_preview.varying_columns`) and the map that
   *  owns them. Absent for a varying column nobody owns. */
  delegated_columns?: { column: string; owner_map: string }[]
  /** What the NEXT source file does to this design — only on a file-scoped
   *  (singleton) map, which by definition mints one entity per file.
   *  `shared_values` is measured, not forecast: columns that already repeat
   *  across sibling files are the ones worth splitting into their own class,
   *  since as-is each file mints its own copy instead of merging. */
  growth_preview?: {
    per_source_entities: number
    source_count: number
    row_maps: string[]
    described_columns: string[]
    shared_values?: { column: string; value: string; files: number }[]
    /** Pre-fill for the one-click split: the columns the sibling files already
     *  agree on (measured), and the identity-like one among them as the key.
     *  One file → nothing pre-checked, only the offer. */
    split_default?: { columns: string[]; key: string | null }
  }
  /** Per-row values with no map to live in: this source has a file-scoped
   *  (singleton) map but NO row-level one, so the columns that vary per row are
   *  silently dropped. Carries the one-click repair (name / key / template /
   *  resulting count) — round-0 returning a single map is the observed case. */
  missing_row_kind?: {
    columns: string[]
    suggested_name: string
    suggested_key: string[]
    suggested_template: string
    /** Starter class in the dataset's own vocabulary (the human renames it). */
    suggested_classes?: string[]
    entity_count: number
  }
  /** Real IDs minted from the first rows (prefix-expanded). */
  id_previews?: string[]
  /** Proven-unique column combinations (one-click fix candidates). `scoped`
   *  marks a parent-key-first rewrite that stays unique under appends. */
  key_candidates?: {
    columns: string[]
    rows_considered: number
    measurement_only: boolean
    scoped?: boolean
  }[]
}

/** The skeleton's minted namespace pair as the server recognizes it (kantan
 *  ADR K13): which prefixes are THIS dataset's (vs reused vocabularies), the
 *  dataset slug inside the IRI — the ONE naming judgment that persists — and
 *  whether the instance base is operator-configured (base fixes belong to
 *  Settings, never to a raw-IRI textbox). */
export interface DatasetNamespaceInfo {
  slug: string
  base: string
  base_configured: boolean
  ontology_prefix: string | null
  resource_prefix: string | null
}

export interface SkeletonAnnotations {
  maps: Record<string, SkeletonMapAnnotation>
  /** Skeleton-level: declared prefixes minted on a placeholder domain
   *  (example.org & co, ADR instance-iri-base.md) — can never be published. */
  placeholder_prefixes?: { prefix: string; iri: string }[]
  /** Null when no …/datasets/<slug>/… mint is present (the gate then falls
   *  back to the raw prefix table). */
  dataset_namespace?: DatasetNamespaceInfo | null
}

/** Result payload carried by the SSE `done` event for a skeleton job. */
export interface SkeletonResult {
  skeleton: MappingSkeleton
  inspection_md: string
  metadata: Record<string, unknown>
  /** Best-effort: null when the server-side evidence pass failed. */
  annotations?: SkeletonAnnotations | null
}

export interface SkeletonHandlers {
  onStart?: (jobId: string) => void
  onStatus?: (message: string) => void
  onDone: (result: SkeletonResult) => void
  onError: (message: string) => void
  onPulse?: () => void
  onCancelled?: () => void
}

/**
 * Phase 2b job 1: generate the mapping SKELETON (which source → which class,
 * keyed how) for human review — no properties or prose yet. Same SSE machinery
 * as propose; the done payload carries the editable skeleton + inspection.
 */
export async function proposeSkeleton(
  files: File[],
  domain: string,
  fks: string[],
  creds: LlmCredentials | null,
  handlers: SkeletonHandlers,
  language?: string,
  dialects?: Record<string, SourceDialect>,
  stagingId?: string | null,
): Promise<JobHandle> {
  const form = new FormData()
  appendSources(form, files, stagingId)
  form.append('domain', domain)
  if (language) form.append('language', language)
  appendDialects(form, dialects)
  const params = new URLSearchParams()
  for (const fk of fks) params.append('fk', fk)
  const query = params.toString()
  const url = query ? `/api/propose/skeleton?${query}` : '/api/propose/skeleton'

  const res = await fetch(url, { method: 'POST', body: form, headers: llmHeaders(creds) })
  if (!res.ok) await throwApiError(res, 'skeleton')
  const { job_id } = (await res.json()) as { job_id: string }
  handlers.onStart?.(job_id)
  return subscribeJob(job_id, handlers)
}

/**
 * Phase 2b job 2: from the CONFIRMED skeleton + the re-attached source, generate
 * each map's property table + the document, splice §9, and run the same
 * self-correction loop. The done payload is a normal {@link ProposeResult}.
 */
export async function proposeContinue(
  files: File[],
  skeleton: MappingSkeleton,
  domain: string,
  fks: string[],
  creds: LlmCredentials | null,
  handlers: ProposeHandlers,
  language?: string,
  autocorrect?: number,
  dialects?: Record<string, SourceDialect>,
  stagingId?: string | null,
): Promise<JobHandle> {
  const form = new FormData()
  appendSources(form, files, stagingId)
  form.append('skeleton', JSON.stringify(skeleton))
  form.append('domain', domain)
  if (language) form.append('language', language)
  appendDialects(form, dialects)
  const params = new URLSearchParams()
  for (const fk of fks) params.append('fk', fk)
  if (autocorrect !== undefined) params.set('autocorrect', String(autocorrect))
  const query = params.toString()
  const url = query ? `/api/propose/continue?${query}` : '/api/propose/continue'

  const res = await fetch(url, { method: 'POST', body: form, headers: llmHeaders(creds) })
  if (!res.ok) await throwApiError(res, 'continue')
  const { job_id } = (await res.json()) as { job_id: string }
  handlers.onStart?.(job_id)
  return subscribeJob(job_id, handlers)
}

/**
 * Re-compute the skeleton gate's deterministic evidence for an EDITED skeleton.
 * No LLM, no job — a plain synchronous call (typically <1s), so the gate can
 * re-check a hand-edited key/class while the human is still looking at it.
 */
export async function validateSkeleton(
  files: File[],
  skeleton: MappingSkeleton,
  dialects?: Record<string, SourceDialect>,
  stagingId?: string | null,
): Promise<SkeletonAnnotations> {
  const form = new FormData()
  appendSources(form, files, stagingId)
  form.append('skeleton', JSON.stringify(skeleton))
  appendDialects(form, dialects)
  const res = await fetch('/api/propose/skeleton/validate', { method: 'POST', body: form })
  if (!res.ok) await throwApiError(res, 'skeleton validate')
  return ((await res.json()) as { annotations: SkeletonAnnotations }).annotations
}

/** Result payload carried by the SSE `done` event for a refine job. */
export interface RefineResult {
  refined_md: string
  metadata: Record<string, unknown>
}

export interface RefineHandlers {
  onStart?: (jobId: string) => void
  onStatus?: (message: string) => void
  onDone: (result: RefineResult) => void
  onError: (message: string) => void
  /** Liveness signal — see {@link ProposeHandlers.onPulse}. */
  onPulse?: () => void
  /** Terminal `cancelled` event — see {@link ProposeHandlers.onCancelled}. */
  onCancelled?: () => void
}

/**
 * Apply review comments to the current schema Markdown and subscribe to the
 * resulting job's SSE stream. Reuses the same job/SSE machinery as propose.
 */
export async function refineSchema(
  schemaMd: string,
  comments: string[],
  creds: LlmCredentials | null,
  handlers: RefineHandlers,
  language?: string,
  /** The dataset being refined, when there is one. It buys two things the
   *  server can only do knowing WHICH dataset this is: the same bounded
   *  self-correction round 0 runs, and the meanings/units a human typed are
   *  re-asserted on the result instead of being quietly rewritten (N6). */
  datasetId?: string | null,
): Promise<JobHandle> {
  const res = await fetch('/api/refine', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...llmHeaders(creds),
    },
    body: JSON.stringify({
      schema_md: schemaMd,
      comments,
      language: language || undefined,
      dataset_id: datasetId || undefined,
    }),
  })
  if (!res.ok) await throwApiError(res, 'refine')
  const { job_id } = (await res.json()) as { job_id: string }
  handlers.onStart?.(job_id)
  return subscribeJob(job_id, handlers)
}

/** Handlers for resuming an existing job by id (result shape is job-dependent). */
export interface ResumeHandlers {
  onStatus?: (message: string) => void
  onDone: (result: unknown) => void
  onError: (message: string) => void
  /** Liveness signal — see {@link ProposeHandlers.onPulse}. */
  onPulse?: () => void
  /** Terminal `cancelled` event — see {@link ProposeHandlers.onCancelled}. */
  onCancelled?: () => void
}

/**
 * Re-subscribe to an already-started job's SSE stream (no new POST). The server
 * JobManager replays started/running/done(/error), so a job that finished while
 * the UI was gone is recovered, and a still-running one keeps streaming. Returns
 * a {@link JobHandle} whose `close()` releases the EventSource.
 */
export function resumeJob(jobId: string, handlers: ResumeHandlers): JobHandle {
  return subscribeJob(jobId, handlers)
}

/**
 * Request a server-side cancel of a running job (POST /api/jobs/{id}/cancel —
 * idempotent). The job's SSE stream then ends with a terminal `cancelled` event,
 * which is what settles the subscribed UI; this call only *requests* the stop.
 */
export async function cancelJob(jobId: string): Promise<void> {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) await throwApiError(res, 'cancel')
}

/** One trap result from the 8-trap validator. */
export interface TrapResult {
  id: string
  name: string
  status: 'pass' | 'fail' | 'warn' | 'skip'
  detail: string
  /**
   * Deterministic repair recipe issued by the failing check itself (where +
   * what shape + a paste-ready example derived from the design). English,
   * AI-directed: composeFixComment forwards it verbatim to the one-click fix.
   * Empty/absent on pass/skip.
   */
  fix?: string
}

/**
 * A failed api call, as STRUCTURE rather than as a sentence.
 *
 * Every call in this module used to throw `new Error("ingest failed (HTTP 404):
 * {\"detail\":…}")`, which dissolved the status and the server's detail into one
 * English string — so the screens that must react to WHICH failure this is (the
 * kantan stop card, the workbench's 404-recreate path) had no choice but to
 * classify by substring, and whatever they failed to recognise reached the
 * researcher raw. The fields below are what callers should branch on.
 *
 * `message` deliberately keeps the exact sentence this family always threw: it
 * is what the folded "技術情報" view shows, and `plainError`
 * (kantan/errorMessages.ts) still classifies on it.
 */
export class ApiError extends Error {
  /** Which call failed — this module's own English verb ("ingest", "propose"). */
  readonly op: string
  /** HTTP status of the failed response. */
  readonly status: number
  /** The response body verbatim (may be JSON, may be empty). */
  readonly body: string
  /** FastAPI's `{"detail": …}` unwrapped; the body itself when it is not that shape. */
  readonly detail: string
  /** Machine-readable cause when the server sends `{"detail": {"error": "…"}}`. */
  readonly code?: string

  constructor(op: string, status: number, body = '') {
    super(apiErrorMessage(op, status, body))
    this.name = 'ApiError'
    this.op = op
    this.status = status
    this.body = body
    const { detail, code } = unwrapDetail(body)
    this.detail = detail
    this.code = code
  }
}

/** The sentence this error family has always carried. Kept byte-for-byte —
 *  the stop card's deterministic classifier reads it (K11). */
function apiErrorMessage(op: string, status: number, body: string): string {
  return `${op} failed (HTTP ${status})${body ? `: ${body}` : ''}`
}

/** Pull the server's human/machine cause out of a FastAPI error body. Parse
 *  failure keeps the raw body — never less information than before. */
function unwrapDetail(body: string): { detail: string; code?: string } {
  const brace = body.indexOf('{')
  if (brace >= 0) {
    try {
      const parsed = JSON.parse(body.slice(brace)) as { detail?: unknown }
      const detail = parsed.detail
      if (typeof detail === 'string') return { detail }
      if (detail && typeof detail === 'object') {
        const error = (detail as { error?: unknown }).error
        if (typeof error === 'string') return { detail: error, code: error }
      }
    } catch {
      /* not JSON — the body is already the best detail we have */
    }
  }
  return { detail: body }
}

/**
 * Read a failed response and throw the {@link ApiError} for it. Always throws;
 * `await throwApiError(res, 'ingest')` is the one-line form every call site in
 * this module uses, so status/detail can never be lost on the way out again.
 */
export async function throwApiError(res: Response, op: string): Promise<never> {
  const body = await res.text().catch(() => '')
  throw new ApiError(op, res.status, body)
}

/** Registry meta for a persisted dataset (subset the workbench needs). */
export interface DatasetMeta {
  id: string
  name: string
  has_rml?: boolean
  ingested?: boolean
  graph_iri?: string
  triple_count?: number
  // Task E: design-time source CSVs persisted server-side (so a design-stage
  // dataset can be ingested from the catalog with no re-attach).
  has_source?: boolean
  source_files?: string[]
  // Redesign: whether the design (propose/refine Markdown) was persisted, so the
  // catalog can offer a "見直す" action that reopens it in the workbench.
  has_proposal?: boolean
  /**
   * Design weaknesses recorded at materialize (disconnected entities, unmapped
   * columns). Persisted so a published dataset can still say what it cannot
   * answer; absent on datasets materialized before this was stored, which is
   * why the catalog re-checks live rather than trusting this alone.
   */
  advisories?: string[]
}

export interface MaterializeResult {
  artifacts: Record<string, string | null> // filename -> contents
  complete: boolean
  warnings: string[]
  traps: TrapResult[]
  exit_code: number
  /** Present when the bundle was persisted to the registry (the default). */
  dataset?: DatasetMeta
  /**
   * Advisory design-validation issues (column references + Tier 0 function
   * parameters checked against the real source CSVs), surfaced at materialize so
   * the user can fix them BEFORE ingest. Empty/absent when the design is clean or
   * no source was available to check against (e.g. a brand-new design whose source
   * is attached after materialize). The hard ingest gate still re-checks.
   */
  validation_issues?: string[]
  /**
   * Design WEAKNESSES — the design is valid but weak: entities that never link
   * to each other, columns left unmapped. Separate from `validation_issues`
   * because the force differs: a defect must be fixed, a weakness is the human's
   * call ("fix it" vs "continue anyway"). Needs no source, so unlike
   * `validation_issues` this IS populated on a brand-new design — which is the
   * whole point: the wizard mints its dataset inside the first materialize, and
   * until 2026-07-24 that call returned nothing at all, so a published dataset
   * (ZEM) had its two entities disconnected without a word to the user.
   */
  advisories?: string[]
}

/** Result of the human-gated substrate ingest. */
export interface IngestResult {
  dataset_id: string
  graph_iri: string
  graph_kind: string
  triple_count: number
  dataset: DatasetMeta
}

/** Result of persisting a dataset's design-time source CSVs (Task E). */
export interface AttachSourceResult {
  dataset_id: string
  source_files: string[]
  dataset: DatasetMeta
}

/**
 * Persist the CSVs a dataset was designed from (Task E). Called after a
 * materialize so the design-stage dataset carries its source server-side and
 * can later be ingested from the catalog with no CSV re-attach.
 */
export async function attachSource(
  datasetId: string,
  files: File[],
  stagingId?: string | null,
): Promise<AttachSourceResult> {
  const form = new FormData()
  appendSources(form, files, stagingId)
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/source`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  })
  if (!res.ok) await throwApiError(res, 'attach source')
  return (await res.json()) as AttachSourceResult
}

/** Result of creating a document dataset from an uploaded JATS/Word file. */
export interface CreateDocumentResult {
  dataset_id: string
  source_files: string[]
  dataset: DatasetMeta
}

/**
 * Create a DOCUMENT dataset from one or MORE uploaded JATS (.xml) / Word (.docx) /
 * PDF (.pdf) files — no schema design (unlike CSV/JSON). The server persists the
 * source(s) (a .docx is converted to JATS by pandoc, a .pdf by the Docling sidecar at
 * ingest; source_kind=xml) and auto-attaches the document recall tools (search_text /
 * quote_with_citation / fetch_passage). Multiple documents land in ONE dataset. The
 * new dataset lands in the catalog at the design stage; ingest + promote are the
 * usual human gates.
 */
export async function createDocumentDataset(
  name: string,
  files: File[],
): Promise<CreateDocumentResult> {
  const form = new FormData()
  form.append('name', name)
  for (const file of files) form.append('files', file)
  const res = await fetch('/api/documents', { method: 'POST', headers: authHeaders(), body: form })
  if (!res.ok) await throwApiError(res, 'create document')
  return (await res.json()) as CreateDocumentResult
}

/**
 * RML design validation failed (the server returned a 422 whose body carries a
 * structured `issues` list): a referenced column is absent from the CSV, or a
 * function execution has a wrong/missing parameter. Carries the per-issue
 * messages so the UI can render a readable bulleted list instead of a raw string.
 */
export class IngestValidationError extends Error {
  issues: string[]
  constructor(issues: string[]) {
    super(issues.join('; '))
    this.name = 'IngestValidationError'
    this.issues = issues
  }
}

/**
 * Pull the `issues` array out of a design-validation 422 body
 * (`{detail: {error, issues: [...]}}`). Returns the string[] when present (and
 * non-empty), else null — so a plain error body falls back to the raw message.
 */
function parseIngestIssues(body: string): string[] | null {
  try {
    const parsed = JSON.parse(body) as { detail?: { issues?: unknown } }
    const issues = parsed?.detail?.issues
    if (Array.isArray(issues) && issues.length > 0) {
      return issues.map((i) => String(i))
    }
  } catch {
    /* not JSON — fall through to the raw-message path */
  }
  return null
}

/** A progress frame streamed while a (background) ingest runs. */
export interface IngestProgress {
  /** "materialize" | "materialized" | "upload" (+ future phases). */
  phase: string
  /** Rows loaded so far / total (present during the "upload" phase). */
  done?: number
  total?: number
  message?: string
}

/** The terminal `cancelled` outcome of an ingest job — callers branch on this
 *  to show "キャンセルしました" instead of an error. */
export class IngestCancelledError extends Error {
  constructor() {
    super('cancelled')
    this.name = 'IngestCancelledError'
  }
}

/** A saved ingest job turned out to belong to a DIFFERENT dataset — the api
 *  restarted and its job-N counter re-minted the id. The saved job is stale;
 *  callers clear it and reset silently. */
export class StaleIngestJobError extends Error {
  constructor() {
    super('stale job')
    this.name = 'StaleIngestJobError'
  }
}

/**
 * Live handle on a (background) ingest job. Unlike the old promise-only
 * wrapper, the job id is exposed so callers can persist it (reload recovery via
 * {@link resumeIngestJob}) and request a server-side cancel.
 */
export interface IngestJobHandle {
  jobId: string
  /** Settles with the ingest result on `done`; rejects with
   *  {@link IngestCancelledError} on the terminal `cancelled` event,
   *  {@link StaleIngestJobError} on a dataset mismatch, `Error` otherwise. */
  result: Promise<IngestResult>
  /** Ask the server to stop the job (its stream then ends with `cancelled`,
   *  which settles `result`). The staged partial graph is reclaimed server-side. */
  cancel: () => Promise<void>
  /** Release the EventSource only — the server-side job keeps running. */
  close: () => void
}

/**
 * Human gate (Phase 5 #15): run a dataset's approved RML through the Morph-KGC
 * substrate and load the result into an isolated staged graph. Pass the source
 * CSVs to upload them (they are also persisted as the dataset's source); pass
 * none to reuse the dataset's persisted design-time source (Task E — the
 * catalog ingests a design-stage dataset with no re-attach).
 *
 * The heavy work (Morph-KGC materialize → chunked streaming load) runs as a
 * background job (ADR scalable-declarative-ingestion.md): the POST returns
 * 202 + job_id and this attaches to the job's SSE stream, forwarding progress
 * frames to `onProgress`. Persist `handle.jobId` (see ingestJob.ts) so a reload
 * can re-attach with {@link resumeIngestJob}.
 */
export async function startIngestJob(
  datasetId: string,
  files: File[] = [],
  onProgress?: (p: IngestProgress) => void,
  onPulse?: () => void,
): Promise<IngestJobHandle> {
  // No files → send no body (the server falls back to the persisted source). With
  // files → multipart upload (also persisted). An empty multipart body is avoided
  // so the no-attach path matches a bare POST.
  let body: FormData | undefined
  if (files.length > 0) {
    body = new FormData()
    for (const file of files) {
      body.append('files', file)
    }
  }
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/ingest`, {
    method: 'POST',
    headers: authHeaders(),
    body,
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    // A design-validation 422 carries {detail: {error, issues: [...]}} — surface the
    // structured issues so the UI renders a readable bulleted list, not a raw string.
    const issues = parseIngestIssues(detail)
    if (issues) throw new IngestValidationError(issues)
    throw new ApiError('ingest', res.status, detail)
  }
  const { job_id } = (await res.json()) as { job_id: string }
  return attachIngestJob(job_id, datasetId, onProgress, onPulse)
}

/**
 * Re-attach to a saved ingest job after a reload — no new POST. The server
 * JobManager replays started/running/done(/error/cancelled), so a job that
 * finished while the UI was gone is recovered, and a still-running one keeps
 * streaming. `datasetId` guards against a stale id (api restart re-mints job-N):
 * a frame or result for a different dataset rejects with StaleIngestJobError.
 */
export function resumeIngestJob(
  jobId: string,
  datasetId: string,
  onProgress?: (p: IngestProgress) => void,
  onPulse?: () => void,
): IngestJobHandle {
  return attachIngestJob(jobId, datasetId, onProgress, onPulse)
}

function attachIngestJob(
  jobId: string,
  datasetId: string,
  onProgress?: (p: IngestProgress) => void,
  onPulse?: () => void,
): IngestJobHandle {
  // The executor runs synchronously, so `settle` is assigned before use.
  let settle!: { resolve: (r: IngestResult) => void; reject: (e: Error) => void }
  const result = new Promise<IngestResult>((resolve, reject) => {
    settle = { resolve, reject }
  })
  const staleGuard = (frameDatasetId: unknown): boolean => {
    if (typeof frameDatasetId === 'string' && frameDatasetId !== datasetId) {
      handle.close()
      settle.reject(new StaleIngestJobError())
      return true
    }
    return false
  }
  const handle = subscribeJob<IngestResult>(jobId, {
    onPulse,
    onRunning: (data) => {
      if (staleGuard(data.dataset_id)) return
      onProgress?.(data as unknown as IngestProgress)
    },
    onDone: (r) => {
      if (staleGuard(r.dataset_id)) return
      settle.resolve(r)
    },
    onError: (m) => settle.reject(new Error(m)),
    onCancelled: () => settle.reject(new IngestCancelledError()),
  })
  return { jobId, result, cancel: handle.cancel, close: handle.close }
}

/**
 * Split a proposal Markdown into the 4 artifacts and run the 8-trap validator.
 * Synchronous on the server (no LLM); returns artifact contents + trap report.
 *
 * `datasetId` (the redesign path) re-materializes that EXISTING dataset in place
 * — same id / graphs / lifecycle / source preserved — instead of minting a new
 * one. Omit it for the normal new-design flow.
 */
export async function materializeSchema(
  proposalMd: string,
  datasetName = 'dataset',
  datasetId?: string,
): Promise<MaterializeResult> {
  const body: Record<string, unknown> = {
    proposal_md: proposalMd,
    dataset_name: datasetName,
  }
  if (datasetId) body.dataset_id = datasetId
  const res = await fetch('/api/materialize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) await throwApiError(res, 'materialize')
  return (await res.json()) as MaterializeResult
}

/** Per-class entity counts of a dataset's draft graph + per-file source data
 *  rows — the kantan tier's correspondence card (ADR kantan-mode-two-tier-ux.md
 *  K12). `classes` is empty when nothing is ingested yet or the store is
 *  unreachable; callers then hide the card. No triple counts by design. */
export interface DraftStats {
  dataset_id: string
  classes: { iri: string; curie?: string; n: number }[]
  source_rows: Record<string, number>
}

export async function fetchDraftStats(datasetId: string): Promise<DraftStats> {
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/draft-stats`, {
    headers: authHeaders(),
  })
  if (!res.ok) await throwApiError(res, 'draft stats')
  return (await res.json()) as DraftStats
}

/** The design a dataset is CURRENTLY stored with (the server's own copy).
 *
 *  The kantan tier keeps a copy of the design in its snapshot, and the detail
 *  tier writes its own to the same dataset — so a round trip through the detail
 *  tier could leave the two disagreeing, with the wizard then refining and
 *  saving the older one over the newer (DETAIL-GAP-05). The server's copy
 *  settles it. Empty string when the dataset has no stored design. */
export async function fetchDatasetProposal(datasetId: string): Promise<string> {
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/proposal`, {
    headers: authHeaders(),
  })
  if (!res.ok) await throwApiError(res, 'proposal')
  return ((await res.json()) as { proposal_md?: string }).proposal_md ?? ''
}

/** Up to 3 real values per column, read from the dataset's OWN persisted source.
 *  What the column-meaning screen shows as evidence when this browser never
 *  parsed the file itself — a review reopened from the catalog, a resume after a
 *  reload, or any .xlsx (KZ-B-25). */
export async function fetchSourceSamples(datasetId: string): Promise<Record<string, string[]>> {
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/source-samples`, {
    headers: authHeaders(),
  })
  if (!res.ok) await throwApiError(res, 'source samples')
  const body = (await res.json()) as { columns?: Record<string, string[]> }
  return body.columns ?? {}
}

/** One human correction to what a column MEANS or the unit it is in (K8). */
export interface DisplayMetaEdit {
  predicate: string
  map?: string
  column?: string
  label?: string
  unit?: string
}

/** Save meanings/units the human typed — deterministic, no AI, no re-ingest.
 *  Returns the rows the server actually changed. */
export async function saveDisplayMeta(
  datasetId: string,
  edits: DisplayMetaEdit[],
): Promise<string[]> {
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/display-meta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ edits }),
  })
  if (!res.ok) await throwApiError(res, 'display meta')
  return ((await res.json()) as { changed?: string[] }).changed ?? []
}

/** One context literal of the trial "top" entity ("試料名: BiTe-04"). */
export interface TrialDetail {
  predicate_iri: string
  value: string
  label?: string
  unit?: string
}

/** The kantan tier's S7 ためす data (ADR K9): deterministic read-only queries
 *  over the draft graph — per-kind counts, the busiest numeric field's range,
 *  the entity holding its maximum (its IRI is the citation), or real entity
 *  IRIs (`samples`) when nothing is numeric. `available: false` = not ingested
 *  or the store did not answer — the UI offers a retry but the screen stays
 *  passable (S7 is enrichment; the human gates are S4/S6/S8). Labels/units come
 *  from the reviewed Mapping IR + model.yaml projection, never from an AI. */
export interface TrialQueries {
  dataset_id: string
  available: boolean
  classes: { iri: string; label?: string; n: number }[]
  count_sparql: string | null
  /** Plain entity count — only set when the draft declares no classes at all
   *  (a legal shape), so the first question never comes back empty-handed. */
  entities: { n: number; sparql: string } | null
  range: {
    predicate_iri: string
    label?: string
    unit?: string
    n: number
    min: string
    max: string
    sparql: string
  } | null
  top: {
    predicate_iri: string
    label?: string
    unit?: string
    value: string
    subject_iri: string
    subject_details: TrialDetail[]
    sparql: string
  } | null
  samples: {
    class_iri: string | null
    label?: string
    iris: string[]
    sparql: string
  } | null
}

export async function fetchTrialQueries(datasetId: string): Promise<TrialQueries> {
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/trial-queries`, {
    headers: authHeaders(),
  })
  if (!res.ok) await throwApiError(res, 'trial queries')
  return (await res.json()) as TrialQueries
}

/** A dataset's stored design (propose/refine Markdown) for the redesign flow. */
export interface DatasetProposal {
  dataset_id: string
  dataset_name: string
  proposal_md: string
  has_proposal: boolean
}

/**
 * Fetch a dataset's stored design so the workbench can reopen it for a redesign
 * (refine/edit → re-materialize the same dataset). `has_proposal` is false for
 * datasets materialized before the design was persisted (the UI then steers the
 * user to recreate instead of reopen).
 */
export async function fetchProposal(datasetId: string): Promise<DatasetProposal> {
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/proposal`, {
    headers: authHeaders(),
  })
  if (!res.ok) await throwApiError(res, 'load design')
  return (await res.json()) as DatasetProposal
}

/**
 * Advisory design validation against the dataset's PERSISTED source (read-only).
 * Called after {@link attachSource} lands so a brand-new design gets the same
 * pre-ingest advice a redesign already gets at materialize (a fresh design has no
 * persisted source at materialize time, so its inline `validation_issues` is empty).
 * Never throws on a bad design — it returns the issue list; only a missing dataset
 * or transport error rejects.
 */
export interface DesignCheck {
  /** Defects: the design will not do what it says (bad column, wrong params). */
  issues: string[]
  /** Weaknesses: valid but poorly connected / incomplete. Human's judgement. */
  advisories: string[]
}

export async function validateDesign(datasetId: string): Promise<DesignCheck> {
  const res = await fetch(
    `/api/datasets/${encodeURIComponent(datasetId)}/validate-design`,
    { headers: authHeaders() },
  )
  if (!res.ok) await throwApiError(res, 'validate design')
  const data = (await res.json()) as { validation_issues?: string[]; advisories?: string[] }
  return { issues: data.validation_issues ?? [], advisories: data.advisories ?? [] }
}

/** Live handle on a subscribed job: `close()` releases the EventSource (the
 *  server-side job keeps running); `cancel()` asks the server to stop the job
 *  (the stream then ends with the terminal `cancelled` event). */
export interface JobHandle {
  jobId: string
  close: () => void
  cancel: () => Promise<void>
}

// Shared SSE subscription for background jobs (propose/refine/ingest/discover).
// Returns a JobHandle whose close() releases the EventSource. Exported so other
// api modules (crosswalkApi) reuse the reconnect handling below rather than
// growing a second, subtly different EventSource client.
export function subscribeJob<T>(
  jobId: string,
  handlers: {
    onStatus?: (m: string) => void
    onDone: (r: T) => void
    onError: (m: string) => void
    /** Fired on EVERY server-sent event (incl. heartbeats) — liveness signal. */
    onPulse?: () => void
    /** Terminal `cancelled` event; absent → falls back to onError('cancelled'). */
    onCancelled?: () => void
    /** The FULL parsed `running` frame — ingest progress carries structured
     *  fields (phase/done/total) that the message-only onStatus would drop. */
    onRunning?: (data: Record<string, unknown>) => void
  },
): JobHandle {
  const es = new EventSource(`/api/jobs/${jobId}/stream`)
  const close = () => es.close()

  es.addEventListener('started', () => {
    handlers.onPulse?.()
    handlers.onStatus?.('started')
  })
  es.addEventListener('running', (e) => {
    handlers.onPulse?.()
    const data = JSON.parse((e as MessageEvent).data)
    handlers.onRunning?.(data)
    handlers.onStatus?.(data.message ?? 'running')
  })
  // Keep-alive sent every ~15s while the job is idle (a minutes-long LLM call):
  // no state change — just proof the server and the stream are alive.
  es.addEventListener('heartbeat', () => handlers.onPulse?.())
  es.addEventListener('done', (e) => {
    handlers.onPulse?.()
    const data = JSON.parse((e as MessageEvent).data)
    handlers.onDone(data.result as T)
    close()
  })
  // Terminal user-requested cancel: the server stopped the job; the stream ends.
  es.addEventListener('cancelled', () => {
    handlers.onPulse?.()
    if (handlers.onCancelled) handlers.onCancelled()
    else handlers.onError('cancelled')
    close()
  })
  es.addEventListener('error', (e) => {
    const msg = (e as MessageEvent).data
    if (msg) {
      handlers.onPulse?.()
      // A server-sent `error` event: the job genuinely failed. Fatal.
      handlers.onError(JSON.parse(msg).message ?? 'unknown error')
      close()
    } else if (es.readyState === EventSource.CLOSED) {
      // The browser gave up reconnecting — a real, permanent loss.
      handlers.onError('connection lost')
      close()
    }
    // Otherwise readyState === CONNECTING: a *transient* drop (common on
    // long-lived SSE through a dev proxy during a multi-minute LLM call). Do
    // NOT close — let EventSource auto-reconnect. The server's JobManager
    // replays started/running/done on reconnect, so the in-flight job (and its
    // result) is recovered without losing progress or clearing the saved job.
  })

  return { jobId, close, cancel: () => cancelJob(jobId) }
}

// ---------------------------------------------------------------------------
// Design-time source staging (ADR source-staging.md)
// ---------------------------------------------------------------------------

export interface StagedSources {
  stagingId: string
  /** Canonical (slugged) source names — the ones the design's rml:source uses. */
  sources: string[]
  /** Worksheet origin of every derived table, for multi-sheet workbooks (K6). */
  sheets: Record<string, SheetOrigin>
  expiresAt: string
}

/**
 * Give the dropped files a server-side home right away. Every later design call
 * passes the id instead of re-uploading, and S5's attach copies from it. Throws
 * on any failure — the caller keeps its own copy of the files and the legacy
 * upload path is unchanged (an old server, a closed write gate: both just mean
 * "no staging this time").
 */
export async function stageSources(files: File[]): Promise<StagedSources> {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  const res = await fetch('/api/staging', { method: 'POST', headers: authHeaders(), body: form })
  if (!res.ok) await throwApiError(res, 'staging')
  const body = (await res.json()) as {
    staging_id: string
    sources: string[]
    sheets?: Record<string, SheetOrigin>
    expires_at: string
  }
  return {
    stagingId: body.staging_id,
    sources: body.sources ?? [],
    sheets: body.sheets ?? {},
    expiresAt: body.expires_at,
  }
}

/** Narrow a staged record to the tables the human chose (K6 「どのシートを使いますか？」).
 *  Every later design call reads only these, and the attach persists only these. */
export async function selectStagingSources(
  stagingId: string,
  sources: string[],
): Promise<string[]> {
  const res = await fetch(`/api/staging/${encodeURIComponent(stagingId)}/sources`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ sources }),
  })
  if (!res.ok) await throwApiError(res, 'staging sources')
  return ((await res.json()) as { sources: string[] }).sources ?? []
}

/** Whether a remembered staging record is still there.
 *
 *  Three values on purpose: only the server SAYING the record is gone (404 /
 *  410) is proof. A network blip on reload, an api still coming up, a 5xx —
 *  those say nothing about the record, and answering "gone" to them threw away
 *  a source the server was still holding, with no way back for a browser whose
 *  own copy (IndexedDB) is unavailable (RESUME-20). */
export type StagingLiveness = 'alive' | 'gone' | 'unknown'

/** Is a remembered staging id still live? (asked on reload before trusting it) */
export async function stagingAlive(stagingId: string): Promise<StagingLiveness> {
  try {
    const res = await fetch(`/api/staging/${encodeURIComponent(stagingId)}`)
    if (res.ok) return 'alive'
    return res.status === 404 || res.status === 410 ? 'gone' : 'unknown'
  } catch {
    return 'unknown'
  }
}

/** Forget a staged source (a fresh start). Best-effort. */
export async function unstageSources(stagingId: string): Promise<void> {
  try {
    await fetch(`/api/staging/${encodeURIComponent(stagingId)}`, {
      method: 'DELETE',
      headers: authHeaders(),
    })
  } catch {
    /* best-effort */
  }
}
