// 絞り込みページの見出し・小見出しを組み立てる純関数。`sets/resolve` が返す
// 構造 `{class_label, clauses:[{property_label, op, value, unit}]}`
// （契約メモ §3.4）を人の言葉の文にする — i18n は ui 側の責任なので、文言は
// `cards:set.op.*` に置き、ここでは値の整形（桁区切り・単位の前の半角空白）
// と組み立てだけを行う。react-i18next への依存は持ち込まない（呼び出し側の
// `t()` を受け取るだけ）。
//
// namespace 付きキー（cards の set.op 以下）で呼ぶのは cardTitle.ts /
// builtinFields.ts と同じ流儀 — i18n 参照チェックはファイルの既定
// namespace（このファイルには無いので common 扱い）で bare の t 呼び出しを
// 判定するため、namespace を省くと未定義キー扱いになる。

import type { SetResolveClause, SetResolveResult, SetWhereClause } from './cardsApi'

export type Translate = (key: string, options?: Record<string, unknown>) => string

const NUMBER_FORMAT = new Intl.NumberFormat('ja-JP')

/** 数値は桁区切り、それ以外はそのまま文字列化。 */
function formatValue(value: unknown): string {
  return typeof value === 'number' ? NUMBER_FORMAT.format(value) : String(value ?? '')
}

/** 数値と単位の間に半角空白を入れる（単位が無ければ触らない）。 */
function withUnit(text: string, unit: string | null | undefined): string {
  return unit ? `${text} ${unit}` : text
}

function valuesOf(value: SetWhereClause['value']): string[] {
  return Array.isArray(value) ? value.map(String) : [String(value)]
}

/** 条件 1 つを人の言葉の文にする（op ごとの文言は `cards:set.op.*`）。 */
export function formatClause(clause: SetResolveClause, t: Translate): string {
  switch (clause.op) {
    case 'gt':
      return t('cards:set.op.gt', { property: clause.property_label, value: withUnit(formatValue(clause.value), clause.unit) })
    case 'lt':
      return t('cards:set.op.lt', { property: clause.property_label, value: withUnit(formatValue(clause.value), clause.unit) })
    case 'eq':
      return t('cards:set.op.eq', { property: clause.property_label, value: withUnit(formatValue(clause.value), clause.unit) })
    case 'between': {
      const v = clause.value as { min?: unknown; max?: unknown } | undefined
      const range = `${formatValue(v?.min)}〜${withUnit(formatValue(v?.max), clause.unit)}`
      return t('cards:set.op.between', { property: clause.property_label, range })
    }
    case 'in':
      return t('cards:set.op.in', { property: clause.property_label, values: valuesOf(clause.value).join('・') })
    default:
      return clause.property_label
  }
}

/** 見出し = `<class label>`（条件が無ければそのまま）＋ `: <条件 1>、<条件 2>…`。 */
export function formatSetTitle(title: SetResolveResult['title'], t: Translate): string {
  if (title.clauses.length === 0) return title.class_label
  const parts = title.clauses.map((c) => formatClause(c, t))
  return `${title.class_label}: ${parts.join(t('cards:set.clause_join'))}`
}

/** 小見出し「<class label> N 件から」。`n`（set_count の結果）が届く前は
 *  「…」（i18next の `count` は複数形の予約語なので変数名は `n`）。 */
export function formatSetSubtitle(className: string, n: number | null, t: Translate): string {
  if (n === null) return t('cards:set.subtitle_loading', { className })
  return t('cards:set.subtitle', { className, n: NUMBER_FORMAT.format(n) })
}
