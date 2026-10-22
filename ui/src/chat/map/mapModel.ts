// 全体の地図（丸＋横の文字）の純関数。グラフを作る・決定論で配置する・近傍・1 枚の対象。
// 配置は networkModel.ts の hashPosition＋forceAtlas2 の固定回数＋連結成分の格子を流用する（乱数も時刻も使わない）。
// 描画（sigma）は MapView.tsx。契約 pr11h-contract.md §2 A。
import Graph from 'graphology'
import type Sigma from 'sigma'
import { hashPosition, layoutNetwork, neighborhoodOf } from '../../networkModel'
import { draftCardConcepts, draftNodeId, hubNodeId, kindId, termId, termLabel } from '../../sky/skyLayout'
import type { DraftSky, SkyData, SkyDataset, SkyKnowledgeRelation } from '../../sky/skyApi'
import { starRadius } from '../graph/graphLayout'
import type { PointSel } from '../chatState'

export type MapNodeType = 'term' | 'kind' | 'draft' | 'hub'
export type MapEdgeKind = 'concept' | 'member' | 'draft' | 'hub'

export interface MapNodeAttrs {
  type: MapNodeType
  label: string
  /** 用語・種類の IRI。視点では視点の id。 */
  iri?: string
  datasetId?: string
  draftId?: string
  conceptKey?: string
  /** 提案（薄い）: 未公開のデータセットの種類・返事を待つ／開いていない提案の札。 */
  proposed?: boolean
  /** 外す提案の用語。 */
  remove?: boolean
  size: number
  x: number
  y: number
  /** 色の役割: 用語・視点・提案＝activity／データの種類＝entity。 */
  role: 'activity' | 'entity'
}

export interface MapEdgeAttrs {
  kind: MapEdgeKind
  relation?: string
  /** 提案の薄い線。 */
  thin?: boolean
}

/** 種類の丸の大きさ（3〜12）。`starRadius`（5〜13・件数の固定の対数）を相対値に写す。 */
export function kindSize(count: number): number {
  const r = starRadius(count)
  return Math.max(3, Math.min(12, 3 + ((r - 5) / 8) * 9))
}

/** 用語・提案の丸の大きさ（4〜12）。入っている種類の数と線の数の対数。 */
export function termSize(members: number, degree: number): number {
  return Math.min(12, 4 + 2 * Math.log2(1 + Math.max(0, members) + Math.max(0, degree)))
}

export const HUB_SIZE = 8

const REAL_RELATION: Record<string, SkyKnowledgeRelation | undefined> = {
  part: 'isPartOf',
  lowers: 'lowers',
  raises: 'raises',
  affects: 'affects',
}

/** SkyData＋提案 → graphology のグラフ。位置は hashPosition（まだ配置していない）。 */
export function buildMapGraph(
  sky: SkyData,
  drafts: readonly DraftSky[],
  proposedDatasets: readonly SkyDataset[],
  lang: 'ja' | 'en',
): Graph {
  const g = new Graph({ type: 'undirected', multi: true })
  const edgeKeys = new Set<string>()
  const addNode = (id: string, a: Omit<MapNodeAttrs, 'x' | 'y'>) => {
    if (g.hasNode(id)) return
    g.addNode(id, { ...a, ...hashPosition(id) })
  }
  const addEdge = (a: string, b: string, attrs: MapEdgeAttrs, directed: boolean) => {
    if (a === b || !g.hasNode(a) || !g.hasNode(b)) return
    const [p, q] = directed || a < b ? [a, b] : [b, a]
    const key = `${attrs.kind}|${p}|${q}|${attrs.relation ?? ''}|${attrs.thin ? 1 : 0}`
    if (edgeKeys.has(key)) return
    edgeKeys.add(key)
    g.addEdge(a, b, attrs)
  }

  // 外す提案の用語
  const removedTerms = new Set<string>()
  for (const d of drafts) for (const iri of d.removals?.terms ?? []) removedTerms.add(iri)

  // データの種類（公開済み＋未公開）
  const have = new Set(sky.datasets.map((d) => d.id))
  const datasets = [...sky.datasets, ...proposedDatasets.filter((d) => !have.has(d.id))]
  const proposedIds = new Set(proposedDatasets.map((d) => d.id))
  const datasetLabel = new Map<string, string>()
  for (const d of datasets) {
    datasetLabel.set(d.id, d.label)
    for (const k of d.kinds) {
      const proposed = d.proposed === true || (proposedIds.has(d.id) && !have.has(d.id))
      addNode(kindId(d.id, k.iri), {
        type: 'kind',
        label: k.label,
        iri: k.iri,
        datasetId: d.id,
        ...(proposed ? { proposed: true } : {}),
        size: kindSize(k.count),
        role: 'entity',
      })
    }
  }

  // 用語
  const terms = [...sky.terms].sort((a, b) => (termId(a.iri) < termId(b.iri) ? -1 : termId(a.iri) > termId(b.iri) ? 1 : 0))
  const termBySlug = new Map<string, string>()
  for (const t of terms) {
    const id = termId(t.iri)
    addNode(id, {
      type: 'term',
      label: termLabel(t, lang),
      iri: t.iri,
      ...(removedTerms.has(t.iri) ? { remove: true } : {}),
      size: 0,
      role: 'activity',
    })
    termBySlug.set(t.slug, id)
  }

  // 用語と種類
  for (const t of terms) {
    const id = termId(t.iri)
    for (const m of t.members) {
      const kid = kindId(m.dataset_id, m.iri)
      if (!g.hasNode(kid)) continue
      addEdge(id, kid, { kind: 'member', relation: m.relation }, false)
    }
  }

  // 用語どうしの知識の線
  const conceptSeen = new Set<string>()
  for (const l of sky.edges ?? []) {
    const a = termId(l.from)
    const b = termId(l.to)
    if (a === b || !g.hasNode(a) || !g.hasNode(b)) continue
    conceptSeen.add(`${a}>${b}>${l.relation}`)
    addEdge(a, b, { kind: 'concept', relation: l.relation }, true)
  }

  // 視点と入っている種類
  for (const h of [...sky.hubs].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    const hid = hubNodeId(h.id)
    addNode(hid, { type: 'hub', label: h.label, iri: h.id, size: HUB_SIZE, role: 'activity' })
    const mem = new Set(h.members)
    for (const d of datasets) for (const k of d.kinds) if (mem.has(k.iri)) addEdge(hid, kindId(d.id, k.iri), { kind: 'hub' }, false)
  }

  // 提案の札（既にある用語に寄せる札は作らない）と提案の線
  for (const d of drafts) {
    const ends = new Map<string, string>()
    for (const c of draftCardConcepts(d)) {
      const id = draftNodeId(d.draft_id, c.key)
      const proposed = c.state === 'thin' || c.state === undefined || c.proposed === true
      addNode(id, {
        type: 'draft',
        label: c.label,
        draftId: d.draft_id,
        conceptKey: c.key,
        ...(proposed ? { proposed: true } : {}),
        ...(c.state === 'remove' ? { remove: true } : {}),
        size: 0,
        role: 'activity',
      })
      ends.set(c.key, id)
    }
    for (const c of d.concepts) {
      if (!c.use.startsWith('existing:')) continue
      const tid = termBySlug.get(c.use.slice('existing:'.length))
      if (tid) ends.set(c.key, tid)
    }
    for (const r of d.relations) {
      const a = ends.get(r.from)
      const b = ends.get(r.to)
      if (!a || !b || a === b) continue
      const state = r.state ?? 'thin'
      const real = REAL_RELATION[r.rel]
      // 薄い提案の線だけ、既にある本物の線と同じ関係なら張らない
      if (state === 'thin' && real && conceptSeen.has(`${a}>${b}>${real}`)) continue
      addEdge(a, b, { kind: 'draft', relation: r.rel, thin: state === 'thin' }, true)
    }
  }

  g.setAttribute('datasetLabels', Object.fromEntries(datasetLabel))

  // 用語・提案の大きさ（入っている種類の数と線の数）
  g.forEachNode((id, a) => {
    if (a.type !== 'term' && a.type !== 'draft') return
    let members = 0
    let degree = 0
    g.forEachEdge(id, (_e, ea) => {
      if (ea.kind === 'member') members++
      else degree++
    })
    g.setNodeAttribute(id, 'size', termSize(members, degree))
  })
  return g
}

/** 決定論の配置（グラフを直接書き換える）。初期位置を hashPosition に戻してから配置するので、何度呼んでも同じ。 */
export function layoutMap(g: Graph): void {
  g.forEachNode((id) => g.mergeNodeAttributes(id, hashPosition(id)))
  layoutNetwork(g)
}

/** 点とそのつながる相手の id。 */
export function mapNeighborhood(g: Graph, id: string | null): Set<string> {
  return neighborhoodOf(g, id)
}

/** 点を 1 枚の対象にする（`chatState.selectionOfNode` と同じ形）。引けなければ null。 */
export function mapSelection(g: Graph, id: string): PointSel | null {
  if (!g.hasNode(id)) return null
  const a = g.getNodeAttributes(id) as unknown as MapNodeAttrs
  switch (a.type) {
    case 'term':
      return { kind: 'term', iri: a.iri ?? '', label: a.label }
    case 'kind': {
      if (a.proposed) {
        // 未公開のデータセットの種類。データセット名はそのデータセットの別の点から引けないので、地図が持つ表で引く
        const name = (g.getAttribute('datasetLabels') as Record<string, string> | undefined)?.[a.datasetId ?? ''] ?? ''
        return { kind: 'kind', iri: a.iri ?? '', label: a.label, thin: { dataset: name } }
      }
      return { kind: 'kind', iri: a.iri ?? '', label: a.label }
    }
    case 'hub':
      return { kind: 'hub', iri: a.iri ?? '', label: a.label }
    case 'draft':
      return { kind: 'draft', iri: a.conceptKey ?? '', draftId: a.draftId ?? '', label: a.label }
    default:
      return null
  }
}

/** 点の hover・クリックの結びつけ（テストできるよう分けてある）。 */
export function bindMapEvents(
  sigma: Pick<Sigma, 'on'>,
  graph: Graph,
  onSelect: (sel: PointSel | null, nodeId: string | null) => void,
  onHover: (id: string | null) => void,
): void {
  sigma.on('enterNode', ({ node }) => onHover(node))
  sigma.on('leaveNode', () => onHover(null))
  sigma.on('clickNode', ({ node }) => onSelect(mapSelection(graph, node), node))
  sigma.on('clickStage', () => onSelect(null, null))
}
