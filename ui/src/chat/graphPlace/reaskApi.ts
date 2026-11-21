// 足した関係で「言えなかった組」を判定し直す口（R4・契約 §2〜§3）。
// 数える: GET /api/relations/{name}/reask（読むだけ）。始める: POST …（AI の座標は X-LLM-*・202 のジョブ id）。
// ジョブの見方は取り込みと同じ `subscribeJob`（/api/jobs/…/stream）。
import { authHeaders } from '../../authToken'
import { llmHeaders, type LlmCredentials } from '../../settings/store'
import { KnowledgeApiError } from './knowledgeRelationsApi'
import { send } from './relationAddApi'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

type Obj = Record<string, unknown>
const isObj = (x: unknown): x is Obj => !!x && typeof x === 'object' && !Array.isArray(x)
const count = (x: unknown): number => (typeof x === 'number' && Number.isFinite(x) && x >= 0 ? Math.floor(x) : 0)

export interface ReaskCount {
  total: number
  sources: Record<string, number>
}

/** 数える。形が違えば例外（呼び側は「ボタンを出さない」）。 */
export async function fetchReaskCount(name: string): Promise<ReaskCount> {
  const body = await send('GET', `/api/relations/${encodeURIComponent(name)}/reask`)
  if (!isObj(body) || typeof body.total !== 'number') throw new KnowledgeApiError(0, 'bad shape')
  const sources: Record<string, number> = {}
  if (isObj(body.sources)) for (const [k, v] of Object.entries(body.sources)) sources[k] = count(v)
  return { total: count(body.total), sources }
}

export interface ReaskStart {
  jobId: string
  sources: string[]
  skipped: Record<string, string>
}

const skippedOf = (x: unknown): Record<string, string> => {
  const out: Record<string, string> = {}
  if (isObj(x)) for (const [k, v] of Object.entries(x)) if (typeof v === 'string') out[k] = v
  return out
}

async function detailOf(res: Response): Promise<string> {
  try {
    const b: unknown = await res.json()
    if (isObj(b)) {
      if (typeof b.detail === 'string') return b.detail
      if (isObj(b.detail) && typeof b.detail.detail === 'string') return b.detail.detail
    }
  } catch {
    // 本文が無い
  }
  return `HTTP ${res.status}`
}

/** 始める。202 のジョブ id と、始めから飛ばした取り込み元（理由の符号つき）。 */
export async function startReask(name: string, lang: 'ja' | 'en', creds: LlmCredentials | null): Promise<ReaskStart> {
  const res = await fetch(`${API_BASE}/api/relations/${encodeURIComponent(name)}/reask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...llmHeaders(creds) },
    body: JSON.stringify({ lang }),
  })
  if (!res.ok) throw new KnowledgeApiError(res.status, await detailOf(res))
  const b: unknown = await res.json()
  if (!isObj(b) || typeof b.job_id !== 'string') throw new KnowledgeApiError(502, 'job_id がありません')
  return { jobId: b.job_id, sources: Array.isArray(b.sources) ? b.sources.filter((s): s is string => typeof s === 'string') : [], skipped: skippedOf(b.skipped) }
}

export interface ReaskResult {
  relation: number
  none: number
  failed: number
  asked: number
  /** 取り込み元 → 飛ばした理由の符号。 */
  skipped: Record<string, string>
}

/** ジョブの結果を正す。形が違えば null。 */
export function normalizeReaskResult(x: unknown): ReaskResult | null {
  if (!isObj(x) || !isObj(x.total)) return null
  return { relation: count(x.total.relation), none: count(x.total.none), failed: count(x.total.failed), asked: count(x.total.asked), skipped: skippedOf(x.skipped) }
}

export type ReaskFailKind = 'aiUnset' | 'busy' | 'nothing' | 'cancelled' | 'other'

/** 始める口の失敗の分類（サーバの文を画面に出さない）。 */
export function reaskFailureOf(e: unknown): ReaskFailKind {
  if (!(e instanceof KnowledgeApiError)) return 'other'
  if (e.status === 409) {
    if (/AI が設定|AI を組め/.test(e.message)) return 'aiUnset'
    if (/判定し直す組がありません/.test(e.message)) return 'nothing'
    return 'busy'
  }
  return 'other'
}
