// 表カード（TableView）の見た目に渡す前に仕様（sort / limit / highlight）を適用する純関数。
// 契約メモ §4・§1（TableSpec の型）を参照。型は spec 担当の viewSpec.ts から import する。

import type { TableHighlight, TableSpec, Row } from './viewSpec'
export type { TableColumn, TableHighlight, TableSpec, Row } from './viewSpec'

/** 行が highlight 条件に一致するかどうか。数値と文字列の両方に対応。 */
export function matchesHighlight(row: Row, highlight: TableHighlight): boolean {
  const actual = row[highlight.when.field]
  const expected = highlight.when.value
  switch (highlight.when.op) {
    case 'eq':
      return actual === expected
    case 'gt':
      return typeof actual === 'number' && typeof expected === 'number' && actual > expected
    case 'lt':
      return typeof actual === 'number' && typeof expected === 'number' && actual < expected
    default:
      return false
  }
}

/** 行のうち最初に一致した highlight の style を返す（無ければ undefined）。 */
export function highlightStyleFor(
  row: Row,
  highlights: TableHighlight[] | undefined,
): TableHighlight['style'] | undefined {
  if (!highlights) return undefined
  for (const h of highlights) {
    if (matchesHighlight(row, h)) return h.style
  }
  return undefined
}

function compareValues(a: unknown, b: unknown): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  const as = String(a ?? '')
  const bs = String(b ?? '')
  return as.localeCompare(bs, 'ja')
}

/**
 * TableSpec の sort / limit を rows に適用する。rows も spec も変更しない（純関数）。
 * group_by は Phase 1 では TableView 側（描画時）の見出し挟みのみで、ここでは扱わない。
 */
export function applyTableSpec(spec: TableSpec, rows: Row[]): Row[] {
  let out = rows.slice()

  if (spec.sort) {
    const { field, dir } = spec.sort
    const sign = dir === 'desc' ? -1 : 1
    out = out.sort((a, b) => sign * compareValues(a[field], b[field]))
  }

  if (typeof spec.limit === 'number' && spec.limit >= 0) {
    out = out.slice(0, spec.limit)
  }

  return out
}
