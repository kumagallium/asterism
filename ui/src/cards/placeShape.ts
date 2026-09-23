// PlaceView（「データを置く」画面）の型と純関数。契約メモ §4・§6.3（担当 c2-place）。
// ここには API 呼び出しも JSX も置かない — テストしやすい決定論の組み立てだけ
// （見出し直後の 1 文・3 状態の表示判定・フッターの集計）。分野語の辞書は持たない。
//
// 統合時の注記（notes 参照）: 当初 `PlaceView.tsx` と大文字小文字だけが違う
// `placeView.ts` として置かれていたが、大文字小文字を区別しないファイル
// システム（既定の macOS/Windows）では `tsc -b` が TS1261/TS2305/TS1149 で
// 必ず失敗するため、統合段でこのファイルを `placeShape.ts` に改名した。
// `PlaceCommitSubject`/`PlaceCommitSet`/`PlaceCommitResult` は
// `PlaceView.tsx` が `./cardsApi` の同名の型（`SubjectItem`/`SetSpec` 込み）に
// 一本化したため、ここでは持たない。

/** 形の一致（契約 §4.1）。`type_id: null` なら「棚を作る」へ渡す。 */
export interface ShapeMatch {
  type_id: string | null
  dialect: string
  matched_columns: string[]
  unmatched_columns: string[]
  confidence: number
}

/** `place/inspect` が返すファイル 1 つぶんのプレビュー。 */
export interface PlaceFilePreview {
  name: string
  columns: string[]
  rows: number
}

export interface PlaceInspectResult {
  files: PlaceFilePreview[]
  match: ShapeMatch
  signature_label: string | null
  signature_dataset_id: string | null
}

/** 1 件の照合の 3 状態（契約 §4.2 / 引き継ぎ書 §5.4）。 */
export type PlaceMatchState = 'linked' | 'ambiguous' | 'own_only'

export interface PlaceCandidate {
  iri: string
  label: string
  count: number
}

/** `place/subjects` が行ごとに返す 1 件。 */
export interface PlaceSubjectItem {
  value: string
  rows: number
  match: PlaceMatchState
  iri?: string
  candidates?: PlaceCandidate[]
}

/** 3 状態 → pill の見た目（試作 `.pill.ok/.warn/.mute`）。判断は 1 か所（ここ）だけに置く。 */
export interface PillInfo {
  tone: 'ok' | 'warn' | 'mute'
  match: PlaceMatchState
}

export function pillFor(match: PlaceMatchState): PillInfo {
  switch (match) {
    case 'linked':
      return { tone: 'ok', match }
    case 'ambiguous':
      return { tone: 'warn', match }
    case 'own_only':
      return { tone: 'mute', match }
  }
}

/** UI 言語に依存しない文言の指し先。`key` は `cards:` 名前空間のキー（`place.` 込み）、
 *  `vars` は i18next の補間 `{{name}}` に渡す値。文字列そのものはここでは組み立てない
 *  （UI 言語を切り替えても disparate に日本語が残らないよう、文言はロケール側に置く）。 */
export interface TextFragment {
  key: string
  vars?: Record<string, string | number>
}

/** 見出し（K23: 1 文だけ）。ファイル名だけから決まる。`datasetLabel` があれば
 *  そちらを使う（KantanWizard から「ページに戻る」で入ってきた既存データセット
 *  の経路 — ファイル名ではなくデータセット名を見せる。契約メモ §6.3 の該当項）。 */
export function readHeading(fileName: string, datasetLabel?: string | null): TextFragment {
  return { key: 'place.reading', vars: { name: datasetLabel ?? fileName } }
}

/** 見出し直後の small（K23）。呼び出し側が「・」で繋いで 1 文の体裁を保つ
 *  （試作 HTML と同じ流儀）。`signatureLabel` が無い（type_id null）ときは、
 *  まだ形が無いことだけを言う。 */
export function readSummary(
  file: { rows: number; columnCount: number },
  match: ShapeMatch,
  signatureLabel: string | null,
): TextFragment[] {
  const parts: TextFragment[] = [{ key: 'place.rows_cols', vars: { rows: file.rows, cols: file.columnCount } }]
  if (match.type_id && signatureLabel) {
    parts.push({
      key: 'place.matched_type',
      vars: { type: signatureLabel, matched: match.matched_columns.length, total: file.columnCount },
    })
    parts.push({ key: 'place.no_design_needed' })
    parts.push({ key: 'place.not_shelved_yet' })
  } else {
    parts.push({ key: 'place.no_match_title' })
  }
  return parts
}

/** フッターの集計文（契約 §6.3「N 件のうち M 件が棚とつながり、K 件の事実が
 *  使えます」）。`factCount` は棚とつながった行の合計行数（決定論・LLM 不要）。 */
export function footerSummary(items: PlaceSubjectItem[]): TextFragment {
  const total = items.length
  const linked = items.filter((i) => i.match === 'linked')
  const factCount = linked.reduce((sum, i) => sum + i.rows, 0)
  return { key: 'place.footer_summary', vars: { total, linked: linked.length, facts: factCount } }
}
