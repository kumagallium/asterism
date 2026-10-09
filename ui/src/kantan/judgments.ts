// ③ の ☑「つながる手がかり」と ④ の「番号」— 骨格へ渡す 2 つの集合の**算出元**。
//
// 骨格の受け口（`linkable`）と、自動つなぎの材料（`handles.json`）は、別々の意味を
// 持つ 2 つの集合から作る（ADR upper-structure-shared-terms §2.5.2・K47／K48）:
//   ticked  = 人が自分で付けた ☑（handles.json に入る。出どころ via／term を添える）
//   numbers = 「この 1 件を名指す番号」の列（機械が決めたものも含む。handles には入れない）
//   linkable = ticked ∪ numbers      handles = ticked だけ
// 2 つの意味は変えない。出どころを 1 か所にして、`runAssemble` と材料の送信が別々に
// 計算して食い違うのを防ぐだけ。UI は DOM を持たないこのファイルを呼ぶ。
import type { MaterializeHandle } from '../api'
import { columnKey } from './settledStore'

/** 判断に要る列の事実（`MeaningRow` の部分集合）。 */
export interface JudgmentRow {
  source: string
  column: string
  /** `preamble` = ファイル全体の値（番号の候補になる）／`table` = 1 件ごとの値。 */
  origin: 'preamble' | 'table'
  examples: string[]
}

export interface JudgmentState {
  rows: JudgmentRow[]
  /** 「取り込まない」列の鍵（`columnKey`）。 */
  excluded: string[]
  /** ④で人が選んだ番号（source → column）。 */
  keyPick: Record<string, string>
  /** ③で人が付けた ☑ の鍵（`columnKey`）。 */
  checked: Iterable<string>
}

/** 測定値（小数を含む数値だけの列）— ☑ も番号も選べない。サーバの型スニッフの
 *  近似: 例が全部数値で、どれかに小数点／指数がある。 */
export function isMeasurement(examples: string[]): boolean {
  const vals = examples.filter((e) => e.trim() !== '')
  return (
    vals.length > 0 &&
    vals.every((e) => Number.isFinite(Number(e))) &&
    vals.some((e) => /[.eE]/.test(e))
  )
}

/** そのファイルで「番号」になれる列（測定値を除く、取り込む前置き列）。 */
export function numberCandidates(
  rows: JudgmentRow[],
  excluded: string[],
  source: string,
): string[] {
  return rows
    .filter(
      (r) =>
        r.source === source &&
        r.origin === 'preamble' &&
        !excluded.includes(columnKey(r.source, r.column)) &&
        !isMeasurement(r.examples),
    )
    .map((r) => r.column)
}

/** 実際に番号になる列 — 人が選んだもの、無ければ候補が 1 つだけのときのその列。
 *  候補が 2 つ以上で未選択、または 0 なら無い（⑤で仮置きの ⚠）。 */
export function effectiveNumber(
  state: Pick<JudgmentState, 'rows' | 'excluded' | 'keyPick'>,
  source: string,
): string | undefined {
  const picked = state.keyPick[source]
  // 「取り込まない」にした列は、④で選んでいても番号にならない（取り込まない列は骨格に入らない）。
  if (picked && !state.excluded.includes(columnKey(source, picked))) return picked
  const candidates = numberCandidates(state.rows, state.excluded, source)
  return candidates.length === 1 ? candidates[0] : undefined
}

export function sourcesOf(rows: JudgmentRow[]): string[] {
  return [...new Set(rows.map((r) => r.source))]
}

/** ④（番号を選ぶ）の出方。
 *   ask         候補が 2 つ以上のファイルがある → 段を出して選ばせる
 *   auto        全ファイルが候補ちょうど 1 つ → 畳む（「番号は自動で決まりました」）
 *   placeholder 候補 0 のファイルがある → 畳む（「番号は仮置き（⑤で確認）」）
 *  列の事実がまだ無いとき（ファイルを読む前）は null — 手順バーに何も言わない。 */
export type NumberStepMode = 'ask' | 'auto' | 'placeholder'

export function numberStepMode(
  rows: JudgmentRow[],
  excluded: string[],
): NumberStepMode | null {
  const sources = sourcesOf(rows)
  if (sources.length === 0) return null
  const counts = sources.map((s) => numberCandidates(rows, excluded, s).length)
  if (counts.some((n) => n >= 2)) return 'ask'
  if (counts.some((n) => n === 0)) return 'placeholder'
  return 'auto'
}

/** 候補が 2 つ以上なのにまだ選ばれていないファイル。 */
export function sourcesMissingNumber(
  rows: JudgmentRow[],
  excluded: string[],
  keyPick: Record<string, string>,
): string[] {
  return sourcesOf(rows).filter(
    (s) =>
      numberCandidates(rows, excluded, s).length >= 2 &&
      !(keyPick[s] && !excluded.includes(columnKey(s, keyPick[s]))),
  )
}

/** ③の列に ☑ を付けられるか。測定値は付けられない（サーバの組み立てが黙って
 *  無視するので、押せたのに何も起きない、を作らない）。 */
export function canTick(examples: string[]): boolean {
  return !isMeasurement(examples)
}

/** 骨格の受け口と自動つなぎの材料の算出元（1 関数）。 */
export function judgments(state: JudgmentState): { ticked: Set<string>; numbers: Set<string> } {
  // 「取り込まない」にした列の ☑ は数えない（handles にも linkable にも残さない）。
  // 外した ☑ の記録は state に残す — 「取り込む」に戻せば ☑ も戻る。
  const ticked = new Set<string>([...state.checked].filter((k) => !state.excluded.includes(k)))
  const numbers = new Set<string>()
  for (const source of sourcesOf(state.rows)) {
    const n = effectiveNumber(state, source)
    if (n) numbers.add(columnKey(source, n))
  }
  return { ticked, numbers }
}

/** 骨格の受け口 = ☑ ∪ 番号。 */
export function linkableOf(j: { ticked: Set<string>; numbers: Set<string> }): Set<string> {
  return new Set([...j.ticked, ...j.numbers])
}

/** `handles.json` に送る形 — ☑ だけ（番号は入れない）。「値でもつなぐ」で付けた
 *  ☑ は via:"fit" と term を添え、それ以外は via:"tick"。 */
export function tickedHandles(
  ticked: Set<string>,
  fitTerms: Record<string, string>,
): MaterializeHandle[] {
  return [...ticked].map((key) => {
    const at = key.indexOf('\u0000')
    const handle: MaterializeHandle = {
      source: key.slice(0, at),
      column: key.slice(at + 1),
      via: 'tick',
    }
    const term = fitTerms[key]
    if (term) {
      handle.via = 'fit'
      handle.term = term
    }
    return handle
  })
}

/** 保管庫から読み戻した handles を、③の ☑ と「値でもつなぐ」の出どころへ分ける。 */
export function hydratedChecks(handles: MaterializeHandle[]): {
  checked: Set<string>
  fitTerms: Record<string, string>
} {
  const checked = new Set<string>()
  const fitTerms: Record<string, string> = {}
  for (const h of handles) {
    const key = columnKey(h.source, h.column)
    checked.add(key)
    if (h.via === 'fit' && h.term) fitTerms[key] = h.term
  }
  return { checked, fitTerms }
}

/** 鍵を 1 つ外した写し（state を壊さず出どころの記録を消すため）。 */
export function withoutKey(map: Record<string, string>, key: string): Record<string, string> {
  if (!(key in map)) return map
  const next = { ...map }
  delete next[key]
  return next
}

/** 保管庫の handles と、いまの ☑ から作った handles に違いがあるか（順序は見ない）。
 *  設計後の見直しで、変わっていないのに PUT しないための判定。 */
export function handlesChanged(stored: MaterializeHandle[], next: MaterializeHandle[]): boolean {
  const key = (h: MaterializeHandle) =>
    [h.source, h.column, h.via ?? 'tick', h.term ?? ''].join('\u0000')
  const a = new Set(stored.map(key))
  const b = new Set(next.map(key))
  if (a.size !== b.size) return true
  for (const k of b) if (!a.has(k)) return true
  return false
}
