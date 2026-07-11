// Client for the ingest-history surface (GET /jobs → jobs.jsonl tail).
//
// jobs.jsonl is written by the ingest watcher (one line per ingest pass). This
// is read-only history — distinct from the workbench registry (/api/datasets,
// which holds materialized *designs*). The fetch is best-effort: if the API is
// absent, the view shows an empty state rather than erroring.

// Same workbench API base as galleryApi (same-origin /jobs via the Vite proxy
// by default; override with VITE_API_URL for separate hosting).
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export interface IngestJob {
  kind: string // papers | samples | curves
  csv_path: string
  ttl_path: string | null
  rows_in: number
  rows_ok: number
  rows_err: number
  triples_out: number
  bytes_uploaded: number
  status: string // ok | partial | error
  error: string | null
  started_at: string
  ended_at: string
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

function normalizeJob(raw: unknown): IngestJob {
  const r = (raw ?? {}) as Record<string, unknown>
  const str = (v: unknown) => (typeof v === 'string' ? v : '')
  return {
    kind: str(r.kind),
    csv_path: str(r.csv_path),
    ttl_path: typeof r.ttl_path === 'string' ? r.ttl_path : null,
    rows_in: num(r.rows_in),
    rows_ok: num(r.rows_ok),
    rows_err: num(r.rows_err),
    triples_out: num(r.triples_out),
    bytes_uploaded: num(r.bytes_uploaded),
    status: str(r.status) || 'unknown',
    error: typeof r.error === 'string' ? r.error : null,
    started_at: str(r.started_at),
    ended_at: str(r.ended_at),
  }
}

/**
 * Fetch the most recent ingest jobs (newest last in jobs.jsonl → reversed to
 * newest first). 障害は throw する — 空配列に丸めると、アクティビティ画面が
 * 障害時に「まだ取り込み記録はありません」という誤った空状態になるため。
 */
export async function getJobs(limit = 50): Promise<IngestJob[]> {
  const res = await fetch(`${API_BASE}/jobs?limit=${limit}`)
  if (!res.ok) throw new Error(`jobs: HTTP ${res.status}`)
  const body = (await res.json()) as { jobs?: unknown[] }
  const jobs = Array.isArray(body.jobs) ? body.jobs.map(normalizeJob) : []
  return jobs.reverse() // jsonl tail is oldest→newest; show newest first
}
