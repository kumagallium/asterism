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
  /** `columns` = 前の ID を綴る列がいまのファイルに無い／`kind` = その種類が設計から消えた／
   *  `ledger` = 引っ越し先を記録できなかった（計画の上では引き継げたぶん）。 */
  reason: 'columns' | 'kind' | 'ledger'
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
  const blocked: IdMoveBlockedLine[] = (move.blocked ?? []).map((b) => ({
    key: `${b.source}:${b.name}`,
    source: b.source_label ?? b.source,
    reason: b.reason === 'missing_columns' ? 'columns' : 'kind',
    columns: b.missing_columns ?? [],
  }))
  // 台帳を作れなかった記録は、たどれなくなるぶんを `blocked` ではなく `moved` に
  // 持つ（計画の上では引き継げたので）。ここで拾わないと、警告の枠に「次のぶんは…」
  // と出たあと一覧が空になる。同じファイルの種類は理由も同じなので 1 回だけ言う。
  if (move.ledger_error) {
    // 保存名でまとめ、画面に出す名前は置いた名前（あれば）。
    const labels = new Map<string, string>()
    for (const m of move.moved ?? []) {
      if (!labels.has(m.source)) labels.set(m.source, m.source_label ?? m.source)
    }
    for (const [source, label] of labels) {
      blocked.push({ key: `${source}:ledger`, source: label, reason: 'ledger', columns: [] })
    }
  }
  // 画面に出る文は「名前・理由・列」で決まる。同じ文になる行は 1 回だけ言う ——
  // Excel のシートごとの表は同じブックの名前で呼ぶので、保存名では別の行でも
  // 読む人には同じ 1 行が並ぶだけになる。
  const said = new Set<string>()
  const lines = blocked.filter((b) => {
    const sentence = [b.source, b.reason, ...b.columns].join('\u0000')
    if (said.has(sentence)) return false
    said.add(sentence)
    return true
  })
  return {
    broken: move.fully_movable === false,
    forwarded: move.forwarded ?? 0,
    blocked: lines,
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
