import { describe, expect, it } from 'vitest'
import { HUB_H, SIDE_H, SIDE_W, xwLinkLayout, type XwSide } from './xwLinkShape'

const side = (key: string): XwSide => ({ key, name: key })

describe('xwLinkLayout', () => {
  it('lays two sides out as A —（共通の値）— B, on one line', () => {
    const { nodes, edges, height } = xwLinkLayout([side('a'), side('b')])
    expect(nodes.map((n) => n.id)).toEqual(['s:a', 'hub', 's:b'])
    expect(new Set(nodes.map((n) => n.y))).toEqual(new Set([0]))
    // 左から右へ、重ならずに並ぶ。
    const xs = nodes.map((n) => n.x)
    expect(xs[0]).toBeLessThan(xs[1])
    expect(xs[1]).toBeLessThan(xs[2])
    expect(xs[1] - xs[0]).toBeGreaterThanOrEqual(SIDE_W)
    expect(edges.map((e) => `${e.source}>${e.target}`)).toEqual(['s:a>hub', 'hub>s:b'])
    expect(height).toBe(SIDE_H + 56)
  })

  it('stacks three or more sides on the left and gathers them into one hub', () => {
    const { nodes, edges } = xwLinkLayout([side('a'), side('b'), side('c')])
    const sides = nodes.filter((n) => n.kind === 'side')
    expect(sides.map((n) => n.x)).toEqual([0, 0, 0])
    expect(sides.map((n) => n.y)).toEqual([0, SIDE_H + 18, 2 * (SIDE_H + 18)])
    // どの側からも中央へ 1 本ずつ。
    expect(edges.map((e) => `${e.source}>${e.target}`)).toEqual([
      's:a>hub',
      's:b>hub',
      's:c>hub',
    ])
    // 中央は縦の真ん中。
    const hub = nodes.find((n) => n.id === 'hub')!
    const stackH = 3 * SIDE_H + 2 * 18
    expect(hub.y).toBe((stackH - HUB_H) / 2)
  })

  it('gives every side a way out and only the far side an inlet', () => {
    const two = xwLinkLayout([side('a'), side('b')]).nodes.filter((n) => n.kind === 'side')
    expect(two.map((n) => [n.hasLeft, n.hasRight])).toEqual([
      [false, true],
      [true, false],
    ])
    // 集める形では、側はどれも出口だけ持つ（入口は中央）。
    const many = xwLinkLayout([side('a'), side('b'), side('c')]).nodes.filter(
      (n) => n.kind === 'side',
    )
    expect(many.every((n) => !n.hasLeft && n.hasRight)).toBe(true)
  })

  it('never draws a link when there is nothing to link', () => {
    expect(xwLinkLayout([])).toMatchObject({ nodes: [], edges: [] })
  })

  it('is deterministic — the same sides lay out identically', () => {
    const sides = [side('a'), side('b'), side('c')]
    expect(xwLinkLayout(sides)).toEqual(xwLinkLayout(sides))
  })
})
