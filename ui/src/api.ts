// Thin client for the asterism-api surface (inspect + propose/SSE).

import { authHeaders } from './authToken'

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

/** Result payload carried by the SSE `done` event for a propose job. */
export interface ProposeResult {
  proposal_md: string
  inspection_md: string
  metadata: Record<string, unknown>
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
 * The API key (D7: user-brought, never persisted server-side) is sent as the
 * `X-API-Key` header on the POST only. Returns a cleanup function that closes
 * the EventSource — call it on unmount or when starting a new run.
 */
export async function proposeCsvs(
  files: File[],
  domain: string,
  fks: string[],
  apiKey: string,
  handlers: ProposeHandlers,
): Promise<() => void> {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file)
  }
  form.append('domain', domain)
  const params = new URLSearchParams()
  for (const fk of fks) {
    params.append('fk', fk)
  }
  const query = params.toString()
  const url = query ? `/api/propose?${query}` : '/api/propose'

  const res = await fetch(url, {
    method: 'POST',
    body: form,
    headers: apiKey ? { 'X-API-Key': apiKey } : {},
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
  apiKey: string,
  handlers: RefineHandlers,
): Promise<() => void> {
  const res = await fetch('/api/refine', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(apiKey ? { 'X-API-Key': apiKey } : {}),
    },
    body: JSON.stringify({ schema_md: schemaMd, comments }),
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
}

export interface MaterializeResult {
  artifacts: Record<string, string | null> // filename -> contents
  complete: boolean
  warnings: string[]
  traps: TrapResult[]
  exit_code: number
  /** Present when the bundle was persisted to the registry (the default). */
  dataset?: DatasetMeta
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
 * Create a DOCUMENT dataset from a single uploaded JATS (.xml) or Word (.docx)
 * file — no schema design (unlike CSV/JSON). The server persists the source (a
 * .docx is converted to JATS by pandoc, source_kind=xml) and auto-attaches the
 * document recall tools (search_text / quote_with_citation / fetch_passage). The
 * new dataset lands in the catalog at the design stage; ingest + promote are the
 * usual human gates.
 */
export async function createDocumentDataset(name: string, file: File): Promise<CreateDocumentResult> {
  const form = new FormData()
  form.append('name', name)
  form.append('file', file)
  const res = await fetch('/api/documents', { method: 'POST', headers: authHeaders(), body: form })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`create document failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return (await res.json()) as CreateDocumentResult
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
    const detail = await res.text().catch(() => '')
    throw new Error(`ingest failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
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
 */
export async function materializeSchema(
  proposalMd: string,
  datasetName = 'dataset',
): Promise<MaterializeResult> {
  const res = await fetch('/api/materialize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ proposal_md: proposalMd, dataset_name: datasetName }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`materialize failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return (await res.json()) as MaterializeResult
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
