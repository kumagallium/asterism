// 単位の CURIE / IRI を短い表示名に落とす。QUDT の記号表（Phase 2）はまだ引かない
// ので、`unit:K` → `K` のように局所名をそのまま出す当面の決定論だけを持つ。

/**
 * `unit:K` → `K`、`unit:W-PER-M-K` → `W-PER-M-K`（そのまま）、
 * フル IRI は末尾の局所名。値が無ければ空文字。
 */
export function unitLabel(unit: string | null | undefined): string {
  if (!unit) return ''
  const local = unit.split(/[:#/]/).pop()
  return local || unit
}
