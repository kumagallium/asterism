// Thin client for the asterism-api surface (inspect + propose/SSE).

import { authHeaders } from './authToken'
import { type LlmCredentials, llmHeaders } from './settings/store'

/**
 * POST the given CSV files to /api/inspect and return the inspection Markdown.
 * `fks` are optional foreign-key hint columns (e.g. ["SID"]).
 */
export async function inspectCsvs(files: File[], fks: string[]): Promise<string> {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file)
  }
  const params = new URLSearchParams()
  for (const fk of fks) {
    params.append('fk', fk)
  }
  const query = params.toString()
  const url = query ? `/api/inspect?${query}` : '/api/inspect'

  const res = await fetch(url, { method: 'POST', body: form })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`inspect failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return res.text()
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
}

/**
 * Start a schema-proposal job and subscribe to its SSE stream.
 *
 * The active model's credentials (D7: user-brought, never persisted server-side)
 * are sent as `X-API-Key` + `X-LLM-*` headers on the POST only. Returns a cleanup
 * function that closes the EventSource — call it on unmount or a new run.
 */
export async function proposeCsvs(
  files: File[],
  domain: string,
  fks: string[],
  creds: LlmCredentials | null,
  handlers: ProposeHandlers,
  language?: string,
): Promise<() => void> {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file)
  }
  form.append('domain', domain)
  // Output language for the proposal's prose (i18next code, e.g. 'ja').
  // Headings / identifiers stay English server-side (materialize contract).
  if (language) form.append('language', language)
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
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`propose failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  const { job_id } = (await res.json()) as { job_id: string }
  handlers.onStart?.(job_id)
  return subscribeJob(job_id, handlers)
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
): Promise<() => void> {
  const res = await fetch('/api/refine', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...llmHeaders(creds),
    },
    body: JSON.stringify({ schema_md: schemaMd, comments, language: language || undefined }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`refine failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  const { job_id } = (await res.json()) as { job_id: string }
  handlers.onStart?.(job_id)
  return subscribeJob(job_id, handlers)
}

/** Handlers for resuming an existing job by id (result shape is job-dependent). */
export interface ResumeHandlers {
  onStatus?: (message: string) => void
  onDone: (result: unknown) => void
  onError: (message: string) => void
}

/**
 * Re-subscribe to an already-started job's SSE stream (no new POST). The server
 * JobManager replays started/running/done(/error), so a job that finished while
 * the UI was gone is recovered, and a still-running one keeps streaming. Returns
 * a cleanup function that closes the EventSource.
 */
export function resumeJob(jobId: string, handlers: ResumeHandlers): () => void {
  return subscribeJob(jobId, handlers)
}

/** One trap result from the 8-trap validator. */
export interface TrapResult {
  id: string
  name: string
  status: 'pass' | 'fail' | 'warn' | 'skip'
  detail: string
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
export async function attachSource(datasetId: string, files: File[]): Promise<AttachSourceResult> {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file)
  }
  const res = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/source`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`attach source failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
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
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`create document failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
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

/**
 * Human gate (Phase 5 #15): run a dataset's approved RML through the Morph-KGC
 * substrate and load the result into an isolated draft graph. Pass the source
 * CSVs to upload them (they are also persisted as the dataset's source); pass
 * none to reuse the dataset's persisted design-time source (Task E — the
 * catalog ingests a design-stage dataset with no re-attach).
 *
 * The heavy work (Morph-KGC materialize → chunked streaming load) runs as a
 * background job so a large dataset (millions of triples) loads with live
 * progress instead of a blocking request that times out (ADR
 * scalable-declarative-ingestion.md). The POST returns 202 + job_id; this
 * subscribes to the job's SSE stream, forwards progress to `onProgress`, and
 * resolves with the result on `done` (rejects on `error`).
 */
export async function ingestDataset(
  datasetId: string,
  files: File[] = [],
  onProgress?: (p: IngestProgress) => void,
): Promise<IngestResult> {
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
    const body = await res.text().catch(() => '')
    // A design-validation 422 carries {detail: {error, issues: [...]}} — surface the
    // structured issues so the UI renders a readable bulleted list, not a raw string.
    const issues = parseIngestIssues(body)
    if (issues) throw new IngestValidationError(issues)
    throw new Error(`ingest failed (HTTP ${res.status})${body ? `: ${body}` : ''}`)
  }
  const { job_id } = (await res.json()) as { job_id: string }
  return new Promise<IngestResult>((resolve, reject) => {
    const es = new EventSource(`/api/jobs/${job_id}/stream`)
    es.addEventListener('running', (e) => {
      try {
        onProgress?.(JSON.parse((e as MessageEvent).data) as IngestProgress)
      } catch {
        /* ignore a malformed progress frame */
      }
    })
    es.addEventListener('done', (e) => {
      es.close()
      resolve(JSON.parse((e as MessageEvent).data).result as IngestResult)
    })
    es.addEventListener('error', (e) => {
      const msg = (e as MessageEvent).data
      if (msg) {
        // Server-sent `error`: the job genuinely failed. Fatal.
        es.close()
        reject(new Error(JSON.parse(msg).message ?? 'ingest failed'))
      } else if (es.readyState === EventSource.CLOSED) {
        // Browser gave up reconnecting — a real, permanent loss.
        es.close()
        reject(new Error('connection lost'))
      }
      // Otherwise CONNECTING: a transient drop — let EventSource reconnect; the
      // JobManager replays from the start so a long ingest survives a blip.
    })
  })
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
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`materialize failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return (await res.json()) as MaterializeResult
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
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`load design failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
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
export async function validateDesign(datasetId: string): Promise<string[]> {
  const res = await fetch(
    `/api/datasets/${encodeURIComponent(datasetId)}/validate-design`,
    { headers: authHeaders() },
  )
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`validate design failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  const data = (await res.json()) as { validation_issues?: string[] }
  return data.validation_issues ?? []
}

// Shared SSE subscription for propose/refine jobs. Returns a cleanup function
// that closes the EventSource.
function subscribeJob<T>(
  jobId: string,
  handlers: { onStatus?: (m: string) => void; onDone: (r: T) => void; onError: (m: string) => void },
): () => void {
  const es = new EventSource(`/api/jobs/${jobId}/stream`)
  const close = () => es.close()

  es.addEventListener('started', () => handlers.onStatus?.('started'))
  es.addEventListener('running', (e) => {
    const data = JSON.parse((e as MessageEvent).data)
    handlers.onStatus?.(data.message ?? 'running')
  })
  es.addEventListener('done', (e) => {
    const data = JSON.parse((e as MessageEvent).data)
    handlers.onDone(data.result as T)
    close()
  })
  es.addEventListener('error', (e) => {
    const msg = (e as MessageEvent).data
    if (msg) {
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

  return close
}
