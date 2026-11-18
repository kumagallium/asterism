// 全体の地図（丸＋横の文字）の純関数。グラフを作る・決定論で配置する・近傍・1 枚の対象。
// 配置は networkModel.ts の hashPosition＋forceAtlas2 の固定回数＋連結成分の格子を流用する（乱数も時刻も使わない）。
// 描画（sigma）は MapView.tsx。契約 pr11h-contract.md §2 A。
import Graph from 'graphology'
import type Sigma from 'sigma'
import { hashPosition, layoutNetwork, neighborhoodOf } from '../../networkModel'
import { draftCardConcepts, draftNodeId, hubNodeId, kindId, realRelationOf, termId, termLabel } from '../../sky/skyLayout'
import type { DraftSky, SkyData, SkyDataset } from '../../sky/skyApi'
import { isDirectedRelation } from '../../lineChoice'
import type { PointSel } from '../chatState'

export type MapNodeType = 'term' | 'kind' | 'draft' | 'hub' | 'dataset'
export type MapEdgeKind = 'concept' | 'member' | 'draft' | 'hub' | 'dataset' | 'evidence'

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
  /** 論文のデータセットの丸（押すと、その論文が支える関係と根拠の文）。 */
  document?: boolean
  size: number
  x: number
  y: number
  /** 色の役割: 用語・視点・提案＝activity／データの種類＝entity／データセット（表の名前）＝dataset（灰）。 */
  role: 'activity' | 'entity' | 'dataset'
  /** 役割の 4 色（材料 m・物性 p・手法 t・概念 c）。用語と台帳の語だけが持つ。 */
  tone?: Tone
  /** 取り込み元の台帳の語（14e-1）。「提案」とは言わない。 */
  ledger?: boolean
  /** 台帳の語のうち、確認済みの関係が 1 本も無いもの（薄く描く）。 */
  unconfirmed?: boolean
}

/** 役割の 4 色の札: 材料・物性・手法・概念。 */
export type Tone = 'm' | 'p' | 't' | 'c'

/** 台帳の下書きの `draft_id` の頭（`ledger:<取り込み元>`）。点の id は `draft|ledger:<取り込み元>|<語の id>`。 */
export const LEDGER_PREFIX = 'ledger:'

/** 語の役割と種別 → 4 色。物性（項目）＝p、材料＝m、手法＝t、それ以外（概念・役割なし）＝c。 */
export function toneOf(role: string | null | undefined, kind?: string | null): Tone {
  if (kind === 'property') return 'p'
  if (role === 'material') return 'm'
  if (role === 'method') return 't'
  return 'c'
}

/** 台帳の下書きだけが足す追加の欄（`DraftSky` の形はそのまま）。 */
export interface LedgerConceptExtra {
  role?: string | null
  confirmed?: boolean
}
export interface LedgerRelationExtra {
  confirmed?: boolean
  /** 関係の無い候補（名前の無い薄い線）。 */
  candidate?: boolean
  /** 根拠の数。 */
  evidence?: number
  /** 向きのある関係の線（from → to の矢じりを付ける）。向きの無い関係・候補・関係でない線には付けない。 */
  directed?: boolean
}

export interface MapEdgeAttrs {
  kind: MapEdgeKind
  relation?: string
  /** 関係の表示名（concept の線だけ。hover で出す。形ではないので signature・線の鍵には入れない）。 */
  label?: string
  /** 描画する線幅を細くする（確定した根拠線にも使う）。 */
  thin?: boolean
  /** 未承認の提案。線幅とは独立した意味状態。 */
  proposed?: boolean
  /** 台帳の未確認の関係（薄い・「提案」の印は付けない）。 */
  unconfirmed?: boolean
  /** 台帳の候補（関係が無い組）。 */
  candidate?: boolean
  ledger?: boolean
  /** 台帳の線の根拠の数（線の名前を置く順に使う）。 */
  evidence?: number
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
export const DATASET_SIZE = 8
export const datasetNodeId = (datasetId: string): string => `d|${datasetId}`

/** 関係の短い表示名。この辞書が正本（sky.json のキーは参照しない）。
 *  i18n のシングルトンは module 直下で読まない罠があるので、lang 引数で引く辞書にしてある。 */
const RELATION_LABEL: Record<'ja' | 'en', Record<string, string>> = {
  ja: {
    isA: '一種', isPartOf: '一部', lowers: '下げる', raises: '上げる', affects: '変える',
    makes: '作る', measuresProperty: '測る', measuresMaterial: '測る', measures: '測る', becomes: '変わる', competesWith: '競合する', hasProperty: '物性',
  },
  en: {
    isA: 'kind of', isPartOf: 'part of', lowers: 'lowers', raises: 'raises', affects: 'changes',
    makes: 'makes', measuresProperty: 'measures', measuresMaterial: 'measures', measures: 'measures', becomes: 'turns into', competesWith: 'competes with', hasProperty: 'has property',
  },
}

/** 下書き側の関係名（sub・eq・…）の短い名前。台帳の線・一覧・絞り込みで使う。 */
const DRAFT_SHORT: Record<'ja' | 'en', Record<string, string>> = {
  ja: { sub: '一種', eq: '同じ', quantity: '量', part: '一部', lowers: '下げる', raises: '上げる', affects: '変える', makes: '作る', measures: '測る', becomes: '変わる', competes: '競合する', has_property: '物性' },
  en: { sub: 'kind of', eq: 'same as', quantity: 'quantity', part: 'part of', lowers: 'lowers', raises: 'raises', affects: 'changes', makes: 'makes', measures: 'measures', becomes: 'turns into', competes: 'competes with', has_property: 'has property' },
}

/** 下書き側の関係名の短い表示名。知らない名前はそのまま。 */
export function draftRelationLabel(rel: string, lang: 'ja' | 'en'): string {
  return DRAFT_SHORT[lang][rel] ?? rel
}

/** 関係の表示名。引けなければ undefined。 */
export function relationLabel(relation: string, lang: 'ja' | 'en'): string | undefined {
  return RELATION_LABEL[lang][relation]
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
    // 矢じり: 向きのある関係の線（用語どうしの確定した線・台帳・提案）だけ。a → b が関係の向き（主語 → 目的語）
    const arrow = directed && (attrs.kind === 'concept' || attrs.kind === 'draft') && !attrs.candidate && attrs.relation !== 'eq' && attrs.relation !== 'competes'
    g.addEdge(a, b, arrow ? { ...attrs, directed: true } : attrs)
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
    if (d.kinds.length > 0 || d.document) {
      addNode(did, { type: 'dataset', label: d.label, datasetId: d.id, ...(d.document ? { document: true } : {}), ...(proposed ? { proposed: true } : {}), size: DATASET_SIZE, role: 'dataset' })
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
      addEdge(did, kid, { kind: 'dataset', ...(proposed ? { thin: true, proposed: true } : {}) }, false)
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
      tone: toneOf(null, t.kind),
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
      addEdge(id, kid, { kind: 'member', relation: m.relation, ...(g.getNodeAttribute(kid, 'proposed') ? { proposed: true } : {}) }, false)
    }
  }

  // 論文の点 → その論文が根拠になっている用語（12c・細い線。用語の点が地図に無ければ引かない）
  for (const d of datasets) {
    if (!d.document || !d.evidence_terms) continue
    const did = datasetNodeId(d.id)
    for (const iri of d.evidence_terms) addEdge(did, termId(iri), { kind: 'evidence', thin: true, ...(g.getNodeAttribute(did, 'proposed') ? { proposed: true } : {}) }, false)
  }

  // 用語どうしの知識の線
  const conceptSeen = new Set<string>()
  for (const l of sky.edges ?? []) {
    const a = termId(l.from)
    const b = termId(l.to)
    if (a === b || !g.hasNode(a) || !g.hasNode(b)) continue
    conceptSeen.add(`${a}>${b}>${l.relation}`)
    const label = relationLabel(l.relation, lang)
    // 向きの無い関係（competesWith）は端の順を揃える（矢じりも出さない）
    addEdge(a, b, { kind: 'concept', relation: l.relation, ...(label ? { label } : {}) }, isDirectedRelation(l.relation))
  }

  // 視点と入っている種類
  for (const h of [...sky.hubs].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    const hid = hubNodeId(h.id)
    addNode(hid, { type: 'hub', label: h.label, iri: h.id, size: HUB_SIZE, role: 'activity' })
    const mem = new Set(h.members)
    for (const d of datasets) for (const k of d.kinds) if (mem.has(k.iri)) addEdge(hid, kindId(d.id, k.iri), { kind: 'hub' }, false)
  }

  // 台帳（取り込み元の関係）: 語は役割の色・確認済みは濃く、線は名前つき（確認済み＝実線の濃さ・未確認＝薄く）
  for (const d of drafts) {
    if (!d.draft_id.startsWith(LEDGER_PREFIX)) continue
    const ends = new Map<string, string>()
    for (const c of d.concepts) {
      const x = c as DraftSky['concepts'][number] & LedgerConceptExtra
      const id = draftNodeId(d.draft_id, c.key)
      addNode(id, {
        type: 'draft',
        label: c.label,
        draftId: d.draft_id,
        conceptKey: c.key,
        ledger: true,
        ...(x.confirmed ? {} : { unconfirmed: true }),
        tone: toneOf(x.role, c.kind),
        size: 0,
        role: 'activity',
      })
      ends.set(c.key, id)
    }
    for (const r of d.relations) {
      const a = ends.get(r.from)
      const b = ends.get(r.to)
      if (!a || !b || a === b) continue
      const x = r as DraftSky['relations'][number] & LedgerRelationExtra
      if (x.candidate) {
        addEdge(a, b, { kind: 'draft', relation: 'candidate', thin: true, unconfirmed: true, candidate: true, ledger: true }, false)
        continue
      }
      const label = draftRelationLabel(r.rel, lang)
      addEdge(
        a,
        b,
        { kind: 'draft', relation: r.rel, label, ledger: true, evidence: x.evidence ?? 0, ...(x.confirmed ? {} : { thin: true, unconfirmed: true }) },
        r.rel !== 'competes' && r.rel !== 'eq',
      )
    }
  }

  // 提案の札（既にある用語に寄せる札は作らない）と提案の線
  for (const d of drafts) {
    if (d.draft_id.startsWith(LEDGER_PREFIX)) continue
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
      const toKind = d.concepts.find((c) => c.key === r.to)?.kind
      const real = realRelationOf(r.rel, toKind)
      // 薄い提案の線だけ、既にある本物の線と同じ関係なら張らない
      // （向きの無い関係＝競合する は、本物の線が逆向きでも同じ線）
      const seen =
        conceptSeen.has(`${a}>${b}>${real}`) || (real === 'competesWith' && conceptSeen.has(`${b}>${a}>${real}`))
      if (state === 'thin' && real && seen) continue
      addEdge(a, b, { kind: 'draft', relation: r.rel, thin: state === 'thin', proposed: state === 'thin' }, r.rel !== 'competes')
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
      else if (ea.kind !== 'evidence') degree++  // 論文の根拠の線は用語どうしの関係ではない
    })
    g.setNodeAttribute(id, 'size', termSize(members, degree))
  })
  return canonical(g)
}

const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)

/** 線の同一性の鍵（端は id の順・関係・薄さ）。 */
function edgeKey(a: string, b: string, at: MapEdgeAttrs): string {
  const [p, q] = a < b ? [a, b] : [b, a]
  // 台帳の線は確認・未確認で薄さが変わるだけ（形は同じ）。鍵に入れると確認のたびに図が作り直されてカメラが戻る
  return `${at.kind}|${p}|${q}|${at.relation ?? ''}|${at.ledger ? 0 : at.thin ? 1 : 0}`
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
  // 線は鍵（端・関係・薄さ）で対応づけ、位置を持たない属性（label・kind・thin など）を丸ごと写す。label は形ではないので lang だけ変わるとここで反映する
  const freshEdges = new Map<string, MapEdgeAttrs>()
  fresh.forEachEdge((_e, at, s, t) => freshEdges.set(edgeKey(s, t, at as unknown as MapEdgeAttrs), at as unknown as MapEdgeAttrs))
  laidOut.forEachEdge((e, at, s, t) => {
    const f = freshEdges.get(edgeKey(s, t, at as unknown as MapEdgeAttrs))
    if (f) laidOut.replaceEdgeAttributes(e, { ...f })
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

/** 台帳の語（主役）を広く取る倍率。ほかの点とは別の群として配置し、右へ寄せる。 */
const LEDGER_SPREAD = 3

/** グラフの一部（`ids`）だけの写しを作る（線は両端が入るものだけ）。 */
function subgraphOf(g: Graph, ids: string[]): Graph {
  const sub = new Graph({ type: 'undirected', multi: true })
  for (const id of ids) sub.addNode(id, { ...g.getNodeAttributes(id) })
  g.forEachEdge((_e, at, s, t) => {
    if (sub.hasNode(s) && sub.hasNode(t)) sub.addEdge(s, t, { ...at })
  })
  return sub
}

/** 全体の外枠が `aspect`（幅÷高さ）になるよう、狭いほうの向きだけ引き伸ばす（図の枠を使い切る）。 */
function stretchToAspect(g: Graph, aspect: number): void {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
  g.forEachNode((_i, a) => {
    x0 = Math.min(x0, a.x); x1 = Math.max(x1, a.x)
    y0 = Math.min(y0, a.y); y1 = Math.max(y1, a.y)
  })
  const w = x1 - x0
  const h = y1 - y0
  if (!(w > 0) || !(h > 0) || !(aspect > 0)) return
  const kx = w / h < aspect ? (aspect * h) / w : 1
  const ky = w / h > aspect ? w / (aspect * h) : 1
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  g.forEachNode((id, a) => g.mergeNodeAttributes(id, { x: cx + (a.x - cx) * kx, y: cy + (a.y - cy) * ky }))
}

/** 決定論の配置（グラフを直接書き換える）。初期位置を hashPosition に戻してから配置するので、何度呼んでも同じ。
 *  `aspect`（枠の幅÷高さ）を渡すと、台帳の語を持つ図は枠いっぱいに伸ばす（既定 1.4）。 */
export function layoutMap(g: Graph, aspect = 1.4): void {
  g.forEachNode((id) => g.mergeNodeAttributes(id, hashPosition(id)))
  const ledger = g.filterNodes((_id, a) => (a as unknown as MapNodeAttrs).ledger === true)
  const rest = g.filterNodes((_id, a) => (a as unknown as MapNodeAttrs).ledger !== true)
  if (ledger.length === 0) {
    layoutNetwork(g)
    // データセット側の点が無い図（語と関係だけ）は枠いっぱいに伸ばす。あるときは従来のまま
    if (!g.someNode((_id, a) => a.type === 'dataset' || a.type === 'kind')) stretchToAspect(g, aspect)
    return
  }
  if (rest.length === 0) {
    layoutNetwork(g)
    stretchToAspect(g, aspect)
    return
  }
  // 台帳の語が主役: 台帳の群を広げ、ほかの点は横長の枠なら右・縦長の枠なら下に小さく寄せる
  const gl = subgraphOf(g, ledger)
  const gr = subgraphOf(g, rest)
  layoutNetwork(gl)
  layoutNetwork(gr)
  const box = (h: Graph, k: number) => {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
    h.forEachNode((_i, a) => {
      x0 = Math.min(x0, a.x * k); x1 = Math.max(x1, a.x * k)
      y0 = Math.min(y0, a.y * k); y1 = Math.max(y1, a.y * k)
    })
    return { x0, x1, y0, y1 }
  }
  const bl = box(gl, LEDGER_SPREAD)
  const br = box(gr, 1)
  let dx: number
  let dy: number
  if (aspect >= 1) {
    dx = bl.x1 + 0.1 * (bl.x1 - bl.x0) - br.x0
    dy = (bl.y0 + bl.y1) / 2 - (br.y0 + br.y1) / 2
  } else {
    dx = (bl.x0 + bl.x1) / 2 - (br.x0 + br.x1) / 2
    dy = bl.y1 + 0.1 * (bl.y1 - bl.y0) - br.y0
  }
  gl.forEachNode((id, a) => g.mergeNodeAttributes(id, { x: a.x * LEDGER_SPREAD, y: a.y * LEDGER_SPREAD }))
  gr.forEachNode((id, a) => g.mergeNodeAttributes(id, { x: a.x + dx, y: a.y + dy }))
  stretchToAspect(g, aspect)
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
    case 'dataset':
      // 公開済みの論文の丸だけ 1 枚を持つ（その論文が支える関係と根拠の文）。表の丸は持たない
      return a.document && !a.proposed && a.datasetId ? { kind: 'paper', iri: a.datasetId, label: a.label } : null
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
