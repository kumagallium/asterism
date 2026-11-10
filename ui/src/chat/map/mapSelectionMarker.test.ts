import { describe, expect, it, vi } from 'vitest'
import { attachMapSelectionMarker } from './mapSelectionMarker'

/** SVG の属性とイベントを記録する。WebGL や DOM 環境には依存しない。 */
function fixture() {
  const elements: Array<ReturnType<typeof element>> = []
  function element() {
    return {
      style: {} as Record<string, string>, attrs: {} as Record<string, string>,
      classList: { add: vi.fn() }, appendChild: vi.fn(), remove: vi.fn(),
      setAttribute(key: string, value: string) { this.attrs[key] = value },
    }
  }
  const host = {
    ownerDocument: { createElementNS: () => { const el = element(); elements.push(el); return el } },
    appendChild: vi.fn(),
  }
  const listeners = new Map<string, Set<() => void>>()
  let selected: string | null = 'chosen'
  let data: { x: number; y: number; size: number; hidden?: boolean } | undefined = { x: 0.2, y: 0.3, size: 5 }
  let viewport = { x: 200, y: 160 }
  let radius = 9
  const sigma = {
    on: (name: string, fn: () => void) => {
      if (!listeners.has(name)) listeners.set(name, new Set())
      listeners.get(name)!.add(fn)
    },
    off: (name: string, fn: () => void) => { listeners.get(name)?.delete(fn) },
    getNodeDisplayData: vi.fn(() => data),
    framedGraphToViewport: vi.fn(() => viewport),
    scaleSize: vi.fn(() => radius),
  }
  const detach = attachMapSelectionMarker(sigma as never, host as unknown as HTMLElement, () => selected, { fill: '#002869', paper: '#fbfcfe' })
  const emit = (name: string) => { for (const fn of [...listeners.get(name) ?? []]) fn() }
  return {
    marker: elements[0], star: elements[1], sigma, detach, emit, listeners, host,
    select: (id: string | null) => { selected = id },
    node: (value: typeof data) => { data = value },
    camera: (position: typeof viewport, size: number) => { viewport = position; radius = size },
  }
}

describe('選択した一点だけに添える星', () => {
  it('キャッシュをviewportへ変換し、カメラ・resize後の位置と描画半径に追従する', () => {
    const f = fixture()
    expect(f.sigma.framedGraphToViewport).toHaveBeenCalledWith({ x: 0.2, y: 0.3, size: 5 })
    expect(f.sigma.scaleSize).toHaveBeenCalledWith(5)
    expect(f.marker.style).toMatchObject({ left: '200px', top: '141px', pointerEvents: 'none' })
    expect(f.marker.attrs['aria-hidden']).toBe('true')
    expect(f.star.attrs).toMatchObject({ fill: '#002869', stroke: '#fbfcfe' })
    f.camera({ x: 85, y: 90 }, 14)
    f.emit('afterRender')
    expect(f.marker.style).toMatchObject({ left: '85px', top: '66px', display: 'block' })
    expect(f.host.appendChild).toHaveBeenCalledTimes(1)
  })
  it('clear・選択解除・消えた点・隠れた点で残像を消す', () => {
    const f = fixture()
    f.emit('afterClear')
    expect(f.marker.style.display).toBe('none')
    f.emit('afterRender')
    expect(f.marker.style.display).toBe('block')
    f.select(null)
    f.emit('afterRender')
    expect(f.marker.style.display).toBe('none')
    f.select('missing')
    f.node(undefined)
    f.emit('afterRender')
    expect(f.marker.style.display).toBe('none')
    f.node({ x: 0, y: 0, size: 5, hidden: true })
    f.emit('afterRender')
    expect(f.marker.style.display).toBe('none')
  })
  it('kill時にSVGと全listenerを片付け、二重解除にも耐える', () => {
    const f = fixture()
    f.emit('kill')
    expect([...f.listeners.values()].every((set) => set.size === 0)).toBe(true)
    expect(f.marker.remove).toHaveBeenCalledTimes(1)
    f.detach()
    expect(f.marker.remove).toHaveBeenCalledTimes(1)
  })
})
