// 「この答えに使った N 件を確認」の配線（取り込み元ごとに分け、50 件ずつ `/relations/review` へ）。
import { postReview, REVIEW_MAX, type ReviewResult } from '../graphPlace/knowledgeRelationsApi'
import { relKey, type LedgerAnswer } from './types'

/** 答えの帯で確認が通った: ChatApp が図（知識グラフ）を読み直す合図。 */
export const LEDGER_REVIEWED_EVENT = 'asterism:ledger-reviewed'
/** 台帳が変わった（確認・取り込み・外す。ChatApp が出す）: 画面にある答えが今の台帳で数え直す合図。ChatApp の読み直しは起こさない（輪にしない）。 */
export const LEDGER_CHANGED_EVENT = 'asterism:ledger-changed'

export interface ReviewOutcome {
  /** 通った鍵（取り込み元＋鍵）。先に確認済みだった（`already_confirmed`）鍵も含む。 */
  done: Set<string>
  /** 通らなかった分の理由（重複なし）。記号（`unknown`・`pair_has_relation`…）か 409 の文。 */
  reasons: string[]
  /** 通らなかった件数。 */
  notDone: number
}

export type PostReviewFn = (source: string, action: 'confirm', keys: string[]) => Promise<ReviewResult>

/** 記号か（画面では記号ごとの文に直す。409 の detail の文はそのまま出す）。 */
export const isReasonSymbol = (r: string): boolean => /^[a-z][a-z_]*$/.test(r)

/** 理由 → `reason.<key>` の key。記号でない（409 の文）なら null＝そのまま出す。 */
export function reasonKeyOf(r: string): 'unknown' | 'pair_has_relation' | 'cycle' | 'stale' | 'other' | null {
  if (!isReasonSymbol(r)) return null
  if (r === 'unknown' || r === 'pair_has_relation' || r === 'cycle') return r
  return /^(already_|not_)/.test(r) ? 'stale' : 'other'
}

/** 未確認の鍵を取り込み元ごとに、50 件ずつ確認する。かたまりが失敗（409 など）したら、その取り込み元の残りは送らず理由にする。
 *  先に通った分は通った分として返す。ほかの取り込み元は続ける。 */
export async function confirmUsed(
  unconfirmed: LedgerAnswer['unconfirmed'],
  post: PostReviewFn = (s, a, k) => postReview(s, a, k),
): Promise<ReviewOutcome> {
  const bySource = new Map<string, string[]>()
  for (const u of unconfirmed) bySource.set(u.source, [...(bySource.get(u.source) ?? []), u.key])
  const out: ReviewOutcome = { done: new Set(), reasons: [], notDone: 0 }
  const addReason = (r: string) => {
    if (r && !out.reasons.includes(r)) out.reasons.push(r)
  }
  for (const [source, keys] of bySource) {
    for (let i = 0; i < keys.length; i += REVIEW_MAX) {
      const chunk = keys.slice(i, i + REVIEW_MAX)
      try {
        const r = await post(source, 'confirm', chunk)
        const ok = new Set(r.changed)
        for (const s of r.skipped) {
          if (s.reason === 'already_confirmed') ok.add(s.key) // 先に確認済み（知識グラフや別の答えで）: 確認できた扱い
          else addReason(s.reason)
        }
        for (const k of ok) out.done.add(relKey(source, k))
        out.notDone += chunk.filter((k) => !ok.has(k)).length
      } catch (e) {
        out.notDone += keys.length - i
        addReason(e instanceof Error ? e.message : String(e))
        break
      }
    }
  }
  return out
}
