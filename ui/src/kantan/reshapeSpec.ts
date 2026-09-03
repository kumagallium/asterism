/**
 * 「表の形」画面（ADR source-reshape.md §4.0/R12）の判断表を編集する純関数群。
 *
 * すべて `ReshapeSpec` を受け取り、変更後の新しい `ReshapeSpec` を返す（元は
 * 変えない）。副作用もサーバ呼び出しも持たない — 呼び出し側（KantanWizard）が
 * 返り値を state に置き、必要になったタイミングで `applyStagingReshape` に渡す。
 * 対象の op / 群が見つからないときは何もせず、渡された `spec` をそのまま返す
 * （呼び出し側が存在確認を重ねなくて済むように — フェイルセーフ）。
 */
import type {
  ReshapeGroup,
  ReshapeOp,
  ReshapeOpCounts,
  ReshapePivotOp,
  ReshapeSpec,
  ReshapeSpelling,
} from '../api'

function isPivot(op: ReshapeOp): op is ReshapePivotOp {
  return op.kind === 'pivot'
}

function mapOp(spec: ReshapeSpec, opIndex: number, fn: (op: ReshapeOp) => ReshapeOp): ReshapeSpec {
  if (opIndex < 0 || opIndex >= spec.ops.length) return spec
  return { ...spec, ops: spec.ops.map((op, i) => (i === opIndex ? fn(op) : op)) }
}

function mapPivot(
  spec: ReshapeSpec,
  opIndex: number,
  fn: (op: ReshapePivotOp) => ReshapePivotOp,
): ReshapeSpec {
  return mapOp(spec, opIndex, (op) => (isPivot(op) ? fn(op) : op))
}

function sameSpelling(a: ReshapeSpelling, b: ReshapeSpelling): boolean {
  return a.label === b.label && a.unit === b.unit
}

/**
 * R5「使う／使わない」: pivot の群 1 つの `enabled` を差し替える。`mergeGroupInto`
 * で合流済み（`members` が空）の群を `enabled: true` に戻すことだけは拒む —
 * 戻すと一致しようがない空の派生表がサーバ側で無言のまま作られる（レビュー
 * 指摘）。合流を取り消したいときは、合流先の群からもう一度分ける操作が要る
 * （まだ無い）。
 */
export function toggleGroup(
  spec: ReshapeSpec,
  opIndex: number,
  slug: string,
  enabled: boolean,
): ReshapeSpec {
  return mapPivot(spec, opIndex, (op) => ({
    ...op,
    groups: op.groups.map((g) => {
      if (g.slug !== slug) return g
      if (enabled && g.members.length === 0) return g
      return { ...g, enabled }
    }),
  }))
}

/**
 * R5/R6「同じ単位とみなす」: `other_units` の1候補を、その群の `members` へ
 * 移す（同時に `other_units` から取り除く — 二重に持たない）。候補が見つから
 * なければ何もしない。
 */
export function adoptOtherUnit(
  spec: ReshapeSpec,
  opIndex: number,
  slug: string,
  candidate: ReshapeSpelling,
): ReshapeSpec {
  return mapPivot(spec, opIndex, (op) => ({
    ...op,
    groups: op.groups.map((g): ReshapeGroup => {
      if (g.slug !== slug) return g
      const others = g.other_units ?? []
      const hit = others.find((o) => sameSpelling(o, candidate))
      if (!hit) return g
      return {
        ...g,
        members: [...g.members, { label: hit.label, unit: hit.unit }],
        other_units: others.filter((o) => o !== hit),
      }
    }),
  }))
}

/**
 * R12「別の群に合流」: `from` の `members` を `into` へ移し、`from` を無効化
 * する。`from` の `members` は空にする — サーバの検証（R6）は `enabled` を
 * 見ずに (label, unit) の重複を拒否するので、無効化するだけでは同じ組が
 * `into` と `from` の両方に残って再適用が 422 になる。`into` に既にある
 * (label, unit) は足さない（重複させない）。どちらかの群が見つからない、
 * または同じ群を指していたら何もしない。
 */
export function mergeGroupInto(
  spec: ReshapeSpec,
  opIndex: number,
  fromSlug: string,
  intoSlug: string,
): ReshapeSpec {
  if (fromSlug === intoSlug) return spec
  return mapPivot(spec, opIndex, (op) => {
    const from = op.groups.find((g) => g.slug === fromSlug)
    const into = op.groups.find((g) => g.slug === intoSlug)
    if (!from || !into) return op
    return {
      ...op,
      groups: op.groups.map((g): ReshapeGroup => {
        if (g.slug === fromSlug) return { ...g, members: [], enabled: false }
        if (g.slug === intoSlug) {
          const merged = [...g.members]
          for (const m of from.members) {
            if (!merged.some((x) => sameSpelling(x, m))) merged.push({ label: m.label, unit: m.unit })
          }
          return { ...g, members: merged }
        }
        return g
      }),
    }
  })
}

/**
 * R8「追加で持ち回る列」: op の `carry` をまるごと書き換える。explode・pivot・
 * flatten のどれも `carry` を同じ形で持つ（ADR §4.0）。
 */
export function setCarry(spec: ReshapeSpec, opIndex: number, columns: string[]): ReshapeSpec {
  return mapOp(spec, opIndex, (op) => ({ ...op, carry: [...columns] }))
}

/** §4.3「wide にする項目」: flatten の `wide.keys` に 1 つ足す／外す。flatten
 *  でない op には何もしない。 */
export function toggleWideKey(spec: ReshapeSpec, opIndex: number, key: string): ReshapeSpec {
  return mapOp(spec, opIndex, (op) => {
    if (op.kind !== 'flatten') return op
    const keys = op.wide.keys
    const next = keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key]
    return { ...op, wide: { ...op.wide, keys: next } }
  })
}

/**
 * apply() が作る派生表名の順序付き list — サーバの `reshape.derived_tables()`
 * と同じ規則（R5/R7: `enabled: false` の群は含めない）。編集した判断表が
 * どの派生表を作ることになるか、適用前にも分かる。
 */
export function derivedTables(spec: ReshapeSpec): string[] {
  const names: string[] = []
  for (const op of spec.ops) {
    if (op.kind === 'explode') {
      names.push(op.table)
    } else if (op.kind === 'pivot') {
      for (const g of op.groups) {
        if (g.enabled === false) continue
        names.push(g.table)
      }
    } else {
      names.push(op.long.table)
      names.push(op.wide.table)
    }
  }
  return names
}

/** 1 op（1 タブ）ぶんの数の要約。表の形画面のタブと帯が読む。 */
export interface ReshapeOpSummary {
  kind: 'explode' | 'pivot' | 'flatten'
  /** 入力行数（実測できるとき）。 */
  sourceRows?: number
  /** この op が生む行の合計（pivot は有効な群の表を合算）。 */
  rowsOut?: number
  /** 捨てた要素（数値でない・空だった）。 */
  dropped?: number
  /** 切った要素（並行する配列の長さが食い違い、短い方に切った）。 */
  truncated?: number
  /** いま生成する派生表の枚数。 */
  tableCount: number
  /** pivot のみ: 有効にした群の数。 */
  enabledGroups?: number
  /** pivot のみ: 無効にした（=表を作らない）群の数。 */
  disabledGroups?: number
  /** pivot のみ: まだ `members` に移していない「同じとみなす」候補の総数
   *  （0 でも構わない判断だが、タブの ⚠ 表示の材料になる）。 */
  unresolvedOtherUnits?: number
}

/**
 * `opIndex` 番目の op の要約。`counts` に実測（R11、適用後）があればそれを使い、
 * 無ければ pivot の既定判断表が持つ `groups[].rows`（全行走査の一致行数、
 * propose() が埋める）から見積もる。explode・flatten は適用してみないと行数が
 * 分からないので、未適用のときは `rowsOut` を返さない。
 */
export function opSummary(
  spec: ReshapeSpec,
  opIndex: number,
  counts: Record<string, ReshapeOpCounts>,
): ReshapeOpSummary {
  const op = spec.ops[opIndex]
  if (!op) return { kind: 'explode', tableCount: 0 }
  const c = counts[String(opIndex)]

  if (op.kind === 'explode') {
    return {
      kind: 'explode',
      sourceRows: c?.source_rows,
      rowsOut: c?.rows_out,
      dropped: c?.dropped_non_numeric,
      truncated: c?.truncated_length_mismatch,
      tableCount: 1,
    }
  }

  if (op.kind === 'pivot') {
    const enabled = op.groups.filter((g) => g.enabled !== false)
    const disabled = op.groups.filter((g) => g.enabled === false)
    const unresolvedOtherUnits = op.groups.reduce((n, g) => n + (g.other_units?.length ?? 0), 0)
    const rowsOut = c?.tables
      ? Object.values(c.tables).reduce((a, b) => a + b, 0)
      : enabled.reduce((a, g) => a + (g.rows ?? 0), 0) || undefined
    return {
      kind: 'pivot',
      sourceRows: c?.source_rows,
      rowsOut,
      dropped: c?.dropped_non_numeric,
      truncated: c?.truncated_length_mismatch,
      tableCount: enabled.length,
      enabledGroups: enabled.length,
      disabledGroups: disabled.length,
      unresolvedOtherUnits,
    }
  }

  // flatten
  return {
    kind: 'flatten',
    sourceRows: c?.source_rows,
    rowsOut: c?.rows_out,
    dropped: c?.entries_empty,
    tableCount: 2,
  }
}

/** 上部の常時の帯（R12）: 入力行数 → 派生表行数・捨てた要素・切った要素。 */
export interface ReshapeTotalSummary {
  sourceRows: number
  derivedRows: number
  dropped: number
  truncated: number
}

/**
 * すべての op を合算した帯の数字。同じソースファイルを 2 つの op が読んでいても
 * 入力行数は 1 回しか数えない（distinct source）。適用済み（`counts` に実測が
 * ある）op は実測を、未適用の pivot は `groups[].rows` の見積もりを使う —
 * explode・flatten は適用するまで行数が分からないので、その分は 0 のまま
 * （実測が付き次第、真の数に更新される）。
 */
export function totalSummary(
  spec: ReshapeSpec,
  counts: Record<string, ReshapeOpCounts>,
): ReshapeTotalSummary {
  const perSource = new Map<string, number>()
  let derivedRows = 0
  let dropped = 0
  let truncated = 0
  spec.ops.forEach((op, i) => {
    const s = opSummary(spec, i, counts)
    if (s.sourceRows !== undefined) {
      perSource.set(op.source, Math.max(perSource.get(op.source) ?? 0, s.sourceRows))
    }
    derivedRows += s.rowsOut ?? 0
    dropped += s.dropped ?? 0
    truncated += s.truncated ?? 0
  })
  return {
    sourceRows: [...perSource.values()].reduce((a, b) => a + b, 0),
    derivedRows,
    dropped,
    truncated,
  }
}
