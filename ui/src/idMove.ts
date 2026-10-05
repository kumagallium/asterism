// 「ID の引っ越し」の知らせの、画面に依らない部分（ADR id-move-after-publish.md §5）。
//
// 公開を押せる画面は 2 つある — かんたんモードの「公開する」と、データセットの
// 詳細の公開確認。どちらも同じ記録（取り込みのときに実測して meta に残したもの）
// を読み、同じことを言う。片方だけが読んでいた頃は、設計を見直したあと
// ウィザードを離れて詳細から公開を更新すると、警告なしで前の ID がたどれなく
// なった。読み方と言うことをここに 1 つだけ置き、描くのは `IdMoveNotice`。
import { useEffect, useState } from 'react'
import { fetchIdMove, type IdMove } from './api'

/** 引き継げない 1 件ぶん: どのファイルのぶんが、なぜ。 */
export interface IdMoveBlockedLine {
  key: string
  source: string
  /** `columns` = 前の ID を綴る列がいまのファイルに無い／`kind` = その種類が設計から消えた。 */
  reason: 'columns' | 'kind'
  columns: string[]
}

/** 知らせが言うこと。 */
export interface IdMoveNoticeView {
  /** 引き継げないぶんがある（警告色・理由・戻り道を出す）。 */
  broken: boolean
  /** 引っ越し先を書けた件数（計画値ではなく実測）。0 なら件数の文は出さない。 */
  forwarded: number
  blocked: IdMoveBlockedLine[]
}

/** 記録から、知らせが言うことを決める。`null` = 何も言わない（住所は動かない・
 *  まだ読めていない・読めなかった）。 */
export function idMoveNoticeView(move: IdMove | null | undefined): IdMoveNoticeView | null {
  if (!move?.changes_ids) return null
  return {
    broken: move.fully_movable === false,
    forwarded: move.forwarded ?? 0,
    blocked: (move.blocked ?? []).map((b) => ({
      key: `${b.source}:${b.name}`,
      source: b.source,
      reason: b.reason === 'missing_columns' ? 'columns' : 'kind',
      columns: b.missing_columns ?? [],
    })),
  }
}

/** 公開の前に「この更新で ID がどうなるか」を読む。`datasetId` が `null` の間は
 *  読まない。
 *
 *  `null` = まだ読んでいない／読めなかった。読めなかったときは黙る: 公開を止める
 *  材料ではないし、無い断定を作るよりは何も言わない方が正しい。 */
export function useIdMove(datasetId: string | null | undefined): IdMove | null {
  const [got, setGot] = useState<{ id: string; move: IdMove | null } | null>(null)
  useEffect(() => {
    if (!datasetId) return
    let off = false
    fetchIdMove(datasetId)
      .then((move) => !off && setGot({ id: datasetId, move }))
      .catch(() => !off && setGot({ id: datasetId, move: null }))
    return () => {
      off = true
    }
  }, [datasetId])
  // 別のデータセットの答えを、いま開いているものの答えとして出さない。
  return got && got.id === datasetId ? got.move : null
}
