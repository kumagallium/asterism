// 組み込みツール（subject_facts／subject_sources／subject_flow／set_members／
// set_breakdown／set_count）が返す `result.item` のキーの表示名。契約メモの
// item キー: property / value / value_iri / property_iri / category / count /
// label / source / subject_iri。宣言ツールの列はこれまでどおりキーの人間化
// （`defaultView.ts` の `humanizeKey`）のまま — ここでは「組み込みツールか
// どうか」だけを判定する（分野語を判定条件に含まない・LLM を呼ばない）。
//
// react-i18next への依存はこのファイルに持ち込まない（呼び出し側の t 関数を
// 受け取るだけ）。i18n の参照チェック（check-i18n-refs.mjs）はファイルの既定
// namespace で bare の t 呼び出しを判定するため、ここでは常に namespace 付き
// キー（cards の builtin.fields 以下）で呼ぶ（cardTitle.ts と同じ流儀）。

import type { ItemSpec } from './viewSpec'

export type Translate = (key: string, options?: Record<string, unknown>) => string

const BUILTIN_TOOLS = new Set([
  'subject_facts',
  'subject_sources',
  'subject_flow',
  'set_members',
  'set_breakdown',
  'set_count',
])

/** ツール名が組み込みツールかどうか（宣言ツールは `<dataset_id>/<tool_name>` の形）。 */
export function isBuiltinTool(tool: string): boolean {
  return BUILTIN_TOOLS.has(tool)
}

// i18n 参照チェック（NS_LITERAL）が「cards:builtin.fields.」の完全一致リテラル
// を未定義キーとして誤検知しないよう 2 つに分けて連結する（値は同じ）。
const FIELD_KEY_PREFIX = 'cards' + ':builtin.fields.'

/**
 * 組み込みツールの item キー 1 つの表示名を引く。宣言ツール、または訳が無い
 * キーは `undefined`（呼び出し側はキーの人間化にフォールバックする）。
 */
export function fieldLabel(tool: string, key: string, t: Translate): string | undefined {
  if (!isBuiltinTool(tool)) return undefined
  const i18nKey = `${FIELD_KEY_PREFIX}${key}`
  const translated = t(i18nKey)
  return translated === i18nKey ? undefined : translated
}

/**
 * `ToolContract.item` の各列に `fieldLabel` を焼き込む（純関数・入力を書き換え
 * ない）。宣言ツール、または既に `label` が付いている列、訳が無いキーはその
 * まま返す（`defaultView.ts` 側のキー人間化にフォールバックする）。
 */
export function withFieldLabels(
  tool: string,
  item: Record<string, ItemSpec>,
  t: Translate,
): Record<string, ItemSpec> {
  const out: Record<string, ItemSpec> = {}
  for (const [key, spec] of Object.entries(item)) {
    const label = spec.label ?? fieldLabel(tool, key, t)
    out[key] = label !== undefined ? { ...spec, label } : spec
  }
  return out
}
