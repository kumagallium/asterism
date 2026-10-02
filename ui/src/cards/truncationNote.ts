// カードの結果が「元の全部ではない」ときの注記（asterism.plot_points と対）。
//
// サーバは図（推移・散らばり）を先頭から切らず、x の全範囲を覆ったまま間引く。
// それでも「何点のうち何点を描いているか」を黙っていると、読み手は図が全部だと
// 思い込む（実例: XRD 20〜80° の 3001 点が 20.00〜20.38° だけ描かれていたのに、
// 画面には何も出ていなかった）。`truncated` のときは必ずこの注記を出す。

export interface TruncationFields {
  count: number
  truncated: boolean
  /** 元の件数・点数（無ければ不明）。 */
  total?: number
  /** 図の点を全範囲から間引いた。 */
  thinned?: boolean
  /** `total` は下限（読み切れていない）。 */
  total_is_lower_bound?: boolean
}

export interface TruncationNote {
  /** `cards` 名前空間の文言キー。 */
  key: string
  params: { shown: number; total?: number }
}

/** 注記の文言キーと値。全部を出しているなら null。純関数。 */
export function truncationNote(result: TruncationFields): TruncationNote | null {
  if (!result.truncated) return null
  const shown = result.count
  const total = typeof result.total === 'number' && result.total > shown ? result.total : undefined
  if (result.thinned) {
    if (total === undefined) return { key: 'truncation.thinned_unknown', params: { shown } }
    return {
      key: result.total_is_lower_bound ? 'truncation.thinned_lower_bound' : 'truncation.thinned',
      params: { shown, total },
    }
  }
  if (total === undefined) return { key: 'truncation.more', params: { shown } }
  return {
    key: result.total_is_lower_bound ? 'truncation.partial_lower_bound' : 'truncation.partial',
    params: { shown, total },
  }
}
