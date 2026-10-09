// 全体グラフ（値でつなぐ網）の純関数: API の返り値 → graphology のグラフ・色の割り当て・
// 決定論の配置・探す・載せたときの強調。描画（sigma）は NetworkView.tsx。
// 契約メモ contract_network_view.md §3.2。配置は乱数も時刻も使わない（同じデータは同じ絵）。
import Graph from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import { sizeScale } from './kindOverview'
import type { NetworkNode, NetworkNodeKind, NetworkResponse } from './networkApi'

/** 件の種類に色を付ける数。残りは灰。 */
export const KIND_COLOR_COUNT = 8
export const REST_ROLE = 'rest'
export const VALUE_ROLE = 'value'
export const HUB_ROLE = 'hub'
export const NODE_R_MIN = 3
export const NODE_R_MAX = 16

/** `kinds`（件数の多い順）の上位 8 種類に `kind-0`〜`kind-7`。順序は API の順のまま。 */
export function assignKindRoles(
  kinds: { class_iri: string }[],
  max = KIND_COLOR_COUNT,
): Map<string, string> {
  const out = new Map<string, string>()
  for (const k of kinds) {
    if (out.has(k.class_iri)) continue
    out.set(k.class_iri, out.size < max ? `kind-${out.size}` : REST_ROLE)
  }
  return out
}

/** 点の色の役割。件・束は種類で、値の点とハブは固定。 */
export function roleOf(node: NetworkNode, kindRoles: Map<string, string>): string {
  if (node.kind === 'value') return VALUE_ROLE
  if (node.kind === 'hub') return HUB_ROLE
  return (node.class_iri ? kindRoles.get(node.class_iri) : undefined) ?? REST_ROLE
}

/** 大きさの元の数。束は中の件数、それ以外は次数。 */
export function sizeMetric(node: NetworkNode): number {
  return node.kind === 'bundle' ? node.count : node.degree
}

/** FNV-1a（32bit）。salt で別の値を作る。 */
function fnv1a(text: string, salt: number): number {
  let h = (0x811c9dc5 ^ salt) >>> 0
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h >>> 0
}

/** 点の id のハッシュから作る初期位置（半径 100 の円盤の中）。乱数・時刻は使わない。 */
export function hashPosition(id: string): { x: number; y: number } {
  const u = fnv1a(id, 0) / 0xffffffff
  const v = fnv1a(id, 0x9e3779b9) / 0xffffffff
  const r = 100 * Math.sqrt(u)
  const a = 2 * Math.PI * v
  return { x: r * Math.cos(a), y: r * Math.sin(a) }
}

export interface NetworkNodeAttrs {
  label: string
  nodeKind: NetworkNodeKind
  role: string
  size: number
  x: number
  y: number
  classLabel: string | null
  datasetId: string | null
  count: number
  degree: number
  setSpec: NetworkNode['set_spec']
}

/** API の返り値 → graphology のグラフ。位置は hashPosition（まだ配置していない）。
 *  重複する点・存在しない点への線・自分への線・同じ組の線は落とす。 */
export function buildNetworkGraph(resp: NetworkResponse): Graph {
  const g = new Graph({ type: 'undirected', multi: false })
  const kindRoles = assignKindRoles(resp.kinds)
  const sized = sizeScale(resp.nodes.map(sizeMetric), NODE_R_MIN, NODE_R_MAX)
  for (const n of resp.nodes) {
    if (g.hasNode(n.id)) continue
    const attrs: NetworkNodeAttrs = {
      label: n.label,
      nodeKind: n.kind,
      role: roleOf(n, kindRoles),
      size: sized(sizeMetric(n)),
      ...hashPosition(n.id),
      classLabel: n.class_label,
      datasetId: n.dataset_id,
      count: n.count,
      degree: n.degree,
      setSpec: n.set_spec,
    }
    g.addNode(n.id, attrs as unknown as Record<string, unknown>)
  }
  for (const e of resp.edges) {
    if (e.source === e.target || !g.hasNode(e.source) || !g.hasNode(e.target)) continue
    if (g.hasEdge(e.source, e.target)) continue
    g.addEdge(e.source, e.target, { label: e.label })
  }
  return g
}

/** 点の数に応じた繰り返し回数（100〜400）。多いほど少なく（計算時間の都合）。 */
export function layoutIterations(order: number): number {
  if (order < 200) return 400
  if (order < 500) return 300
  if (order < 2000) return 200
  return 100
}

export const BARNES_HUT_FROM = 500

/** ForceAtlas2 を同期で固定回数だけ回して止める（グラフを直接書き換える）。
 *  初期位置が決まっていれば結果は決まる。 */
export function layoutNetwork(g: Graph): void {
  if (g.order === 0) return
  const inferred = forceAtlas2.inferSettings(g)
  forceAtlas2.assign(g, {
    iterations: layoutIterations(g.order),
    settings: { ...inferred, barnesHutOptimize: g.order >= BARNES_HUT_FROM },
  })
}

/** 載せた（選んだ）点とそのつながる相手の id。 */
export function neighborhoodOf(g: Graph, id: string | null): Set<string> {
  const out = new Set<string>()
  if (!id || !g.hasNode(id)) return out
  out.add(id)
  g.forEachNeighbor(id, (n) => out.add(n))
  return out
}

/** 名前で探す。大文字小文字を区別せず部分一致。前方一致→短い名前→id の順。 */
export function searchNodes(g: Graph, query: string, limit = 20): string[] {
  const q = query.trim().toLowerCase()
  if (!q) return []
  const hits: { id: string; label: string; starts: boolean }[] = []
  g.forEachNode((id, a) => {
    const label = String(a.label ?? '')
    const low = label.toLowerCase()
    if (low.includes(q)) hits.push({ id, label, starts: low.startsWith(q) })
  })
  hits.sort(
    (x, y) =>
      Number(y.starts) - Number(x.starts) ||
      x.label.length - y.label.length ||
      (x.id < y.id ? -1 : x.id > y.id ? 1 : 0),
  )
  return hits.slice(0, limit).map((h) => h.id)
}

/** 名前を常に出す点か（値・ハブ・束）。件は拡大したときか載せたときだけ。 */
export function alwaysLabeled(kind: NetworkNodeKind): boolean {
  return kind !== 'entity'
}

export interface LaidOutNetwork {
  graph: Graph
  response: NetworkResponse
}

/** 取得 → グラフ化 → 配置。fetcher は差し替えられる（テストはモック）。 */
export async function loadLaidOutNetwork(
  fetcher: (includeProv: boolean) => Promise<NetworkResponse>,
  includeProv: boolean,
): Promise<LaidOutNetwork> {
  const response = await fetcher(includeProv)
  const graph = buildNetworkGraph(response)
  layoutNetwork(graph)
  return { graph, response }
}

/** 色の役割 → 色トークン（index.css の CSS 変数）。凡例（CSS）と sigma（実際の色に解決）で共有。 */
export const ROLE_VAR: Record<string, string> = {
  'kind-0': '--entity',
  'kind-1': '--accent',
  'kind-2': '--prov-result',
  'kind-3': '--primary',
  'kind-4': '--accent-strong',
  'kind-5': '--info-fg',
  'kind-6': '--warn-fg',
  'kind-7': '--success-fg',
  [REST_ROLE]: '--faint',
  [VALUE_ROLE]: '--muted',
  [HUB_ROLE]: '--link',
}
