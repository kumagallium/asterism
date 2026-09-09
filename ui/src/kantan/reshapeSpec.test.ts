// 「表の形」画面の判断表編集（ADR source-reshape.md §4.0/R5/R6/R8/R11/R12）。
// すべて純関数 — サーバは叩かず、返り値の spec の形だけを検証する。

import { describe, expect, it } from 'vitest'
import type {
  ReshapeFlattenOp,
  ReshapeGroup,
  ReshapeOpCounts,
  ReshapePivotOp,
  ReshapeSpec,
} from '../api'
import {
  adoptOtherUnit,
  derivedTables,
  groupDerivedRows,
  groupIsZeroAfterApply,
  groupSourceRows,
  mergeGroupInto,
  opSummary,
  setCarry,
  spellingDisplay,
  toggleGroup,
  toggleWideKey,
  totalSummary,
} from './reshapeSpec'

const CARRY = ['SID', 'sample_id']

function group(slug: string, partial: Partial<ReshapeGroup> = {}): ReshapeGroup {
  return {
    slug,
    label: partial.label ?? slug,
    unit: partial.unit ?? '-',
    table: partial.table ?? `curves__${slug}.csv`,
    enabled: partial.enabled,
    rows: partial.rows,
    members: partial.members ?? [{ label: partial.label ?? slug, unit: partial.unit ?? '-' }],
    other_units: partial.other_units,
    partner: partial.partner,
  }
}

function pivotSpec(groups: ReshapeGroup[]): ReshapeSpec {
  const op: ReshapePivotOp = {
    kind: 'pivot',
    source: 'curves.csv',
    dialect: {},
    explode: { arrays: ['x', 'y'], index: 'point_index' },
    carry: CARRY,
    label: 'prop_y',
    unit: 'unit_y',
    value: 'y',
    partner: { label: 'prop_x', unit: 'unit_x', value: 'x' },
    groups,
  }
  return { version: 1, ops: [op] }
}

describe('toggleGroup — R5「使う/使わない」', () => {
  it('指定した群だけ enabled を差し替える', () => {
    const spec = pivotSpec([group('zt'), group('seebeck')])
    const next = toggleGroup(spec, 0, 'zt', false)
    const op = next.ops[0] as ReshapePivotOp
    expect(op.groups.find((g) => g.slug === 'zt')?.enabled).toBe(false)
    expect(op.groups.find((g) => g.slug === 'seebeck')?.enabled).toBeUndefined()
  })

  it('無効化した群の表は derivedTables から消える（R7）', () => {
    const spec = pivotSpec([group('zt'), group('seebeck')])
    expect(derivedTables(spec)).toEqual(['curves__zt.csv', 'curves__seebeck.csv'])
    const next = toggleGroup(spec, 0, 'zt', false)
    expect(derivedTables(next)).toEqual(['curves__seebeck.csv'])
  })

  it('存在しない群・op を指しても何もしない', () => {
    const spec = pivotSpec([group('zt')])
    expect(toggleGroup(spec, 0, 'no-such-group', false)).toEqual(spec)
    expect(toggleGroup(spec, 5, 'zt', false)).toBe(spec)
  })

  it('mergeGroupInto で members が空になった群は使うに戻せない（レビュー指摘）', () => {
    const spec = pivotSpec([group('thermopower'), group('seebeck')])
    const merged = mergeGroupInto(spec, 0, 'thermopower', 'seebeck')
    const next = toggleGroup(merged, 0, 'thermopower', true)
    const g = (next.ops[0] as ReshapePivotOp).groups.find((g) => g.slug === 'thermopower')!
    expect(g.enabled).toBe(false)
    expect(g.members).toEqual([])
  })
})

describe('adoptOtherUnit — R5/R6「同じ単位とみなす」', () => {
  it('other_units の候補を members へ移し、other_units から消す', () => {
    const spec = pivotSpec([
      group('electrical-conductivity', {
        label: 'Electrical conductivity',
        unit: 'ohm^(-1)*m^(-1)',
        other_units: [{ label: 'Electrical conductivity', unit: 'S*m^(-1)', rows: 7214 }],
      }),
    ])
    const next = adoptOtherUnit(spec, 0, 'electrical-conductivity', {
      label: 'Electrical conductivity',
      unit: 'S*m^(-1)',
    })
    const g = (next.ops[0] as ReshapePivotOp).groups[0]
    expect(g.members).toContainEqual({ label: 'Electrical conductivity', unit: 'S*m^(-1)' })
    expect(g.other_units).toEqual([])
  })

  it('一致する候補が無ければ何もしない', () => {
    const spec = pivotSpec([group('zt', { other_units: [] })])
    const next = adoptOtherUnit(spec, 0, 'zt', { label: 'ZT', unit: 'unknown' })
    expect(next).toEqual(spec)
  })
})

describe('mergeGroupInto — R12「別の群に合流」', () => {
  it('from の members を into へ移し、from を無効化して空にする', () => {
    const spec = pivotSpec([
      group('thermopower', { label: 'thermopower', members: [{ label: 'thermopower', unit: 'V/K' }] }),
      group('seebeck', {
        label: 'Seebeck coefficient',
        unit: 'V/K',
        members: [{ label: 'Seebeck coefficient', unit: 'V/K' }],
      }),
    ])
    const next = mergeGroupInto(spec, 0, 'thermopower', 'seebeck')
    const op = next.ops[0] as ReshapePivotOp
    const from = op.groups.find((g) => g.slug === 'thermopower')!
    const into = op.groups.find((g) => g.slug === 'seebeck')!
    expect(from.enabled).toBe(false)
    expect(from.members).toEqual([])
    expect(into.members).toEqual([
      { label: 'Seebeck coefficient', unit: 'V/K' },
      { label: 'thermopower', unit: 'V/K' },
    ])
  })

  it('合流後、同じ (label, unit) がどちらの群にも重複して残らない', () => {
    const shared = { label: 'ZT', unit: '-' }
    const spec = pivotSpec([
      group('zt-a', { members: [shared] }),
      group('zt-b', { members: [shared] }),
    ])
    const next = mergeGroupInto(spec, 0, 'zt-a', 'zt-b')
    const op = next.ops[0] as ReshapePivotOp
    const allMembers = op.groups.flatMap((g) => g.members.map((m) => `${m.label} ${m.unit}`))
    const dupes = allMembers.filter((k, i) => allMembers.indexOf(k) !== i)
    expect(dupes).toEqual([])
  })

  it('同じ群どうし・存在しない群を指したら何もしない', () => {
    const spec = pivotSpec([group('zt')])
    expect(mergeGroupInto(spec, 0, 'zt', 'zt')).toBe(spec)
    expect(mergeGroupInto(spec, 0, 'zt', 'no-such')).toEqual(spec)
  })
})

describe('setCarry / toggleWideKey', () => {
  it('setCarry は carry をまるごと書き換える', () => {
    const spec = pivotSpec([group('zt')])
    const next = setCarry(spec, 0, ['SID', 'DOI', 'composition'])
    expect((next.ops[0] as ReshapePivotOp).carry).toEqual(['SID', 'DOI', 'composition'])
  })

  it('toggleWideKey は flatten の wide.keys に足す/外す', () => {
    const spec: ReshapeSpec = {
      version: 1,
      ops: [
        {
          kind: 'flatten',
          source: 'samples.csv',
          dialect: {},
          column: 'sample_info',
          carry: ['SID'],
          long: { table: 'samples__sample_info.csv', fields: ['category', 'comment'] },
          wide: { table: 'samples__sample_info-wide.csv', keys: ['MaterialFamily'] },
        },
      ],
    }
    const added = toggleWideKey(spec, 0, 'Form')
    expect(added.ops[0].kind === 'flatten' && added.ops[0].wide.keys).toEqual([
      'MaterialFamily',
      'Form',
    ])
    const removed = toggleWideKey(added, 0, 'MaterialFamily')
    expect(removed.ops[0].kind === 'flatten' && removed.ops[0].wide.keys).toEqual(['Form'])
  })

  it('toggleWideKey は flatten でない op には何もしない', () => {
    const spec = pivotSpec([group('zt')])
    expect(toggleWideKey(spec, 0, 'Form')).toEqual(spec)
  })
})

describe('opSummary / totalSummary — R11 の帯（適用前後）', () => {
  it('適用済み: counts の実測を読む', () => {
    const spec = pivotSpec([group('zt'), group('seebeck', { enabled: false })])
    const counts: Record<string, ReshapeOpCounts> = {
      '0': {
        source_rows: 233103,
        rows_unmatched: 86015,
        rows_matched: { zt: 147088 },
        tables: { 'curves__zt.csv': 285296 },
        dropped_non_numeric: 3,
        truncated_length_mismatch: 5,
        elements_matched: 285304,
      },
    }
    const s = opSummary(spec, 0, counts)
    expect(s).toMatchObject({
      kind: 'pivot',
      sourceRows: 233103,
      rowsOut: 285296,
      dropped: 3,
      truncated: 5,
      tableCount: 1, // seebeck is disabled — only zt's table exists
    })
    const total = totalSummary(spec, counts)
    expect(total).toEqual({ sourceRows: 233103, derivedRows: 285296, dropped: 3, truncated: 5 })
  })

  it('未適用: pivot は groups[].rows から見積もる', () => {
    const spec = pivotSpec([
      group('zt', { rows: 20684 }),
      group('seebeck', { rows: 13871, enabled: false }),
    ])
    const s = opSummary(spec, 0, {})
    // 無効な seebeck の rows は見積もりに入らない。
    expect(s.rowsOut).toBe(20684)
    expect(s.tableCount).toBe(1)
    const total = totalSummary(spec, {})
    expect(total.derivedRows).toBe(20684)
  })

  it('適用前: 入力行数は counts でなく op.source_rows を使う（0 行と誤表示しない）', () => {
    const spec = pivotSpec([group('zt', { rows: 17 })])
    ;(spec.ops[0] as ReshapePivotOp).source_rows = 233103
    const s = opSummary(spec, 0, {})
    expect(s.sourceRows).toBe(233103)
    const total = totalSummary(spec, {})
    expect(total.sourceRows).toBe(233103)
  })

  it('同じソースを読む2つの op があっても入力行数は1回だけ数える', () => {
    const spec: ReshapeSpec = {
      version: 1,
      ops: [
        { kind: 'explode', source: 'curves.csv', dialect: {}, table: 't1.csv', arrays: ['a', 'b'], carry: [] },
        pivotSpec([group('zt')]).ops[0],
      ],
    }
    const counts: Record<string, ReshapeOpCounts> = {
      '0': { source_rows: 100, elements_in: 10, rows_out: 8, dropped_non_numeric: 1, truncated_length_mismatch: 1 },
      '1': {
        source_rows: 100,
        rows_unmatched: 0,
        rows_matched: { zt: 100 },
        tables: { 'curves__zt.csv': 100 },
        dropped_non_numeric: 0,
        truncated_length_mismatch: 0,
        elements_matched: 100,
      },
    }
    expect(totalSummary(spec, counts).sourceRows).toBe(100)
  })
})

describe('flatten の entries_empty — 空の項目は「捨てた要素」に数えない', () => {
  function flattenSpec(): ReshapeSpec {
    const op: ReshapeFlattenOp = {
      kind: 'flatten',
      source: 'samples.csv',
      dialect: {},
      column: 'sample_info',
      carry: CARRY,
      long: { table: 'samples__sample_info.csv', fields: [] },
      wide: { table: 'samples__sample_info-wide.csv', keys: [], fields: [] },
    }
    return { version: 1, ops: [op] }
  }

  it('opSummary は dropped を返さず、emptyEntries に entries_empty を置く', () => {
    const spec = flattenSpec()
    const counts: Record<string, ReshapeOpCounts> = {
      '0': {
        source_rows: 46,
        entries_in: 300,
        rows_out: 55,
        entries_empty: 245,
        wide_rows_out: 46,
        wide_key_collisions: 0,
      },
    }
    const s = opSummary(spec, 0, counts)
    expect(s.dropped).toBeUndefined()
    expect(s.emptyEntries).toBe(245)
  })

  it('totalSummary の dropped は 0 のまま（flatten の entries_empty を混ぜない）', () => {
    const spec = flattenSpec()
    const counts: Record<string, ReshapeOpCounts> = {
      '0': {
        source_rows: 46,
        entries_in: 300,
        rows_out: 55,
        entries_empty: 245,
        wide_rows_out: 46,
        wide_key_collisions: 0,
      },
    }
    expect(totalSummary(spec, counts).dropped).toBe(0)
  })
})

describe('groupSourceRows / groupDerivedRows / groupIsZeroAfterApply — 「行数」の二重の意味を混ぜない', () => {
  it('groupSourceRows: 有効・無効どちらでも常に g.rows（元の表で一致した行数）を返す', () => {
    const enabledSpec = pivotSpec([group('zt', { rows: 100 })])
    const enabledGroup = (enabledSpec.ops[0] as ReshapePivotOp).groups[0]
    expect(groupSourceRows(enabledGroup)).toBe(100)

    const disabledSpec = pivotSpec([group('thermopower', { rows: 20, enabled: false })])
    const disabledGroup = (disabledSpec.ops[0] as ReshapePivotOp).groups[0]
    expect(groupSourceRows(disabledGroup)).toBe(20)
  })

  it('groupDerivedRows: 無効な群は counts があっても undefined（派生表を作らない）、有効な群は実測を返す', () => {
    const spec = pivotSpec([
      group('zt', { rows: 100 }),
      group('thermopower', { rows: 20, enabled: false }),
    ])
    const [enabledGroup, disabledGroup] = (spec.ops[0] as ReshapePivotOp).groups
    const counts: Record<string, ReshapeOpCounts> = {
      '0': {
        source_rows: 120,
        rows_unmatched: 20,
        rows_matched: { zt: 100 },
        tables: { 'curves__zt.csv': 95, 'curves__thermopower.csv': 0 },
        dropped_non_numeric: 0,
        truncated_length_mismatch: 0,
        elements_matched: 95,
      },
    }
    // 未適用（counts 無し）は有効な群でも undefined — 見積もりで代用しない。
    expect(groupDerivedRows(enabledGroup, 0, {})).toBeUndefined()
    // 無効な群は counts があっても常に undefined。
    expect(groupDerivedRows(disabledGroup, 0, counts)).toBeUndefined()
    // 有効な群は counts があれば実測（source_rows の一致数とは別の数）を返す。
    expect(groupDerivedRows(enabledGroup, 0, counts)).toBe(95)
  })

  it('無効化した群は 0 行でも警告しない', () => {
    const spec = pivotSpec([group('thermopower', { rows: 20, enabled: false })])
    const g = (spec.ops[0] as ReshapePivotOp).groups[0]
    const counts: Record<string, ReshapeOpCounts> = {
      '0': {
        source_rows: 46,
        rows_unmatched: 46,
        rows_matched: {},
        tables: {},
        dropped_non_numeric: 0,
        truncated_length_mismatch: 0,
        elements_matched: 0,
      },
    }
    expect(groupIsZeroAfterApply(g, 0, counts)).toBe(false)
  })

  it('有効な群は適用後に 0 行なら警告フラグが立つ', () => {
    const spec = pivotSpec([group('thermopower', { rows: 20 })])
    const g = (spec.ops[0] as ReshapePivotOp).groups[0]
    const counts: Record<string, ReshapeOpCounts> = {
      '0': {
        source_rows: 46,
        rows_unmatched: 46,
        rows_matched: {},
        tables: { 'curves__thermopower.csv': 0 },
        dropped_non_numeric: 0,
        truncated_length_mismatch: 0,
        elements_matched: 0,
      },
    }
    expect(groupIsZeroAfterApply(g, 0, counts)).toBe(true)
  })

  it('opSummary(pivot).zeroRowGroups: 適用後に 0 行の有効な群の数を数える', () => {
    const spec = pivotSpec([group('zt', { rows: 100 }), group('thermopower', { rows: 20 })])
    const counts: Record<string, ReshapeOpCounts> = {
      '0': {
        source_rows: 120,
        rows_unmatched: 20,
        rows_matched: { zt: 100 },
        tables: { 'curves__zt.csv': 100, 'curves__thermopower.csv': 0 },
        dropped_non_numeric: 0,
        truncated_length_mismatch: 0,
        elements_matched: 100,
      },
    }
    const s = opSummary(spec, 0, counts)
    expect(s.zeroRowGroups).toBe(1)
    // 未適用（counts 無し）は判定できないので undefined。
    expect(opSummary(spec, 0, {}).zeroRowGroups).toBeUndefined()
  })
})

describe('spellingDisplay — 「同じ単位とみなす」で移した別単位 member を区別する', () => {
  it('member の単位が群の代表単位と同じなら表記だけ', () => {
    expect(spellingDisplay({ label: 'Seebeck coefficient', unit: 'V/K' }, 'V/K')).toBe(
      'Seebeck coefficient',
    )
  })

  it('member の単位が群の代表単位と違えば単位を括弧で添える（綴りが同じ/似ていても区別できる）', () => {
    // adoptOtherUnit で移した候補は group.unit と違う単位を持ちうる。二重空白の
    // 綴り違いは目に見えないので、単位の添え書きだけで区別できれば十分
    // （実機報告: 「Seebeck coefficient, Seebeck coefficient」と並んでいた）。
    expect(spellingDisplay({ label: 'Seebeck coefficient', unit: 'V/K' }, 'mV/K')).toBe(
      'Seebeck coefficient (V/K)',
    )
  })

  it('mergeGroupInto/adoptOtherUnit 後の群を並べても、単位違いの member だけ括弧が付く', () => {
    const zt = group('zt', {
      unit: 'mV/K',
      members: [
        { label: 'Seebeck coefficient', unit: 'mV/K' },
        { label: 'Seebeck coefficient ', unit: 'V/K' },
      ],
    })
    const display = zt.members.map((m) => spellingDisplay(m, zt.unit))
    expect(display).toEqual(['Seebeck coefficient', 'Seebeck coefficient  (V/K)'])
  })
})
