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
  const key = node.group_iri ?? node.class_iri
  return (key ? kindRoles.get(key) : undefined) ?? REST_ROLE
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
export function buildNetworkGraph(
  resp: NetworkResponse,
  /** 束の名前（言語ごと）。省略時はサーバの label のまま。 */
  bundleName?: (className: string, count: number) => string,
): Graph {
  const g = new Graph({ type: 'undirected', multi: false })
  const kindRoles = assignKindRoles(resp.kinds)
  const sized = sizeScale(resp.nodes.map(sizeMetric), NODE_R_MIN, NODE_R_MAX)
  for (const n of resp.nodes) {
    if (g.hasNode(n.id)) continue
    const attrs: NetworkNodeAttrs = {
      label: n.kind === 'bundle' && bundleName ? bundleName(n.class_label ?? n.label, n.count) : n.label,
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
  bundleName?: (className: string, count: number) => string,
): Promise<LaidOutNetwork> {
  const response = await fetcher(includeProv)
  const graph = buildNetworkGraph(response, bundleName)
  layoutNetwork(graph)
  return { graph, response }
}

/** 件の種類の 8 色。ガイドラインの意味色（warn・success・accent など）は注意／状態の意味を持つので
 *  流用せず、互いに見分けられる別のパレットを使う（色相を散らし、ハブの青・値の灰とも離す）。 */
export const KIND_COLORS: string[] = [
  '#1b9e77', // 緑
  '#e08a00', // 橙
  '#c2579b', // 桃
  '#7b4fc9', // 紫
  '#d6452c', // 朱
  '#a3a100', // 黄緑
  '#00a6c8', // 水色
  '#7a4a1e', // 茶
]

/** 色の役割 → 色トークン（index.css の CSS 変数）。種類以外の固定色。 */
export const ROLE_VAR: Record<string, string> = {
  [REST_ROLE]: '--faint',
  [VALUE_ROLE]: '--muted',
  [HUB_ROLE]: '--link',
}

/** 役割 → CSS の色（凡例用）。種類は固定の hex、ほかは CSS 変数。 */
export function roleCss(role: string): string {
  const m = /^kind-(\d+)$/.exec(role)
  if (m && KIND_COLORS[Number(m[1])]) return KIND_COLORS[Number(m[1])]
  return `var(${ROLE_VAR[role] ?? ROLE_VAR[REST_ROLE]})`
}

/** 種類の名前。class_label が無いときは IRI のローカル名を読みくだす（生の IRI を出さない）。 */
export function kindDisplayName(label: string | null, iri: string): string | null {
  if (label && label.trim()) return label
  const local = iri.split(/[#/]/).filter(Boolean).pop() ?? ''
  let decoded = local
  try {
    decoded = decodeURIComponent(local)
  } catch {
    // 壊れた % 符号（例 a%ZZ）は読みくだせないので、そのまま使う（凡例の描画ごと落とさない）
  }
  const words = decoded
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .trim()
  return words || null
}
