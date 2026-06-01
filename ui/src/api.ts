// Thin client for the csv2rdf-api surface (inspect + propose/SSE).

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
  return subscribeJob(job_id, handlers)
}

/** Result payload carried by the SSE `done` event for a refine job. */
export interface RefineResult {
  refined_md: string
  metadata: Record<string, unknown>
}

export interface RefineHandlers {
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
  return subscribeJob(job_id, handlers)
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
    // Distinguish a server-sent `error` event (has data) from a transport drop.
    const msg = (e as MessageEvent).data
    if (msg) {
      handlers.onError(JSON.parse(msg).message ?? 'unknown error')
    } else {
      handlers.onError('connection lost')
    }
    close()
  })

  return close
}
