// 「この答えに使った N 件を確認」の配線（取り込み元ごとに分けて `/relations/review` へ）。
import { KnowledgeApiError, reviewInChunks, type ReviewResult } from '../graphPlace/knowledgeRelationsApi'
import { relKey, type LedgerAnswer } from './types'

/** 確認が通ったら図（知識グラフ）を読み直す合図。ChatApp が受ける。 */
export const LEDGER_REVIEWED_EVENT = 'asterism:ledger-reviewed'

export interface ReviewOutcome {
  /** 通った鍵（取り込み元＋鍵）。 */
  done: Set<string>
  /** 通らなかった分の理由（重複なし・サーバの文）。 */
  reasons: string[]
  /** 通らなかった件数。 */
  notDone: number
}

export type PostReviewFn = (source: string, action: 'confirm', keys: string[]) => Promise<ReviewResult>

/** 未確認の鍵を取り込み元ごとに分けて確認する。1 つの取り込み元が失敗（409 など）しても、ほかは続ける。 */
export async function confirmUsed(
  unconfirmed: LedgerAnswer['unconfirmed'],
  post: PostReviewFn = (s, a, k) => reviewInChunks(s, a, k),
): Promise<ReviewOutcome> {
  const bySource = new Map<string, string[]>()
  for (const u of unconfirmed) bySource.set(u.source, [...(bySource.get(u.source) ?? []), u.key])
  const out: ReviewOutcome = { done: new Set(), reasons: [], notDone: 0 }
  const addReason = (r: string) => {
    if (r && !out.reasons.includes(r)) out.reasons.push(r)
  }
  for (const [source, keys] of bySource) {
    try {
      const r = await post(source, 'confirm', keys)
      for (const k of r.changed) out.done.add(relKey(source, k))
      for (const s of r.skipped) addReason(s.reason)
      out.notDone += keys.length - r.changed.length
    } catch (e) {
      out.notDone += keys.length
      addReason(e instanceof KnowledgeApiError || e instanceof Error ? e.message : String(e))
    }
  }
  return out
}
