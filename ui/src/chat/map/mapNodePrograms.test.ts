import { describe, expect, it, vi } from 'vitest'

// WebGL の代わりに、各描画層へ渡す picking index と半径を記録する。
vi.mock('sigma/rendering', () => {
  class Circle {
    calls: unknown[][] = []
    processVisibleItem(...args: unknown[]) { this.calls.push(args) }
  }
  return {
    NodeCircleProgram: Circle,
    drawDiscNodeLabel: vi.fn(),
    drawDiscNodeHover: vi.fn(),
    createNodeCompoundProgram: vi.fn((layers: unknown[]) => layers),
  }
})

import { mapNodePrograms } from './mapNodePrograms'
import { createNodeCompoundProgram } from 'sigma/rendering'

describe('地図の記号とクリック範囲', () => {
  it('compound の通常・hoverラベルとも紙色の共通drawerを使う', () => {
    const drawLabel = vi.fn()
    vi.mocked(createNodeCompoundProgram).mockClear()
    mapNodePrograms('#fbfcfe', drawLabel)
    for (const call of vi.mocked(createNodeCompoundProgram).mock.calls) {
      expect(call[1]).toBe(drawLabel)
      expect(call[2]).toBe(drawLabel)
    }
  })
  it('○ と ⊙ の内円は紙色、中心点は元の色で、全層が同じ picking index を保つ', () => {
    const programs = mapNodePrograms('#fbfcfe') as unknown as Record<string, Array<new () => {
      calls: [number, number, { size: number; color: string }][]
      processVisibleItem(index: number, offset: number, data: unknown): void
    }>>
    const data = { size: 10, color: '#002869', x: 1, y: 2 }
    for (const [symbol, sizes] of [['map-outline', [10, 6.2]], ['map-dataset', [10, 6.2, 3.2]]] as const) {
      const layers = programs[symbol].map((Program) => new Program())
      layers.forEach((layer) => layer.processVisibleItem(27, 3, data))
      expect(layers.map((layer) => layer.calls[0][0])).toEqual(sizes.map(() => 27))
      expect(layers.map((layer) => layer.calls[0][2].size)).toEqual([...sizes])
      expect(layers[1].calls[0][2].color).toBe('#fbfcfe')
      expect(layers[0].calls[0][2].color).toBe('#002869')
      if (layers[2]) expect(layers[2].calls[0][2].color).toBe('#002869')
    }
    expect(data.size).toBe(10)
  })
})
