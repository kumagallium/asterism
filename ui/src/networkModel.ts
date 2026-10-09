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
/** 役割ごとの大きさの範囲（[最小, 最大]）。 */
export const ENTITY_R: [number, number] = [1.5, 5]
export const VALUE_R: [number, number] = [5, 11]
export const BUNDLE_R: [number, number] = [9, 14]

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
  // 大きさは役割ごとに別の尺度（件は小さな点・値は中くらい・束は大きく）。1 本の尺度だと
  // 束（3,001 件など）に引っぱられて件の点まで大きくなり、網が団子に見えた（実データで確認）。
  const scaleOf = (kinds: NetworkNodeKind[], lo: number, hi: number) =>
    sizeScale(resp.nodes.filter((n) => kinds.includes(n.kind)).map(sizeMetric), lo, hi)
  const sizedBy: Record<NetworkNodeKind, (n: number | undefined) => number> = {
    entity: scaleOf(['entity', 'hub'], ENTITY_R[0], ENTITY_R[1]),
    hub: scaleOf(['entity', 'hub'], ENTITY_R[0], ENTITY_R[1]),
    value: scaleOf(['value'], VALUE_R[0], VALUE_R[1]),
    bundle: scaleOf(['bundle'], BUNDLE_R[0], BUNDLE_R[1]),
  }
  const sized = (n: NetworkNode) => sizedBy[n.kind](sizeMetric(n))
  for (const n of resp.nodes) {
    if (g.hasNode(n.id)) continue
    const attrs: NetworkNodeAttrs = {
      label: n.kind === 'bundle' && bundleName ? bundleName(n.class_label ?? n.label, n.count) : n.label,
      nodeKind: n.kind,
      role: roleOf(n, kindRoles),
      size: sized(n),
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

/** 点の数に応じた繰り返し回数（120〜500）。多いほど少なく（計算時間の都合。776 点で 400 回＝約 0.7 秒）。 */
export function layoutIterations(order: number): number {
  if (order < 200) return 500
  if (order < 1000) return 400
  if (order < 2000) return 250
  return 120
}

export const BARNES_HUT_FROM = 500

/** つながっている点のまとまり（連結成分）。大きい順・同じ大きさは先頭の id 順（決定論）。 */
export function connectedParts(g: Graph): string[][] {
  const seen = new Set<string>()
  const parts: string[][] = []
  for (const start of [...g.nodes()].sort()) {
    if (seen.has(start)) continue
    const part: string[] = []
    const stack = [start]
    seen.add(start)
    while (stack.length > 0) {
      const id = stack.pop() as string
      part.push(id)
      g.forEachNeighbor(id, (nb) => {
        if (!seen.has(nb)) {
          seen.add(nb)
          stack.push(nb)
        }
      })
    }
    parts.push(part.sort())
  }
  return parts.sort((a, b) => b.length - a.length || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
}

/** まとまり 1 つの半径の目安（面積がおよそ点の数に比例。小さなまとまりも読める大きさに底上げ）。 */
export const PART_MIN_N = 60
export const PART_UNIT = 10
export const PART_GAP = 2 * PART_UNIT
export function partRadius(n: number): number {
  return PART_UNIT * Math.sqrt(n + PART_MIN_N)
}

/** 1 つのまとまりを ForceAtlas2 で配置し、重心を原点・半径を PART_UNIT·√n にそろえた位置を返す。 */
function layoutPart(g: Graph, ids: string[]): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>()
  if (ids.length === 1) {
    out.set(ids[0], { x: 0, y: 0 })
    return out
  }
  const sub = new Graph({ type: 'undirected', multi: false })
  for (const id of ids) {
    sub.addNode(id, {
      x: g.getNodeAttribute(id, 'x'),
      y: g.getNodeAttribute(id, 'y'),
      size: g.getNodeAttribute(id, 'size'),
    })
  }
  for (const id of ids) {
    g.forEachNeighbor(id, (nb) => {
      if (id < nb && !sub.hasEdge(id, nb)) sub.addEdge(id, nb)
    })
  }
  const inferred = forceAtlas2.inferSettings(sub)
  forceAtlas2.assign(sub, {
    iterations: layoutIterations(sub.order),
    settings: {
      ...inferred,
      // 重力を弱くした線形の FA2。実データ（国 62・年ごとの記録 682・年 11・地域 6）で設定を比べ、
      // 同じ地域の国どうしの距離 ÷ 違う地域の国どうしの距離が 0.23 と最も小さかった（LinLog は 1.0＝
      // 地域で寄らない・重力 1 は 0.81）。年のように全員につながる値の点に引かれて団子になるのを避ける。
      gravity: 0.1,
      scalingRatio: 2,
      barnesHutOptimize: sub.order >= BARNES_HUT_FROM,
    },
  })
  let cx = 0
  let cy = 0
  sub.forEachNode((_, a) => {
    cx += a.x
    cy += a.y
  })
  cx /= sub.order
  cy /= sub.order
  let radius = 0
  sub.forEachNode((_, a) => {
    radius = Math.max(radius, Math.hypot(a.x - cx, a.y - cy))
  })
  const scale = radius > 0 ? partRadius(ids.length) / radius : 1
  sub.forEachNode((id, a) => out.set(id, { x: (a.x - cx) * scale, y: (a.y - cy) * scale }))
  return out
}

/** 配置（グラフを直接書き換える）。まとまりごとに ForceAtlas2 を固定回数回し、大きい順に
 *  左から行に並べる（行の幅は全体の面積から決め、横長の枠に合わせて 1.6 倍）。
 *  1 つの力学配置で全部を回すと、小さなまとまり（XRD のカードなど）が大きな網の縁に押しつけられ、
 *  名前が重なった（実データで確認）。初期位置が決まっていれば結果は決まる。 */
export function layoutNetwork(g: Graph): void {
  if (g.order === 0) return
  const parts = connectedParts(g).map((ids) => ({
    pos: layoutPart(g, ids),
    r: partRadius(ids.length),
    cx: 0,
    cy: 0,
  }))
  const area = parts.reduce((s, p) => s + (2 * p.r + PART_GAP) ** 2, 0)
  const width = Math.max(2 * parts[0].r, 1.6 * Math.sqrt(area))
  const rows: (typeof parts)[] = []
  let x = 0
  for (const p of parts) {
    if (rows.length === 0 || (x > 0 && x + 2 * p.r > width)) {
      rows.push([])
      x = 0
    }
    p.cx = x + p.r
    x += 2 * p.r + PART_GAP
    rows[rows.length - 1].push(p)
  }
  let top = 0
  for (const row of rows) {
    const h = Math.max(...row.map((p) => 2 * p.r))
    for (const p of row) p.cy = top + h / 2
    top += h + PART_GAP
  }
  for (const p of parts) {
    // 行は上から下へ（sigma の y は上向きなので符号を反転）。
    for (const [id, q] of p.pos) g.mergeNodeAttributes(id, { x: p.cx + q.x, y: -(p.cy + q.y) })
  }
}

/** 探した点に寄るときのカメラ（sigma の正規化した座標・全体が ratio 1）。点と相手が全部入る
 *  広さにする（点だけに寄ると相手が画面の外に出た・実データで確認）。広さは 0.04〜1。 */
export function focusCamera(points: { x: number; y: number }[]): { x: number; y: number; ratio: number } {
  if (points.length === 0) return { x: 0.5, y: 0.5, ratio: 1 }
  const xs = points.map((p) => p.x)
  const ys = points.map((p) => p.y)
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
  const span = Math.max(x1 - x0, y1 - y0) * 1.3
  return { x: (x0 + x1) / 2, y: (y0 + y1) / 2, ratio: Math.min(1, Math.max(0.04, span)) }
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
