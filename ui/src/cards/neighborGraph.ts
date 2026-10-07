import type { NeighborGroup, NeighborsResult } from './cardsApi'
import type { GraphEdge, GraphNode, GraphSpec } from './viewSpec'

/** 1 件から広げる図（`NeighborExplorer`）の組み立て。純関数だけ（契約メモ §3.2）。
 *
 *  状態（{@link ExplorerState}）は「中心」「ひらいた節ごとの隣のデータ」「ひらいた
 *  順」「ひらいた束」。図はその状態から毎回**作り直す**（ひらいた順に再生する）
 *  ので、同じ状態は必ず同じ図になる（決定論）。すでに図にある箱は動かさず、
 *  線だけ足す。たたむ＝ひらいた順から外して作り直す（その節が足した箱と線は
 *  消え、中心や他の節からもつながる箱は残る）。 */

/** 図の箱の上限。これを超えるひらき方は止める（黙って切らない — O66 の精神）。 */
export const MAX_BOXES = 80
/** 隣を 1 つずつ箱にする件数の上限。API の `inline_max` の既定と同じ。これを超えた群は束。 */
export const INLINE_MAX = 6

export interface ExplorerState {
  centerIri: string
  /** ひらいた（またはひらいていた）節の IRI → その節の隣（API の返り値）。 */
  data: Record<string, NeighborsResult>
  /** ひらいている節の IRI。ひらいた順（先頭は中心）。 */
  openOrder: string[]
  /** 「中を見る」で中身を出した束の id。 */
  openedBundles: string[]
}

export type NodeMeta = {
  id: string
  kind: 'center' | 'entity' | 'bundle'
  /** 1 件の箱のとき。束では undefined。 */
  iri?: string
  label: string
  classLabel: string | null
  isHub: boolean
  /** 1 件の箱: その節をひらいているか。 */
  isOpen: boolean
  bundle?: {
    ownerIri: string
    group: NeighborGroup
    /** まだ箱になっていない件数（「中を見る」のあとは count − 出した件数）。 */
    remaining: number
  }
  /** 束: 中を見たあとの「残り」の札か。 */
  opened?: boolean
}

export interface NeighborGraphView {
  graph: GraphSpec
  /** 箱の列（中心 0・出る線は +1・入る線は −1）。 */
  columns: Map<string, number>
  meta: Map<string, NodeMeta>
  /** ひらいた節のどれかで、隣が上限で切れていた。 */
  truncated: boolean
}

export interface BundleFormat {
  /** 束の名前（種類名 or 線の名前）と件数 → 札の文言。 */
  bundleName?: (name: string, count: number) => string
  /** 「中を見る」のあとの残りの札。省略時は bundleName と同じ。 */
  restName?: (name: string, count: number) => string
}

const defaultBundleName = (name: string, count: number) => `${name} ${count} 件`

export function initialState(result: NeighborsResult): ExplorerState {
  return {
    centerIri: result.iri,
    data: { [result.iri]: result },
    openOrder: [result.iri],
    openedBundles: [],
  }
}

/** 図の箱の数が {@link MAX_BOXES} を超えているか（最初の表示にも使う）。 */
export function isOverLimit(state: ExplorerState): boolean {
  return buildNeighborGraph(state).graph.nodes.length > MAX_BOXES
}

export function bundleId(ownerIri: string, groupKey: string): string {
  return `bundle|${ownerIri}|${groupKey}`
}

function isBundleGroup(g: NeighborGroup): boolean {
  return g.count > INLINE_MAX
}

export function buildNeighborGraph(state: ExplorerState, fmt: BundleFormat = {}): NeighborGraphView {
  const bundleName = fmt.bundleName ?? defaultBundleName
  const restName = fmt.restName ?? bundleName
  const nodes: GraphNode[] = []
  const edges: GraphEdge[] = []
  const edgeSeen = new Set<string>()
  const columns = new Map<string, number>()
  const meta = new Map<string, NodeMeta>()
  let truncated = false

  const addEdge = (from: string, to: string, label: string) => {
    const k = `${from}\u0000${to}\u0000${label}`
    if (edgeSeen.has(k)) return
    edgeSeen.add(k)
    edges.push({ from, to, label })
  }

  const centerData = state.data[state.centerIri]
  const c = centerData?.center
  nodes.push({
    id: state.centerIri,
    label: c?.label ?? '',
    kind: c?.is_hub ? 'hub' : 'entity',
    center: true,
    ...(c?.class_label ? { props: { type_label: c.class_label } } : {}),
  })
  columns.set(state.centerIri, 0)
  meta.set(state.centerIri, {
    id: state.centerIri,
    kind: 'center',
    iri: state.centerIri,
    label: c?.label ?? '',
    classLabel: c?.class_label ?? null,
    isHub: !!c?.is_hub,
    isOpen: true,
  })

  const opened = new Set(state.openedBundles)

  // 親が columns に入ってから再生する。ひらいた順が「別の節で先に図に入った箱」を
  // 後から親にするときがある（他の節からもつながる箱を残してたたんだあと）ので、
  // 進みがなくなるまで繰り返す（固定点。決定論）。
  const replayed = new Set<string>()
  const replay = (owner: string): boolean => {
    const d = state.data[owner]
    const ownerCol = columns.get(owner)
    if (!d || ownerCol === undefined) return false // 親が消えていれば再生しない
    replayed.add(owner)
    if (d.truncated) truncated = true
    for (const g of d.groups) {
      const col = ownerCol + (g.direction === 'out' ? 1 : -1)
      const link = (neighbor: string) =>
        g.direction === 'out'
          ? addEdge(owner, neighbor, g.predicate_label)
          : addEdge(neighbor, owner, g.predicate_label)
      const bundle = isBundleGroup(g)
      const id = bundleId(owner, g.key)
      const showItems = !bundle || (opened.has(id) && !g.set_spec)

      if (showItems) {
        for (const it of g.items) {
          if (!columns.has(it.iri)) {
            columns.set(it.iri, col)
            nodes.push({
              id: it.iri,
              label: it.label,
              kind: it.is_hub ? 'hub' : 'entity',
              ...(g.class_label ? { props: { type_label: g.class_label } } : {}),
            })
            meta.set(it.iri, {
              id: it.iri,
              kind: 'entity',
              iri: it.iri,
              label: it.label,
              classLabel: g.class_label,
              isHub: it.is_hub,
              isOpen: false,
            })
          }
          link(it.iri)
        }
      }

      const remaining = showItems ? g.count - g.items.length : g.count
      if (bundle && remaining > 0) {
        const name = g.class_label ?? g.predicate_label
        const isRest = showItems
        columns.set(id, col)
        nodes.push({
          id,
          label: (isRest ? restName : bundleName)(name, remaining),
          kind: 'bundle',
        })
        meta.set(id, {
          id,
          kind: 'bundle',
          label: (isRest ? restName : bundleName)(name, remaining),
          classLabel: g.class_label,
          isHub: false,
          isOpen: false,
          bundle: { ownerIri: owner, group: g, remaining },
          opened: isRest,
        })
        link(id)
      }
    }
    return true
  }
  for (let progress = true; progress; ) {
    progress = false
    for (const owner of state.openOrder) {
      if (!replayed.has(owner) && replay(owner)) progress = true
    }
  }

  // 「ひらいている」印（箱ごと）。
  for (const iri of state.openOrder) {
    const m = meta.get(iri)
    if (m && m.kind === 'entity') m.isOpen = !!state.data[iri]
  }

  return { graph: { direction: 'LR', nodes, edges }, columns, meta, truncated }
}

/** 線の名前は、選んだ箱につながる線にだけ付ける。列の間で線が交わると、真ん中に
 *  置かれる名前どうしが重なって読めなくなる（実機: 「食材の名前」と「レシピ」が重なって
 *  「食レシピ前」に見えた）。選んだ箱の線だけなら、名前は 1 つの箱から扇状に出るので
 *  重なりにくい。線そのもの（向き・つながり）は全部残す。 */
export function labelSelectedEdges(graph: GraphSpec, selectedId: string | undefined): GraphSpec {
  return {
    ...graph,
    edges: graph.edges.map((e) =>
      e.from === selectedId || e.to === selectedId ? e : { from: e.from, to: e.to },
    ),
  }
}

/** 状態から、図に出ている節だけを残す（たたんだあとの後始末）。 */
function prune(state: ExplorerState, fmt?: BundleFormat): ExplorerState {
  const view = buildNeighborGraph(state, fmt)
  const openOrder = state.openOrder.filter((iri) => view.columns.has(iri))
  const valid = new Set<string>()
  for (const owner of openOrder) {
    for (const g of state.data[owner]?.groups ?? []) valid.add(bundleId(owner, g.key))
  }
  return {
    ...state,
    openOrder,
    openedBundles: state.openedBundles.filter((id) => valid.has(id)),
  }
}

export interface ChangeResult {
  state: ExplorerState
  /** 箱が {@link MAX_BOXES} を超えるので止めた（state は変えていない）。 */
  blocked: boolean
}

function guard(prev: ExplorerState, next: ExplorerState): ChangeResult {
  if (isOverLimit(next)) return { state: prev, blocked: true }
  return { state: next, blocked: false }
}

/** 節をひらく。すでにひらいていれば何もしない。 */
export function openNode(state: ExplorerState, iri: string, result: NeighborsResult): ChangeResult {
  if (state.openOrder.includes(iri)) return { state, blocked: false }
  return guard(state, {
    ...state,
    data: { ...state.data, [iri]: result },
    openOrder: [...state.openOrder, iri],
  })
}

/** 節をたたむ。中心はたためない（同じ state を返す）。 */
export function collapseNode(state: ExplorerState, iri: string): ExplorerState {
  if (iri === state.centerIri || !state.openOrder.includes(iri)) return state
  return prune({ ...state, openOrder: state.openOrder.filter((x) => x !== iri) })
}

/** set_spec の無い束の中（sample）を箱にする。 */
export function openBundle(state: ExplorerState, id: string): ChangeResult {
  if (state.openedBundles.includes(id)) return { state, blocked: false }
  return guard(state, { ...state, openedBundles: [...state.openedBundles, id] })
}

const COL_NODE_W = 168
const COL_NODE_H = 46
const COL_GAP_X = 96
const COL_GAP_Y = 24

/** 列（{@link NeighborGraphView.columns}）で決まる配置。x = 列・y = 列の中の足した順
 *  （各列は縦に中央揃え）。`GraphView` の `layout` に渡す。 */
export function columnLayout(columns: Map<string, number>) {
  return (graph: GraphSpec) => {
    const byCol = new Map<number, string[]>()
    for (const n of graph.nodes) {
      const col = columns.get(n.id) ?? 0
      const row = byCol.get(col) ?? []
      row.push(n.id)
      byCol.set(col, row)
    }
    const cols = [...byCol.keys()].sort((a, b) => a - b)
    const widest = Math.max(1, ...cols.map((k) => byCol.get(k)!.length))
    const span = (n: number) => n * COL_NODE_H + (n - 1) * COL_GAP_Y
    const total = span(widest)
    const positions = new Map<string, { x: number; y: number }>()
    for (const k of cols) {
      const row = byCol.get(k)!
      const top = (total - span(row.length)) / 2
      const x = (k - cols[0]) * (COL_NODE_W + COL_GAP_X)
      row.forEach((id, i) => positions.set(id, { x, y: top + i * (COL_NODE_H + COL_GAP_Y) }))
    }
    return { positions, height: total }
  }
}
