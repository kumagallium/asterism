import { describe, expect, it, vi } from 'vitest'
import type { NetworkNode, NetworkResponse } from './networkApi'
import {
  alwaysLabeled,
  assignKindRoles,
  buildNetworkGraph,
  hashPosition,
  layoutIterations,
  layoutNetwork,
  loadLaidOutNetwork,
  neighborhoodOf,
  searchNodes,
} from './networkModel'

// 架空の分野（星・観測所）の作り物。
const NS = 'https://example.org/sky#'
const node = (id: string, over: Partial<NetworkNode> = {}): NetworkNode => ({
  id,
  kind: 'entity',
  label: id,
  class_iri: `${NS}Star`,
  class_label: '星',
  dataset_id: 'd1',
  count: 1,
  degree: 1,
  set_spec: null,
  ...over,
})

function fixture(): NetworkResponse {
  const nodes: NetworkNode[] = [
    node('s1', { degree: 3 }),
    node('s2', { degree: 2, label: 'Vega' }),
    node('o1', { class_iri: `${NS}Obs`, class_label: '観測所', degree: 2 }),
    node('v:region:north', { kind: 'value', class_iri: null, class_label: null, label: '地域: 北', degree: 3 }),
    node('b1', { kind: 'bundle', count: 3001, degree: 1, label: 'Record 3,001 件' }),
    node('h1', { kind: 'hub', class_iri: `${NS}Hub`, class_label: '共通', degree: 2 }),
  ]
  return {
    nodes,
    edges: [
      { source: 's1', target: 'v:region:north', label: '地域' },
      { source: 's2', target: 'v:region:north', label: '地域' },
      { source: 'o1', target: 'v:region:north', label: '地域' },
      { source: 's1', target: 'o1', label: '観測' },
      { source: 'b1', target: 'h1', label: '' },
      { source: 'h1', target: 's2', label: '' },
      // 落とされる: 重複・自分への線・存在しない点
      { source: 'o1', target: 's1', label: '観測' },
      { source: 's1', target: 's1', label: '' },
      { source: 's1', target: 'nope', label: '' },
    ],
    kinds: [
      { class_iri: `${NS}Star`, class_label: '星', count: 3003 },
      { class_iri: `${NS}Obs`, class_label: '観測所', count: 1 },
    ],
    stats: { entities: 3004, nodes: 6, edges: 6, values: 1, bundles: 1 },
    truncated: false,
  }
}

describe('assignKindRoles', () => {
  it('上位 8 種類に kind-0〜7、残りは rest。API の順のまま', () => {
    const kinds = Array.from({ length: 10 }, (_, i) => ({ class_iri: `k${i}` }))
    const roles = assignKindRoles(kinds)
    expect(roles.get('k0')).toBe('kind-0')
    expect(roles.get('k7')).toBe('kind-7')
    expect(roles.get('k8')).toBe('rest')
    expect(roles.get('k9')).toBe('rest')
  })
  it('同じ入力で同じ割り当て（決定論）', () => {
    const kinds = [{ class_iri: 'b' }, { class_iri: 'a' }]
    expect([...assignKindRoles(kinds)]).toEqual([...assignKindRoles(kinds)])
    expect(assignKindRoles(kinds).get('b')).toBe('kind-0')
  })
})

describe('buildNetworkGraph', () => {
  const g = buildNetworkGraph(fixture())
  it('点と線を変換し、重複・自分・欠けた相手の線を落とす', () => {
    expect(g.order).toBe(6)
    expect(g.size).toBe(6)
  })
  it('色の役割: 件は種類、値・ハブは固定、束は中身の種類', () => {
    expect(g.getNodeAttribute('s1', 'role')).toBe('kind-0')
    expect(g.getNodeAttribute('o1', 'role')).toBe('kind-1')
    expect(g.getNodeAttribute('v:region:north', 'role')).toBe('value')
    expect(g.getNodeAttribute('h1', 'role')).toBe('hub')
    expect(g.getNodeAttribute('b1', 'role')).toBe('kind-0')
  })
  it('大きさ: 束は件数、ほかは次数。最小〜最大に収まり、大きいほど大きい', () => {
    const size = (id: string) => g.getNodeAttribute(id, 'size') as number
    expect(size('b1')).toBe(16)
    expect(size('s1')).toBeGreaterThan(size('s2') - 1)
    g.forEachNode((id) => {
      expect(size(id)).toBeGreaterThanOrEqual(3)
      expect(size(id)).toBeLessThanOrEqual(16)
    })
  })
  it('初期位置は id のハッシュそのもの', () => {
    expect(g.getNodeAttribute('s1', 'x')).toBe(hashPosition('s1').x)
  })
})

describe('hashPosition', () => {
  it('同じ id は同じ位置、別の id は別の位置、円盤の中', () => {
    expect(hashPosition('a')).toEqual(hashPosition('a'))
    expect(hashPosition('a')).not.toEqual(hashPosition('b'))
    for (const id of ['a', 'b', 'https://example.org/x#1']) {
      const p = hashPosition(id)
      expect(Math.hypot(p.x, p.y)).toBeLessThanOrEqual(100.0001)
    }
  })
})

describe('layoutNetwork', () => {
  it('同じ入力で同じ配置（固定回数・決定論）', () => {
    const a = buildNetworkGraph(fixture())
    const b = buildNetworkGraph(fixture())
    layoutNetwork(a)
    layoutNetwork(b)
    a.forEachNode((id, attrs) => {
      expect(attrs.x).toBe(b.getNodeAttribute(id, 'x'))
      expect(attrs.y).toBe(b.getNodeAttribute(id, 'y'))
      expect(Number.isFinite(attrs.x)).toBe(true)
    })
    // 初期位置から動いている
    expect(a.getNodeAttribute('s1', 'x')).not.toBe(hashPosition('s1').x)
  })
  it('500 点以上（Barnes-Hut）でも同じ配置', () => {
    const big = (): NetworkResponse => {
      const nodes = Array.from({ length: 520 }, (_, i) => node(`n${i}`, { degree: 2 }))
      const edges = nodes.map((n, i) => ({ source: n.id, target: `n${(i + 1) % 520}`, label: '' }))
      return { ...fixture(), nodes, edges }
    }
    const a = buildNetworkGraph(big())
    const b = buildNetworkGraph(big())
    layoutNetwork(a)
    layoutNetwork(b)
    expect(a.getNodeAttribute('n7', 'x')).toBe(b.getNodeAttribute('n7', 'x'))
  })
  it('空のグラフでも落ちない', () => {
    expect(() => layoutNetwork(buildNetworkGraph({ ...fixture(), nodes: [], edges: [] }))).not.toThrow()
  })
  it('繰り返し回数は 100〜400、点が多いほど少ない', () => {
    expect(layoutIterations(10)).toBe(400)
    expect(layoutIterations(776)).toBe(200)
    expect(layoutIterations(100000)).toBe(100)
  })
})

describe('neighborhoodOf / searchNodes / alwaysLabeled', () => {
  const g = buildNetworkGraph(fixture())
  it('載せた点と相手', () => {
    expect([...neighborhoodOf(g, 'v:region:north')].sort()).toEqual(['o1', 's1', 's2', 'v:region:north'])
    expect(neighborhoodOf(g, null).size).toBe(0)
    expect(neighborhoodOf(g, 'ない').size).toBe(0)
  })
  it('名前で探す: 大文字小文字を無視・前方一致が先', () => {
    expect(searchNodes(g, 'vega')).toEqual(['s2'])
    expect(searchNodes(g, '')).toEqual([])
    expect(searchNodes(g, 'S')[0]).toBe('s1')
  })
  it('値・ハブ・束は名前を常に出す', () => {
    expect(alwaysLabeled('entity')).toBe(false)
    expect(alwaysLabeled('value')).toBe(true)
    expect(alwaysLabeled('hub')).toBe(true)
    expect(alwaysLabeled('bundle')).toBe(true)
  })
})

describe('loadLaidOutNetwork（API をモック）', () => {
  it('取得して配置まで進め、include_prov をそのまま渡す', async () => {
    const fetcher = vi.fn(async () => fixture())
    const r = await loadLaidOutNetwork(fetcher, true)
    expect(fetcher).toHaveBeenCalledWith(true)
    expect(r.graph.order).toBe(6)
    expect(r.response.stats.nodes).toBe(6)
  })
})
