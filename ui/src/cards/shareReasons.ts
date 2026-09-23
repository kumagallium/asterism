// `materials.shareable_reasons`（契約メモ §2・固定語彙・固定順: own_data →
// unknown_origin → unknown_license → not_redistributable → no_materials）を
// 文言キーへ写すだけの純粋関数。判断はしない — サーバが確定した理由をそのまま
// i18n キーへ変換する（`cards:detail.reason_*`）。CardDetail の verdict 行と
// ExportDialog の 409 表示の両方から使う（契約メモ §6）。

/** 未知の reason（将来サーバが語彙を増やしたとき）は変換せずそのまま返す —
 *  UI を落とさない保守側の既定（キーが無ければ i18next はキー文字列を出す
 *  だけで、例外にはならない）。 */
const REASON_I18N_KEY: Readonly<Record<string, string>> = {
  own_data: 'detail.reason_own_data',
  unknown_origin: 'detail.reason_unknown_origin',
  unknown_license: 'detail.reason_unknown_license',
  not_redistributable: 'detail.reason_not_redistributable',
  no_materials: 'detail.reason_no_materials',
}

export function shareReasonKey(reason: string): string {
  return REASON_I18N_KEY[reason] ?? reason
}

/** `reasons` を、渡された `t` で文言に変換した配列にする（順序は保つ）。 */
export function shareReasonLabels(reasons: string[], t: (key: string) => string): string[] {
  return reasons.map((reason) => t(shareReasonKey(reason)))
}

/** 「・」区切りの 1 文にする（verdict 行・export ダイアログの両方で使う）。
 *  空配列は空文字列。 */
export function formatShareReasons(reasons: string[], t: (key: string) => string): string {
  return shareReasonLabels(reasons, t).join('・')
}
