// ③「意味をつける」の「取り込まない」と、データセットの保管庫
// （column-decisions.json）のあいだの読み書きの形。
//
// 画面の state は列を 1 本の文字列（`source\0column`）で指し、保管庫は
// `{source, column, action}` で持つ。ここはその行き来だけ — 何をいつ書くかは
// ウィザード側が決める（データセットを作る保存がサーバ側で書き、できたあとは
// ③の「この意味を保存して戻る」だけが書く）。
import type { ColumnDecision, ColumnRef } from '../api'

/** 物理的な 1 列を指す鍵。ウィザードの state はどこでもこの形で列を持つ。 */
export function columnKey(source: string, column: string): string {
  return `${source}\u0000${column}`
}

/** 保管庫の判断のうち「取り込まない」の列を、state と同じ鍵の形で返す。
 *  「取り込む」「持ち主」の判断は別の画面のもので、③の表には出ない。 */
export function storedExclusionKeys(decisions: ColumnDecision[]): string[] {
  return decisions
    .filter((decision) => decision.action === 'exclude')
    .map((decision) => columnKey(decision.source, decision.column))
}

/** ③で「取り込む」に戻された列 — 開いたときは保管庫で「取り込まない」だった
 *  のに、いまの画面ではもう外れていない列。保存のときに取り下げとして送る。 */
export function withdrawnExclusions(stored: string[], current: string[]): ColumnRef[] {
  const stillExcluded = new Set(current)
  return stored
    .filter((key) => !stillExcluded.has(key))
    .flatMap((key) => {
      const [source, column] = key.split('\u0000')
      return source && column ? [{ source, column }] : []
    })
}
