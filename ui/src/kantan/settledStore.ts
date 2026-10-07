// ③「意味をつける」の「取り込まない」と、データセットの保管庫
// （column-decisions.json）のあいだの読み書きの形。
//
// 画面の state は列を 1 本の文字列（`source\0column`）で指し、保管庫は
// `{source, column, action}` で持つ。ここはその行き来と、③を開いているあいだの
// 控え（保存せずに出るときの戻し先）だけ — 何をいつ書くかはウィザード側が
// 決める（データセットを作る保存がサーバ側で書き、できたあとは③の
// 「この意味を保存して戻る」だけが書く）。
import type { ColumnDecision, ColumnMeaning, ColumnRef } from '../api'

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

/** ③をデータセットの上で開いているあいだの控え — 保存せずに出るとき、state を
 *  ここへ戻す。できたあとの③の外では「state ＝ 保管庫」が約束で、書きかけを
 *  残したまま出ると、⑤からのやり直しがそれをサーバへ送って、保存していない
 *  「取り込まない」が設計に効く（実機 2026-10-05）。
 *
 *  戻す先は、保管庫から読めた欄はその内容。まだ読めていない欄（読み込み中・
 *  読めなかった）は開いたときの state ＝ 書きかけより前の値。 */
export interface MeaningsScreenOpened {
  datasetId: string
  meanings: ColumnMeaning[]
  excluded: string[]
  /** 保管庫から読めた「取り込まない」。読めるまでは無く、取り下げも計算しない。 */
  storedExclusions: string[] | null
}

/** 開いた瞬間の控え — 保管庫はまだ読めていない。 */
export function meaningsScreenOpened(
  datasetId: string,
  meanings: ColumnMeaning[],
  excluded: string[],
): MeaningsScreenOpened {
  return { datasetId, meanings, excluded, storedExclusions: null }
}

/** 保管庫の意味が読めた。空なら画面にあるもの（AI の下書き・前回の値）を残す —
 *  意味の無いデータセット（この仕組みより前に作ったもの）を、空で上書きしない。 */
export function withStoredMeanings(
  opened: MeaningsScreenOpened,
  stored: ColumnMeaning[],
): MeaningsScreenOpened {
  return stored.length > 0 ? { ...opened, meanings: stored } : opened
}

/** 保管庫の判断が読めた。「取り込まない」は空でも保管庫が勝つ（外した列は無い、
 *  がその答え）。 */
export function withStoredDecisions(
  opened: MeaningsScreenOpened,
  decisions: ColumnDecision[],
): MeaningsScreenOpened {
  const keys = storedExclusionKeys(decisions)
  return { ...opened, excluded: keys, storedExclusions: keys }
}

/** 保存せずに出る: 書きかけを捨てて戻す state。控えが無い・別のデータセットの
 *  控えなら戻さない（null）。 */
export function unsavedDraftDiscarded(
  opened: MeaningsScreenOpened | null,
  datasetId: string | null,
): { meanings: ColumnMeaning[]; excluded: string[] } | null {
  if (!opened || !datasetId || opened.datasetId !== datasetId) return null
  return { meanings: opened.meanings, excluded: opened.excluded }
}
