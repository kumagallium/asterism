// 作業場の会話（home・dataset）の状態の純関数。React から切り離してあるので vitest で固定できる。
// 提案カード・履歴の形は 7b（themes/workspaceState.ts）のものをそのまま使う。
import type { GraphOp } from '../themes/consultGraphApi'
import type { ChatItem, ProposalItem, RowDecision } from '../workspace/workspaceState'

/** 「AI に結ばせる」が送る定型の一言（契約 B3）。 */
export function bindUnlinkedMessage(lang: string): string {
  return lang.startsWith('en')
    ? 'Link the published kinds that are not yet linked to a term.'
    : '公開済みの種類で、用語にまだ結ばれていないものを結んで'
}

/** 提案カードの返事（rows の行）を反映した新しい ProposalItem。drop された行は `notes` に理由を残す。 */
export function settleRows(
  item: ProposalItem,
  rows: readonly number[],
  dropped: ReadonlyMap<number, string>,
  verdict: Exclude<RowDecision, null | 'dropped'>,
): ProposalItem {
  return {
    ...item,
    busy: false,
    decided: item.decided.map((d, i) => {
      if (!rows.includes(i)) return d
      return verdict === 'yes' && dropped.has(i) ? 'dropped' : verdict
    }),
    notes: item.notes.map((n, i) => (dropped.has(i) ? (dropped.get(i) as string) : n)),
  }
}

/** 会話に出ている提案のうち、まだ返事をしていない行の数（開くボタンの印）。 */
export function openProposalCount(items: readonly ChatItem[]): number {
  let n = 0
  for (const it of items) {
    if (it.kind === 'proposal') n += it.decided.filter((d) => d === null).length
  }
  return n
}

/** 適用した ops（落ちたものを除く）。 */
export function appliedOps(ops: readonly GraphOp[], droppedIndexes: Iterable<number>): GraphOp[] {
  const gone = new Set(droppedIndexes)
  return ops.filter((_, i) => !gone.has(i))
}
