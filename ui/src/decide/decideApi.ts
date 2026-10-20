// 「ひとこと決める」の api クライアント（薄い包み）。
//
//   preview … POST /api/vocab/decide/preview   帰結の試算（store には書かない・認証なし）
//   decide  … POST /api/crosswalk/align        そう決める（既存の align をそのまま）
//   dismiss … POST /api/vocab/dismiss          当てはまるものは無い（提案を外す）
//   undismiss … DELETE /api/vocab/dismiss      外した提案を戻す
//
// 失敗は DecideApiError（HTTP status 付き）で投げる — 画面が 409（循環）/ 422（結べない）を
// 言い分けられるように。
import { align as alignApi } from '../crosswalkApi'
import { authHeaders } from '../authToken'
import i18n from '../i18n'
import type { AlignRelation } from '../lineChoice'
import type { DecidePreview } from './decide'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export class DecideApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'DecideApiError'
    this.status = status
  }
}

async function asError(res: Response, op: string): Promise<DecideApiError> {
  const text = await res.text().catch(() => '')
  let detail = text
  try {
    const j = JSON.parse(text) as { detail?: unknown }
    if (j && typeof j.detail === 'string') detail = j.detail
  } catch {
    /* JSON ではない — 生の文字列のまま */
  }
  return new DecideApiError(
    i18n.t('decide:error.failed', { op, status: res.status, detail: detail ? `: ${detail}` : '' }),
    res.status,
  )
}

/** 線を足したら（`remove` なら外したら）どうなるかの試算。結べない組み合わせは `valid=false`。 */
export async function previewDecision(
  source: string,
  target: string,
  relation: AlignRelation,
  opts: { remove?: boolean } = {},
): Promise<DecidePreview> {
  const res = await fetch(`${API_BASE}/api/vocab/decide/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, target, relation, ...(opts.remove ? { remove: true } : {}) }),
  })
  if (!res.ok) throw await asError(res, i18n.t('decide:error.ops.preview'))
  return (await res.json()) as DecidePreview
}

/** そう決める（既存の align）。409＝循環・422＝結べない。 */
export async function decide(source: string, target: string, relation: AlignRelation): Promise<void> {
  try {
    await alignApi(source, target, relation)
  } catch (e) {
    const m = e instanceof Error ? e.message.match(/HTTP (\d{3})/) : null
    throw new DecideApiError(e instanceof Error ? e.message : String(e), m ? Number(m[1]) : 0)
  }
}

/** 「当てはまるものは無い」— この主語（IRI か、公開前の列・種類の鍵）にその用語を提案しない。冪等。
 * `relation`（述語名）を付けると関係の却下（下書きの線の ✕）、概念の却下は `subject="concept:<名前>"`・
 * `term="concept"`。 */
export async function dismiss(subject: string, term: string, relation?: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/vocab/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ subject, term, ...(relation ? { relation } : {}) }),
  })
  if (!res.ok) throw await asError(res, i18n.t('decide:error.ops.dismiss'))
}

/** 外した提案を戻す。冪等。 */
export async function undismiss(subject: string, term: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/vocab/dismiss`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ subject, term }),
  })
  if (!res.ok) throw await asError(res, i18n.t('decide:error.ops.undismiss'))
}
