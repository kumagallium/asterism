// ③の ☑ と ④の番号 — 骨格の受け口（linkable）と handles.json の算出元。
// 守る線: linkable = ☑ ∪ 番号、handles = ☑ だけ（番号は入れない・K48）。
import { describe, expect, it } from 'vitest'
import { columnKey } from './settledStore'
import {
  canTick,
  effectiveNumber,
  handlesChanged,
  hydratedChecks,
  isMeasurement,
  judgments,
  linkableOf,
  numberCandidates,
  numberStepMode,
  sourcesMissingNumber,
  tickedHandles,
  withoutKey,
  type JudgmentRow,
} from './judgments'

const F = 'xrd.csv'
const k = (column: string, source = F) => columnKey(source, column)

const pre = (column: string, example = 'A-001', source = F): JudgmentRow => ({
  source,
  column,
  origin: 'preamble',
  examples: [example],
})
const tab = (column: string, examples: string[] = ['x'], source = F): JudgmentRow => ({
  source,
  column,
  origin: 'table',
  examples,
})

describe('judgments: ticked と numbers の分離', () => {
  it('候補が 1 つの前置き列は自動で番号になり、☑ には入らない', () => {
    const rows = [pre('カード番号'), tab('組成')]
    const j = judgments({ rows, excluded: [], keyPick: {}, checked: [k('組成')] })
    expect([...j.numbers]).toEqual([k('カード番号')])
    expect([...j.ticked]).toEqual([k('組成')])
  })

  it('linkable = ticked ∪ numbers、handles = ticked だけ（番号は handles に入らない）', () => {
    const rows = [pre('カード番号'), tab('組成')]
    const j = judgments({ rows, excluded: [], keyPick: {}, checked: [k('組成')] })
    expect(linkableOf(j)).toEqual(new Set([k('カード番号'), k('組成')]))
    const handles = tickedHandles(j.ticked, {})
    expect(handles.map((h) => h.column)).toEqual(['組成'])
    expect(handles.map((h) => h.column)).not.toContain('カード番号')
  })

  it('☑ が 1 つも無くても番号は linkable に入る（handles は空）', () => {
    const j = judgments({ rows: [pre('カード番号')], excluded: [], keyPick: {}, checked: [] })
    expect(linkableOf(j).size).toBe(1)
    expect(tickedHandles(j.ticked, {})).toEqual([])
  })

  it('候補が 2 つ以上で未選択なら番号は無い。選べばその列だけが番号', () => {
    const rows = [pre('カード番号'), pre('試料ID', 'S-9')]
    expect(judgments({ rows, excluded: [], keyPick: {}, checked: [] }).numbers.size).toBe(0)
    const picked = judgments({ rows, excluded: [], keyPick: { [F]: '試料ID' }, checked: [] })
    expect([...picked.numbers]).toEqual([k('試料ID')])
    expect(picked.ticked.size).toBe(0)
  })

  it('測定値と「取り込まない」列は番号の候補にならない', () => {
    const rows = [pre('カード番号'), pre('温度', '25.5'), pre('メモ', 'x')]
    expect(numberCandidates(rows, [k('メモ')], F)).toEqual(['カード番号'])
    expect(effectiveNumber({ rows, excluded: [k('メモ')], keyPick: {} }, F)).toBe('カード番号')
  })

  it('1 行ごとの値（table）は番号の候補にならない', () => {
    expect(numberCandidates([tab('id')], [], F)).toEqual([])
  })

  it('ファイルごとに別々に決まる', () => {
    const rows = [pre('a', '1', 'x.csv'), pre('b', '2', 'y.csv'), pre('c', '3', 'y.csv')]
    const j = judgments({ rows, excluded: [], keyPick: { 'y.csv': 'c' }, checked: [] })
    expect(j.numbers).toEqual(new Set([k('a', 'x.csv'), k('c', 'y.csv')]))
  })

  it('人が付けた ☑ はそのまま ticked（意味を変えない）', () => {
    const checked = [k('組成'), k('産地')]
    const j = judgments({ rows: [tab('組成'), tab('産地')], excluded: [], keyPick: {}, checked })
    expect(j.ticked).toEqual(new Set(checked))
  })
})

describe('③の ☑ 可否', () => {
  it('測定値（小数を含む数値だけの列）は付けられない', () => {
    expect(canTick(['25.5', '30.1'])).toBe(false)
    expect(isMeasurement(['1e-3', '2'])).toBe(true)
  })

  it('整数だけ・文字・空は付けられる（番号や名前になりうる）', () => {
    expect(canTick(['1', '2', '3'])).toBe(true)
    expect(canTick(['Fe2O3'])).toBe(true)
    expect(canTick([''])).toBe(true)
  })
})

describe('④の畳み（numberStepMode）', () => {
  it('候補が 2 つ以上のファイルがあれば ask（段を出す）', () => {
    expect(numberStepMode([pre('a'), pre('b')], [])).toBe('ask')
  })

  it('候補が 1 つなら auto（番号は自動で決まりました）', () => {
    expect(numberStepMode([pre('a'), tab('c')], [])).toBe('auto')
  })

  it('候補が 0 なら placeholder（番号は仮置き・⑤で確認）', () => {
    expect(numberStepMode([tab('a'), tab('b')], [])).toBe('placeholder')
    expect(numberStepMode([pre('t', '1.5')], [])).toBe('placeholder')
  })

  it('取り込まない列を外すと畳み方が変わる', () => {
    const rows = [pre('a'), pre('b')]
    expect(numberStepMode(rows, [])).toBe('ask')
    expect(numberStepMode(rows, [k('b')])).toBe('auto')
  })

  it('ファイルが混ざるとき: 1 つでも 2 候補なら ask、次に 0 候補があれば placeholder', () => {
    expect(numberStepMode([pre('a', '1', 'x'), pre('b', '2', 'y'), pre('c', '3', 'y')], [])).toBe(
      'ask',
    )
    expect(numberStepMode([pre('a', '1', 'x'), tab('b', ['q'], 'y')], [])).toBe('placeholder')
  })

  it('列の事実がまだ無いときは null（手順バーに何も言わない）', () => {
    expect(numberStepMode([], [])).toBeNull()
  })

  it('未選択のファイルだけが missing に残る', () => {
    const rows = [pre('a', '1', 'x'), pre('b', '2', 'x'), pre('c', '3', 'y')]
    expect(sourcesMissingNumber(rows, [], {})).toEqual(['x'])
    expect(sourcesMissingNumber(rows, [], { x: 'a' })).toEqual([])
  })
})

describe('handles の出どころ（via／term）', () => {
  it('「値でもつなぐ」で付けた ☑ だけ via:"fit"・term 付き。それ以外は tick', () => {
    const handles = tickedHandles(new Set([k('組成'), k('産地')]), {
      [k('組成')]: 'https://example.org/term/composition',
    })
    expect(handles).toEqual([
      { source: F, column: '組成', via: 'fit', term: 'https://example.org/term/composition' },
      { source: F, column: '産地', via: 'tick' },
    ])
  })

  it('☑ が無ければ fitTerms が残っていても handles に出ない', () => {
    expect(tickedHandles(new Set(), { [k('組成')]: 'https://x/t' })).toEqual([])
  })

  it('読み戻し: via 無しの旧形式は tick、fit は term を戻す', () => {
    const back = hydratedChecks([
      { source: F, column: '産地' },
      { source: F, column: '組成', via: 'fit', term: 'https://x/t' },
      { source: F, column: '年', via: 'tick' },
    ])
    expect(back.checked).toEqual(new Set([k('産地'), k('組成'), k('年')]))
    expect(back.fitTerms).toEqual({ [k('組成')]: 'https://x/t' })
  })

  it('読み戻して送り直しても同じ（見直しで ☑ が消えない・出どころも保たれる）', () => {
    const before = [
      { source: F, column: '組成', via: 'fit' as const, term: 'https://x/t' },
      { source: F, column: '産地', via: 'tick' as const },
    ]
    const back = hydratedChecks(before)
    expect(tickedHandles(back.checked, back.fitTerms)).toEqual(before)
  })

  it('withoutKey は鍵が無ければ同じ参照を返し、あれば外した写しを返す', () => {
    const m = { a: '1', b: '2' }
    expect(withoutKey(m, 'z')).toBe(m)
    expect(withoutKey(m, 'a')).toEqual({ b: '2' })
    expect(m).toEqual({ a: '1', b: '2' })
  })
})

describe('「取り込まない」にした列の ☑ と番号', () => {
  it('取り込まない列の ☑ は ticked にも linkable にも handles にも残らない', () => {
    const rows = [pre('カード番号'), tab('組成'), tab('温度', ['1', '2'])]
    const j = judgments({
      rows,
      excluded: [k('組成')],
      keyPick: {},
      checked: [k('組成'), k('温度')],
    })
    expect([...j.ticked]).toEqual([k('温度')])
    expect(linkableOf(j).has(k('組成'))).toBe(false)
    expect(tickedHandles(j.ticked, {}).map((h) => h.column)).toEqual(['温度'])
  })

  it('「取り込む」に戻せば ☑ も戻る（外した ☑ の記録は消さない）', () => {
    const rows = [tab('組成')]
    const base = { rows, keyPick: {}, checked: [k('組成')] }
    expect(judgments({ ...base, excluded: [k('組成')] }).ticked.size).toBe(0)
    expect(judgments({ ...base, excluded: [] }).ticked.has(k('組成'))).toBe(true)
  })

  it('④で選んだ番号の列を取り込まないにすると、番号は選ばれたことにならない', () => {
    const rows = [pre('カード番号'), pre('試料ID', 'S-9'), pre('ロット', 'L-1')]
    const state = { rows, excluded: [k('試料ID')], keyPick: { [F]: '試料ID' } }
    // 残る候補は 2 つ → 未選択扱い（番号は無い・選び直しを求める）
    expect(effectiveNumber(state, F)).toBeUndefined()
    expect(sourcesMissingNumber(rows, state.excluded, state.keyPick)).toEqual([F])
  })

  it('選んだ番号の列を外し、残る候補が 1 つなら、その列が自動で番号になる', () => {
    const rows = [pre('カード番号'), pre('試料ID', 'S-9')]
    const state = { rows, excluded: [k('試料ID')], keyPick: { [F]: '試料ID' } }
    expect(effectiveNumber(state, F)).toBe('カード番号')
  })
})

describe('handlesChanged（設計後の見直しの ☑ 保存）', () => {
  const h = (column: string, via?: 'tick' | 'fit', term?: string) => ({
    source: F,
    column,
    ...(via ? { via } : {}),
    ...(term ? { term } : {}),
  })

  it('同じ中身なら順序が違っても変更なし。via 無しは tick と同じ', () => {
    expect(handlesChanged([h('a'), h('b', 'tick')], [h('b', 'tick'), h('a', 'tick')])).toBe(false)
  })

  it('増えた・減った・出どころが変わったら変更あり', () => {
    expect(handlesChanged([h('a')], [h('a'), h('b')])).toBe(true)
    expect(handlesChanged([h('a'), h('b')], [h('a')])).toBe(true)
    expect(handlesChanged([h('a')], [h('a', 'fit', 'https://x/t')])).toBe(true)
  })

  it('両方空なら変更なし（送らない）', () => {
    expect(handlesChanged([], [])).toBe(false)
  })
})
