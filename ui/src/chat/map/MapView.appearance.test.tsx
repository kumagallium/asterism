import type Graph from 'graphology'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { kindId } from '../../sky/skyLayout'
import { datasetNodeId } from './mapModel'
import { EMPTY_SKY } from '../../sky/skyApi'
import { setAppearance } from '../../appearance'

// WebGL の代わりに描画境界を記録する。React の effect は mount 順に実行する。
const harness = vi.hoisted(() => ({
  effects: [] as Array<() => void | (() => void)>,
  refs: 0,
  host: { getBoundingClientRect: () => ({ width: 600, height: 400 }) },
  instances: [] as Array<{ graph: unknown; settings: Record<string, unknown>; state: Record<string, number>; kill: ReturnType<typeof vi.fn>; setSettings: ReturnType<typeof vi.fn>; getCamera: () => unknown }>,
}))
vi.mock('react', async (importOriginal) => ({
  ...await importOriginal<typeof import('react')>(),
  useMemo: (fn: () => unknown) => fn(),
  useRef: (value: unknown) => ({ current: harness.refs++ === 0 ? harness.host : value }),
  useEffect: (fn: () => void | (() => void)) => { harness.effects.push(fn) },
}))
vi.mock('./mapNodePrograms', () => ({ mapNodePrograms: () => ({}) }))
vi.mock('sigma', () => ({ default: class {
  state = { x: 0.5, y: 0.5, ratio: 1, angle: 0 }
  kill = vi.fn()
  setSettings = vi.fn((next: Record<string, unknown>) => Object.assign(this.settings, next))
  graph: unknown
  settings: Record<string, unknown>
  constructor(graph: unknown, _host: unknown, settings: Record<string, unknown>) {
    this.graph = graph
    this.settings = settings
    harness.instances.push(this)
  }
  getCamera() { return { setState: (state: Record<string, number>) => Object.assign(this.state, state), getState: () => this.state } }
  on() { return this }
  refresh() {}
} }))

import { MapView } from './MapView'

afterEach(() => { vi.unstubAllGlobals(); harness.effects = []; harness.refs = 0; harness.instances = [] })

describe('地図の外観切替', () => {
  it('paletteを再読込し、選択・位置・カメラとSigmaインスタンスを保つ', () => {
    const root = { dataset: { theme: 'dark' } }
    vi.stubGlobal('document', { documentElement: root })
    vi.stubGlobal('localStorage', { setItem: vi.fn() })
    vi.stubGlobal('WebGLRenderingContext', class {})
    vi.stubGlobal('getComputedStyle', () => ({
      fontFamily: 'sans-serif',
      getPropertyValue: (name: string) => name === '--graph-kind' ? (root.dataset.theme === 'dark' ? '#c8d9ff' : '#002869') : root.dataset.theme === 'dark' ? '#e8edf6' : '#16243b',
    }))
    const sky = { ...EMPTY_SKY, datasets: [{ id: 'one', label: '一つ', created_at: '2026-01-01', kinds: [{ iri: 'urn:k:a', label: '種類', count: 1, links: [], standard: null }] }] }
    MapView({ sky, drafts: [], proposedDatasets: [], lang: 'ja', height: 400, focusIds: new Set(), selectedId: datasetNodeId('one'), highlightIds: null, removeIds: new Set(), onSelect: vi.fn(), ariaLabel: '地図', fitLabel: '全体' })
    const cleanups = harness.effects.map((effect) => effect())
    const sigma = harness.instances[0]
    expect(sigma).toBeDefined()
    Object.assign(sigma.state, { x: 0.73, y: 0.32, ratio: 0.6, angle: 0.2 })
    const camera = { ...sigma.state }
    const graph = sigma.graph as Graph
    const before = graph.export()
    const reducer = sigma.settings.nodeReducer as (id: string, data: Record<string, unknown>) => Record<string, unknown>
    const data = { role: 'entity', label: '種類', x: 1, y: 2, size: 4 }
    expect(reducer(kindId('one', 'urn:k:a'), data).color).toBe('#c8d9ff')
    expect(reducer(datasetNodeId('one'), graph.getNodeAttributes(datasetNodeId('one'))).highlighted).toBe(true)
    setAppearance('light')
    expect(reducer(kindId('one', 'urn:k:a'), data).color).toBe('#002869')
    expect(sigma.setSettings).toHaveBeenCalledWith(expect.objectContaining({ labelColor: { color: '#16243b' } }))
    expect(sigma.state).toEqual(camera)
    expect(sigma.graph).toBe(graph)
    expect(graph.export()).toEqual(before)
    expect(reducer(datasetNodeId('one'), graph.getNodeAttributes(datasetNodeId('one'))).highlighted).toBe(true)
    expect(harness.instances).toHaveLength(1)
    expect(sigma.kill).not.toHaveBeenCalled()
    for (const cleanup of cleanups) if (typeof cleanup === 'function') cleanup()
  })
})
