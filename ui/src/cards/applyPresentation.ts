// カードに保存された「見せ方」（presentation）を、既定ビュー（`defaultViewFor`
// の結果）へ適用する共有の純関数。会話で決めた見せ方（例:「棒にして」）を
// カードに保存して（`CardSpec.presentation`）、ページのカードの描画にも同じ
// 規則で適用するために `PageChatDrawer.tsx`（プレビュー）と `CardTile.tsx`/
// `CardDetail.tsx`（ページの描画）の両方から呼ぶ。
//
// F3（見せ方の切替の本実装・`presentation.ts`/`cardPresentation.ts`/
// `ViewSwitcher.tsx`）はこの作業ツリーにまだ無い（PR F18 契約メモ参照）。
// ここは最小の実装（vega-lite の `mark` だけ）にとどめ、F3 と後で合流する。
import type { VegaLiteSpec, ViewSpec } from './viewSpec'

const ALLOWED_MARKS = new Set(['line', 'bar', 'point'])

/** `presentation`（`{mark: "line"|"bar"|"point"}`）の `mark` だけを既定ビューへ
 *  上書きする。vega-lite 以外の `view`、`presentation` が無い/許可外の値のとき
 *  は `view` をそのまま返す（入力は書き換えない）。 */
export function applyPresentation(view: ViewSpec, presentation: Record<string, unknown> | null | undefined): ViewSpec {
  if (!presentation || view.lang !== 'vega-lite') return view
  const mark = presentation.mark
  if (typeof mark !== 'string' || !ALLOWED_MARKS.has(mark)) return view
  return { ...view, spec: { ...(view.spec as VegaLiteSpec), mark } }
}
