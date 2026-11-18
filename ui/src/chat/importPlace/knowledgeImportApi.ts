// 取り込み（知識の束）の api クライアント（api/src/asterism_api/knowledge_import.py・契約 pr14d2b §2）。
// 認証は authHeaders()、AI の座標は llmHeaders()（関係を選ぶ段だけが使う。未設定なら裏側が段を飛ばす）。
import { authHeaders } from '../../authToken'
import { type LlmCredentials, llmHeaders } from '../../settings/store'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export class ImportApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export interface ImportCounts {
  claims: number
  topics: number
  sources: number
  terms: number
}

/** 一覧の 1 行（GET /api/knowledge-imports）。 */
export interface ImportSource {
  source: string
  title: string | null
  dataset_id: string | null
  copied_at: string | null
  counts: ImportCounts
  relations: { shown: number; terms: number; candidates: number; unconfirmed: number; confirmed: number }
  /** 走っている作業（import / remove）。無ければ null。 */
  running: string | null
}

export type SkipReason = 'ai_unset' | 'off' | 'error' | 'cancelled' | 'aborted'

export interface ImportQuestion {
  op: string
  term: { id: string; name: string }
  text: string
  count: number
}

/** ジョブの result（結果の 1 枚）。 */
export interface ImportResult {
  source: string
  title: string | null
  dataset_id: string | null
  created: boolean
  copied: ImportCounts & { dropped: Record<string, number> }
  relations: {
    shown: number
    terms: number
    candidates: number
    tentative: number
    failed: number
    skipped: string | null
    cancelled: boolean
    aborted: boolean
  }
  questions: ImportQuestion[]
  questions_total: number
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const strOrNull = (v: unknown): string | null => (typeof v === 'string' ? v : null)

function counts(v: unknown): ImportCounts {
  const o = isObj(v) ? v : {}
  return { claims: num(o.claims), topics: num(o.topics), sources: num(o.sources), terms: num(o.terms) }
}

/** 一覧の形を検査して整える（壊れた行は捨てる）。 */
export function normalizeSources(body: unknown): ImportSource[] {
  const rows = isObj(body) && Array.isArray(body.sources) ? body.sources : []
  const out: ImportSource[] = []
  for (const r of rows) {
    if (!isObj(r) || typeof r.source !== 'string') continue
    const rel = isObj(r.relations) ? r.relations : {}
    out.push({
      source: r.source,
      title: strOrNull(r.title),
      dataset_id: strOrNull(r.dataset_id),
      copied_at: strOrNull(r.copied_at),
      counts: counts(r.counts),
      relations: {
        shown: num(rel.shown),
        terms: num(rel.terms),
        candidates: num(rel.candidates),
        unconfirmed: num(rel.unconfirmed),
        confirmed: num(rel.confirmed),
      },
      running: strOrNull(r.running),
    })
  }
  return out
}

/** ジョブの result の形を検査して整える。壊れていれば null。 */
export function normalizeResult(v: unknown): ImportResult | null {
  if (!isObj(v) || typeof v.source !== 'string' || !isObj(v.copied) || !isObj(v.relations)) return null
  const rel = v.relations
  const qs = Array.isArray(v.questions) ? v.questions : []
  return {
    source: v.source,
    title: strOrNull(v.title),
    dataset_id: strOrNull(v.dataset_id),
    created: v.created === true,
    copied: { ...counts(v.copied), dropped: isObj(v.copied.dropped) ? (v.copied.dropped as Record<string, number>) : {} },
    relations: {
      shown: num(rel.shown),
      terms: num(rel.terms),
      candidates: num(rel.candidates),
      tentative: num(rel.tentative),
      failed: num(rel.failed),
      skipped: strOrNull(rel.skipped),
      cancelled: rel.cancelled === true,
      aborted: rel.aborted === true,
    },
    questions: qs.flatMap((q): ImportQuestion[] =>
      isObj(q) && typeof q.text === 'string' && isObj(q.term)
        ? [{ op: String(q.op ?? ''), term: { id: String(q.term.id ?? ''), name: String(q.term.name ?? '') }, text: q.text, count: num(q.count) }]
        : [],
    ),
    questions_total: num(v.questions_total),
  }
}

async function fail(res: Response): Promise<ImportApiError> {
  let detail = `HTTP ${res.status}`
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail) detail = body.detail
  } catch {
    // 本文が JSON でなければ HTTP 状態のまま
  }
  return new ImportApiError(res.status, detail)
}

const jsonHeaders = (extra: Record<string, string> = {}): Record<string, string> => ({
  'Content-Type': 'application/json',
  ...authHeaders(),
  ...extra,
})

export async function listImports(): Promise<ImportSource[]> {
  const res = await fetch(`${API_BASE}/api/knowledge-imports`, { headers: authHeaders() })
  if (!res.ok) throw await fail(res)
  return normalizeSources(await res.json())
}

async function jobIdOf(res: Response): Promise<string> {
  if (!res.ok) throw await fail(res)
  const body: unknown = await res.json()
  if (!isObj(body) || typeof body.job_id !== 'string') throw new ImportApiError(502, 'job_id がありません')
  return body.job_id
}

/** 登録した MCP サーバから束を取って取り込む。202 のジョブ id を返す。 */
export async function startMcpImport(server: string, lang: 'ja' | 'en', creds: LlmCredentials | null): Promise<string> {
  const res = await fetch(`${API_BASE}/api/mcp-servers/${encodeURIComponent(server)}/knowledge-import`, {
    method: 'POST',
    headers: jsonHeaders(llmHeaders(creds)),
    body: JSON.stringify({ lang }),
  })
  return jobIdOf(res)
}

/** 束を渡して取り込む（スクリプト・試験用）。 */
export async function startBundleImport(source: string, bundle: unknown, lang: 'ja' | 'en', creds: LlmCredentials | null): Promise<string> {
  const res = await fetch(`${API_BASE}/api/knowledge-imports`, {
    method: 'POST',
    headers: jsonHeaders(llmHeaders(creds)),
    body: JSON.stringify({ source, bundle, lang }),
  })
  return jobIdOf(res)
}

/** 外す。写しを取り下げ、台帳の未確認を消す。 */
export async function removeImport(source: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/knowledge-imports/${encodeURIComponent(source)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) throw await fail(res)
}
