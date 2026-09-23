import { describe, expect, it } from 'vitest'
import { layoutGraph } from './graphLayout'
import type { GraphSpec } from './viewSpec'

/** 段（入次数から決まる深さ）の決定論と、LR/TD で段をどちらの軸に積むかを確かめる。
 *  題材は気象観測（分野語を書かないため）。 */

const g = (
  ids: string[],
  edges: [string, string][],
  direction?: 'LR' | 'TD',
): GraphSpec => ({
  direction,
  nodes: ids.map((id) => ({ id, label: id, kind: 'entity' })),
  edges: edges.map(([from, to]) => ({ from, to })),
})

describe('layoutGraph: LR は段を横に積む', () => {
  it('起点は左、行き先は右（x が段）', () => {
    const { positions } = layoutGraph(g(['station', 'reading'], [['station', 'reading']], 'LR'))
    expect(positions.get('station')!.x).toBeLessThan(positions.get('reading')!.x)
    expect(positions.get('station')!.y).toBe(positions.get('reading')!.y)
  })

  it('同じ段の節は同じ x で、y が登場順に並ぶ', () => {
    const { positions } = layoutGraph(
      g(['station', 'a', 'b'], [['station', 'a'], ['station', 'b']], 'LR'),
    )
    expect(positions.get('a')!.x).toBe(positions.get('b')!.x)
    expect(positions.get('a')!.y).toBeLessThan(positions.get('b')!.y)
  })
})

describe('layoutGraph: TD は段を縦に積む', () => {
  it('起点は上、行き先は下（y が段）', () => {
    const { positions } = layoutGraph(g(['station', 'reading'], [['station', 'reading']], 'TD'))
    expect(positions.get('station')!.y).toBeLessThan(positions.get('reading')!.y)
    expect(positions.get('station')!.x).toBe(positions.get('reading')!.x)
  })

  it('同じ段の節は同じ y で、x が登場順に並ぶ', () => {
    const { positions } = layoutGraph(
      g(['station', 'a', 'b'], [['station', 'a'], ['station', 'b']], 'TD'),
    )
    expect(positions.get('a')!.y).toBe(positions.get('b')!.y)
    expect(positions.get('a')!.x).toBeLessThan(positions.get('b')!.x)
  })
})

describe('layoutGraph: direction を省略すると LR 扱い', () => {
  it('x が段になる', () => {
    const { positions } = layoutGraph(g(['station', 'reading'], [['station', 'reading']]))
    expect(positions.get('station')!.x).toBeLessThan(positions.get('reading')!.x)
  })
})

describe('layoutGraph: 循環', () => {
  it('止まらずに終わり、すべて有限の座標になる', () => {
    const { positions } = layoutGraph(g(['a', 'b'], [['a', 'b'], ['b', 'a']]))
    expect(positions.size).toBe(2)
    expect([...positions.values()].every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(
      true,
    )
  })
})

describe('layoutGraph: 決定論', () => {
  it('同じ入力は毎回同じ配置になる', () => {
    const shape = g(['a', 'b', 'c'], [['a', 'b'], ['a', 'c']], 'LR')
    const r1 = layoutGraph(shape)
    const r2 = layoutGraph(shape)
    expect([...r1.positions.entries()]).toEqual([...r2.positions.entries()])
    expect(r1.width).toBe(r2.width)
    expect(r1.height).toBe(r2.height)
  })

  it('入力の GraphSpec を変更しない', () => {
    const shape = g(['a', 'b'], [['a', 'b']], 'LR')
    const before = JSON.parse(JSON.stringify(shape))
    layoutGraph(shape)
    expect(shape).toEqual(before)
  })
})

describe('layoutGraph: 幅・高さ', () => {
  it('段数・段内の最大数から一貫した幅と高さを返す', () => {
    const { width, height } = layoutGraph(
      g(['station', 'a', 'b'], [['station', 'a'], ['station', 'b']], 'LR'),
      { nodeWidth: 100, nodeHeight: 40, gap: 10 },
    )
    // LR: 2 段（station, a/b） → 幅は 2*100 + 1*10 = 210
    expect(width).toBe(210)
    // 縦は 2 個並ぶ段があるので 2*40 + 1*10 = 90
    expect(height).toBe(90)
  })
})
