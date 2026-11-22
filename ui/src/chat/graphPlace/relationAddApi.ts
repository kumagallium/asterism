// 人が足した関係を読む・足す・直す・やめる・消す口（R3c・契約 §1〜§2）。
// 読む: GET /api/relations?usage=1（組み込みは飛ばし、足した関係だけ）。書く: POST/PATCH /api/relations・POST …/retire・DELETE …。
// 認証は authHeaders()（他の書き込みと同じ）。
import { authHeaders } from '../../authToken'
import { KnowledgeApiError } from './knowledgeRelationsApi'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

type Lang = 'ja' | 'en'
type Obj = Record<string, unknown>
const isObj = (x: unknown): x is Obj => !!x && typeof x === 'object' && !Array.isArray(x)
const str = (x: unknown): string => (typeof x === 'string' ? x : '')
const num = (x: unknown): number => (typeof x === 'number' && Number.isFinite(x) ? x : 0)

export interface RelationUsage {
  lines: number
  ledger: number
  drafts: number
}

/** 足した関係 1 件（`GET /api/relations` の組み込みでない物）。`usage` は `?usage=1` のときだけ。 */
export interface AddedRelation {
  name: string
  label: { ja: string; en: string }
  definition: { ja: string; en: string }
  symmetric: boolean
  toPropertyOnly: boolean
  exactMatch: string | null
  retired: boolean
  addedAt: string | null
  /** 使われ方の合計。`usage` が無い・欄が足りない・数でないときは null（使われ方が分からない）。 */
  used: number | null
}

const pair = (x: unknown): { ja: string; en: string } => {
  const o = isObj(x) ? x : {}
  return { ja: str(o.ja), en: str(o.en) }
}

/** 返りの `relations` から足した関係だけを正す。組み込み・名前の無い物は捨てる。 */
export function normalizeAdded(body: unknown): AddedRelation[] {
  const rels = isObj(body) && Array.isArray(body.relations) ? body.relations : []
  const out: AddedRelation[] = []
  for (const r of rels) {
    if (!isObj(r) || r.builtin !== false || typeof r.name !== 'string' || !r.name) continue
    const u = isObj(r.usage) ? r.usage : null
    // 3 つの欄がすべて 0 以上の数のときだけ合計する。1 つでも欠ける・数でないなら「分からない」
    const counts = u ? [u.lines, u.ledger, u.drafts] : null
    const known = u !== null && counts !== null && counts.every((n) => typeof n === 'number' && Number.isFinite(n) && n >= 0)
    out.push({
      name: r.name,
      label: pair(r.label),
      definition: pair(r.definition),
      symmetric: r.symmetric_pair === true,
      toPropertyOnly: r.to_property_only === true,
      exactMatch: str(r.exact_match) || null,
      retired: r.retired === true,
      addedAt: str(r.added_at) || null,
      used: u !== null && known ? num(u.lines) + num(u.ledger) + num(u.drafts) : null,
    })
  }
  return out
}

async function failure(res: Response): Promise<KnowledgeApiError> {
  let detail = ''
  try {
    const b: unknown = await res.json()
    if (isObj(b)) {
      if (typeof b.detail === 'string') detail = b.detail
      else if (isObj(b.detail) && typeof b.detail.detail === 'string') detail = b.detail.detail
    }
  } catch {
    // 本文が無い
  }
  return new KnowledgeApiError(res.status, detail || `HTTP ${res.status}`)
}

/** 一覧の取得の時間切れ（ミリ秒）。時間切れは失敗として扱う（画面の無効が戻る）。 */
export const READ_TIMEOUT_MS = 15000

export async function send(method: string, path: string, body?: unknown): Promise<unknown> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...authHeaders() },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    ...(method === 'GET' ? { signal: AbortSignal.timeout(READ_TIMEOUT_MS) } : {}),
  })
  if (!res.ok) throw await failure(res)
  return res.json()
}

export async function fetchAdded(): Promise<AddedRelation[]> {
  const body = await send('GET', '/api/relations?usage=1')
  if (!isObj(body) || !Array.isArray(body.relations)) throw new KnowledgeApiError(0, 'bad shape')
  return normalizeAdded(body)
}

/** 足す・直すで送る欄（言語ごと）。もう片方の言語は空で送らない（サーバの既定に任せる）。 */
export interface AddBody {
  label: Partial<Record<Lang, string>>
  definition: Partial<Record<Lang, string>>
  symmetric: boolean
  to_property_only: boolean
  exact_match?: string
  unstated_key?: string
}
export interface PatchBody {
  label: Partial<Record<Lang, string>>
  definition: Partial<Record<Lang, string>>
  exact_match: string | null
}

export const addRelation = (body: AddBody) => send('POST', '/api/relations', body)
export const patchRelation = (name: string, body: PatchBody) => send('PATCH', `/api/relations/${encodeURIComponent(name)}`, body)
export const retireRelation = (name: string) => send('POST', `/api/relations/${encodeURIComponent(name)}/retire`, {})
export const deleteRelation = (name: string) => send('DELETE', `/api/relations/${encodeURIComponent(name)}`)

/** 書き込みの失敗の分類。画面は分類ごとの文言（ja/en）を出す（サーバの文はそのまま出さない）。
 *  `ctx`: 409 の意味が操作で違う（足す＝既にある・上限／削除＝使用中）。 */
export type AddFailure =
  | { kind: 'unavailable' }
  | { kind: 'input' }
  | { kind: 'exists'; name: string | null }
  | { kind: 'limit' }
  | { kind: 'inUse' }
  | { kind: 'notFound' }
  | { kind: 'failed' }

export function addFailureOf(e: unknown, ctx: 'add' | 'delete' | 'other' = 'add'): AddFailure {
  if (!(e instanceof KnowledgeApiError)) return { kind: 'failed' } // ネットワーク・想定外
  const st = e.status
  if (st === 401 || st === 503) return { kind: 'unavailable' }
  if (st === 400) return { kind: 'input' }
  if (st === 404) return { kind: 'notFound' }
  if (st === 409) {
    if (ctx === 'delete') return { kind: 'inUse' }
    if (/足せる関係は/.test(e.message)) return { kind: 'limit' }
    if (/既にある関係/.test(e.message)) {
      const m = /既にある関係（(.+)）/.exec(e.message)
      return { kind: 'exists', name: m ? m[1] : null }
    }
    return { kind: 'failed' }
  }
  return { kind: 'failed' } // 502 など
}

/** AI に渡すのは先に足した 40 件まで（やめた物は数えない）。 */
export const AI_LIMIT = 40

export const nameOf = (r: AddedRelation, lang: Lang): string => r.label[lang] || r.label.ja || r.label.en || r.name

type T = (key: string, opts?: Record<string, unknown>) => string

/** 分類 → 画面の文言（`graphPlace.json` の `error.rel.*`）。 */
export function failureText(t: T, f: AddFailure): string {
  switch (f.kind) {
    case 'unavailable':
      return t('graphPlace:added.unavailable')
    case 'input':
      return t('graphPlace:error.rel.input', { label: 40, definition: 200 })
    case 'exists':
      return f.name ? t('graphPlace:error.rel.exists', { name: f.name }) : t('graphPlace:error.rel.existsPlain')
    case 'limit':
      return t('graphPlace:error.rel.limit', { n: 500 })
    case 'inUse':
      return t('graphPlace:added.inUse')
    case 'notFound':
      return t('graphPlace:error.rel.notFound')
    default:
      return t('graphPlace:error.rel.failed')
  }
}
