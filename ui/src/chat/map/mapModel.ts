// 全体の地図（丸＋横の文字）の純関数。グラフを作る・決定論で配置する・近傍・1 枚の対象。
// 配置は networkModel.ts の hashPosition＋forceAtlas2 の固定回数＋連結成分の格子を流用する（乱数も時刻も使わない）。
// 描画（sigma）は MapView.tsx。契約 pr11h-contract.md §2 A。
import Graph from 'graphology'
import type Sigma from 'sigma'
import { hashPosition, layoutNetwork, neighborhoodOf } from '../../networkModel'
import { draftCardConcepts, draftNodeId, hubNodeId, kindId, termId, termLabel } from '../../sky/skyLayout'
import type { DraftSky, SkyData, SkyDataset, SkyKnowledgeRelation } from '../../sky/skyApi'
import type { PointSel } from '../chatState'

export type MapNodeType = 'term' | 'kind' | 'draft' | 'hub' | 'dataset'
export type MapEdgeKind = 'concept' | 'member' | 'draft' | 'hub' | 'dataset'

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
  /** 色の役割: 用語・視点・提案＝activity／データの種類＝entity／データセット（表の名前）＝dataset（灰）。 */
  role: 'activity' | 'entity' | 'dataset'
}

export interface MapEdgeAttrs {
  kind: MapEdgeKind
  relation?: string
  /** 関係の表示名（concept の線だけ。hover で出す。形ではないので signature・線の鍵には入れない）。 */
  label?: string
  /** 提案の薄い線。 */
  thin?: boolean
}

/** 件数の固定の対数の半径（1 件 5・10 件 7・100 件 9・1,000 件 12・1 万件以上 13）。11f の札の図から引き継いだ式。 */
export const starRadius = (count: number): number => (count > 0 ? Math.min(13, 5 + 2.2 * Math.log10(count)) : 4)

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
/** データセット（表の名前）の丸。種類をまとめる要で、押しても 1 枚は出ない。 */
export const DATASET_SIZE = 6
export const datasetNodeId = (datasetId: string): string => `d|${datasetId}`

/** 関係の短い表示名。sky.json の line.partOf／line.sub と同じ語（一部・一種）。下げる・上げる・変えるは sky.json に短い語が無いのでここで持つ。
 *  i18n のシングルトンは module 直下で読まない罠があるので、lang 引数で引く辞書にしてある。 */
const RELATION_LABEL: Record<'ja' | 'en', Record<string, string>> = {
  ja: { isA: '一種', isPartOf: '一部', lowers: '下げる', raises: '上げる', affects: '変える' },
  en: { isA: 'kind of', isPartOf: 'part of', lowers: 'lowers', raises: 'raises', affects: 'changes' },
}

/** 関係の表示名。引けなければ undefined。 */
export function relationLabel(relation: string, lang: 'ja' | 'en'): string | undefined {
  return RELATION_LABEL[lang][relation]
}

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
    const proposed = d.proposed === true || (proposedIds.has(d.id) && !have.has(d.id))
    // データセットの丸（表の名前）。その種類を線で束ねて 1 つのまとまりにする（孤立した種類が散らばらない）
    const did = datasetNodeId(d.id)
    if (d.kinds.length > 0) {
      addNode(did, { type: 'dataset', label: d.label, datasetId: d.id, ...(proposed ? { proposed: true } : {}), size: DATASET_SIZE, role: 'dataset' })
    }
    for (const k of d.kinds) {
      const kid = kindId(d.id, k.iri)
      addNode(kid, {
        type: 'kind',
        label: k.label,
        iri: k.iri,
        datasetId: d.id,
        ...(proposed ? { proposed: true } : {}),
        size: kindSize(k.count),
        role: 'entity',
      })
      addEdge(did, kid, { kind: 'dataset', ...(proposed ? { thin: true } : {}) }, false)
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
    const label = relationLabel(l.relation, lang)
    addEdge(a, b, { kind: 'concept', relation: l.relation, ...(label ? { label } : {}) }, true)
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
  return canonical(g)
}

const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)

/** 線の同一性の鍵（端は id の順・関係・薄さ）。 */
function edgeKey(a: string, b: string, at: MapEdgeAttrs): string {
  const [p, q] = a < b ? [a, b] : [b, a]
  return `${at.kind}|${p}|${q}|${at.relation ?? ''}|${at.thin ? 1 : 0}`
}

/** 点は id の順・線は鍵の順に入れ直したグラフ。隣の順が入力順に依らなくなるので、forceAtlas2 の結果も入力順に依らない。
 *  グラフ属性 `signature`（点の id と線の鍵）が同じなら、形は同じ＝配置を使い回せる。 */
function canonical(g: Graph): Graph {
  const out = new Graph({ type: 'undirected', multi: true })
  const nodes = g.nodes().sort(cmp)
  for (const id of nodes) out.addNode(id, { ...g.getNodeAttributes(id) })
  const edges = g
    .edges()
    .map((e) => {
      const [a, b] = g.extremities(e)
      const at = g.getEdgeAttributes(e) as unknown as MapEdgeAttrs
      return { a, b, at, key: edgeKey(a, b, at) }
    })
    .sort((p, q) => cmp(p.key, q.key))
  for (const e of edges) out.addEdge(e.a, e.b, { ...e.at })
  out.setAttribute('datasetLabels', g.getAttribute('datasetLabels'))
  out.setAttribute('signature', `${nodes.join('\n')}\n#\n${edges.map((e) => e.key).join('\n')}`)
  return out
}

/** 形の署名（点の id と線の鍵）。同じなら配置を使い回してよい。 */
export function mapSignature(g: Graph): string {
  return String(g.getAttribute('signature') ?? '')
}

/** 配置済みのグラフに、新しいグラフの点の属性（位置以外）を写す（形が同じとき。色・大きさ・薄さの変化を反映する）。 */
export function syncMapAttributes(laidOut: Graph, fresh: Graph): void {
  fresh.forEachNode((id, a) => {
    if (!laidOut.hasNode(id)) return
    const { x: _x, y: _y, ...rest } = a as MapNodeAttrs
    void _x
    void _y
    // 置き換え（merge だと無くなった proposed・remove が残る）。位置だけ配置済みのものを保つ
    laidOut.replaceNodeAttributes(id, { ...rest, x: laidOut.getNodeAttribute(id, 'x'), y: laidOut.getNodeAttribute(id, 'y') })
  })
  laidOut.setAttribute('datasetLabels', fresh.getAttribute('datasetLabels'))
}

/** sigma の resize は、容器が隠れている（幅か高さが 0）と例外を投げる。隠れているときは何もしない。 */
export function safeResize(sigma: { resize: () => void; refresh: () => void }, width: number, height: number): boolean {
  if (width <= 0 || height <= 0) return false
  try {
    sigma.resize()
    sigma.refresh()
    return true
  } catch {
    return false
  }
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
      // データセットの丸は 1 枚を持たない
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
