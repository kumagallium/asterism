// 「ことば」の育つ地図の**データ**（shared-vocab-graph.md）。
//
// 複数データセットの保存済み取り込みルールを 1 枚の図に重ね、標準語彙との接点
// （使用・候補）とデータセット間の対応を足す。描画（VocabGraph.tsx）とは切り離す —
// 1 データセット分の絵は ⑤/詳細の `rulesShape` と同じ判定で組む（同じ設計は
// どの画面でも同じ形に見える）。すべて決定論・入力順を保つ。
import type { Alignment } from './crosswalkApi'
import type { DatasetRules, KindCounts, RuleMap, RuleProperty } from './galleryApi'
import type { GroundCandidate } from './groundingApi'
import type { ShapeEdge, ShapeField, ShapeNode } from './shapeGraph'
import { isDirectedRelation, relationKey, sharedLines, standardTermKind, type PickEnd } from './lineChoice'
import { linksTo } from './shapeGraph'
import type { SharedTerm } from './vocabApi'
import { knownVocabForIri, localName } from './vocab'

/** カタログの `id` は表示用（`live-<登録 id>`）で、API が受け取る登録 id とは**違う**。
 *
 *  ⭐この取り違えで「ことばの地図」は出荷後ずっと空だった: `getDatasetRules(d.id)`
 *  が `live-…` を投げて 404 → catch → 対象 0 件 → 節が `null` を返す（＝画面から
 *  丸ごと消える）ので、エラーも空状態も出ないまま「地図が無い」に見えていた
 *  （利用者報告 2026-09-03・v0.39.0）。ハーネスは API を通らないので気付けない。
 *  API を呼ぶときは必ずこれを通す。 */
export function datasetApiId(dataset: {
  id: string
  live?: { meta: { id: string } } | null
}): string {
  return dataset.live?.meta.id ?? dataset.id.replace(/^live-/, '')
}

/** RDF の配管（rdf/rdfs/owl）。項目としては描くが、「標準語の使用」の線にはしない —
 *  すべてのカタログが rdfs:label で RDFS に繋がる絵は、地図ではなくノイズ。 */
export const PLUMBING_NS = [
  'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  'http://www.w3.org/2000/01/rdf-schema#',
  'http://www.w3.org/2002/07/owl#',
]

const isPlumbing = (iri: string): boolean => PLUMBING_NS.some((ns) => iri.startsWith(ns))

/** 辺の性格。灰＝データの中 / 緑＝標準語を使用（確定） / 琥珀点線＝接地の候補 /
 *  青点線＝データセット間の対応（crosswalk の既存色に合わせる）/
 *  青の実線＝上位への線（`upper`: 種類 → 共有のことば・共有のことば → 標準。⊂ は向きあり・≡ は両向き）。 */
export type VocabEdgeKind = 'link' | 'used' | 'candidate' | 'alignment' | 'upper'

export interface VocabEdge extends ShapeEdge {
  kind: VocabEdgeKind
  /** 対応・≡ は両向き（どちらが先という話ではない）。 */
  both?: boolean
  /** `upper` の関係（短い名前）。 */
  relation?: string
}

/** 共有のことばの節が持つ情報（帯の箱・選択・孤立の札に使う）。 */
export interface VocabSharedInfo {
  slug: string
  kind: 'class' | 'property'
  /** まだ線になっていない（答えるデータセットが 0）。 */
  orphan: boolean
  /** 下に掛かる種類の箱の数。 */
  kids: number
  /** 子の件数の合計（派生値）。件数のある子が無ければ undefined。 */
  count?: number
}

export interface VocabNode extends ShapeNode {
  /** 所属データセット。標準語彙の節は持たない（下の帯に置かれる）。 */
  cluster?: string
  /** 標準語彙の節だけが持つ: どの語彙か（QUDT / schema.org …）。共有のことばの節は小見出し。 */
  vocab?: string
  /** 種類の箱だけが持つ: その種類の IRI（線の起点に選ぶとき）。 */
  iri?: string
  /** 標準語彙の節だけが持つ: 種類か項目か（分かるときだけ。線を引く関係の絞り込みに使う）。 */
  termKind?: 'class' | 'property'
  /** 共有のことばの節だけが持つ（帯は標準の帯の上）。 */
  shared?: VocabSharedInfo
}

export interface VocabCluster {
  id: string
  label: string
}

export interface VocabStats {
  datasets: number
  kinds: number
  items: number
  used: number
  candidates: number
  alignments: number
  /** 共有のことば（帯に描いた節）の数。 */
  shared: number
}

export interface VocabShape {
  nodes: VocabNode[]
  edges: VocabEdge[]
  clusters: VocabCluster[]
  stats: VocabStats
}

/** ある項目の行き先が別の種類そのものか（`rulesShape` と同じ判定）。 */
function linkTarget(rules: DatasetRules, p: RuleProperty): RuleMap | undefined {
  return rules.maps.find((x) => linksTo(p, x))
}

/** 図で人が読む項目名（`rulesShape` と同じ優先順位）。 */
function termName(rules: DatasetRules, p: RuleProperty): string {
  return p.label || rules.labels?.[p.predicate_iri] || localName(p.predicate_iri)
}

/** 図で人が読む種類名（`rulesShape` の既定ラベルと同じ優先順位）。
 *  `labels` の種類名は api がワークスペースと同じ読み手で引いてくる。ここに別の
 *  読み順を足さない（画面ごとに名前が食い違う）。 */
export function kindLabelOf(rules: DatasetRules, m: RuleMap): string {
  const classIri = (m.subject.class_iris ?? [])[0] ?? ''
  return (
    (classIri && rules.labels?.[classIri]) ||
    (m.subject.classes ?? [])[0]?.split(':').pop() ||
    m.id
  )
}

/** 接地候補を問い合わせる語の一覧（POST /api/ground/terms の入力）。
 *  合成（composeVocabGraph）と**同じ名前の導出**であること — ここで送った名前が
 *  そのまま返答のキーになり、合成はそのキーで候補を引く。 */
export function collectMintedTermQueries(
  datasets: { rules: DatasetRules }[],
): { name: string; kind: 'class' | 'property' }[] {
  const out: { name: string; kind: 'class' | 'property' }[] = []
  const seen = new Set<string>()
  const push = (name: string, kind: 'class' | 'property') => {
    const key = `${kind} ${name}`
    if (!name || seen.has(key)) return
    seen.add(key)
    out.push({ name, kind })
  }
  for (const ds of datasets) {
    for (const m of ds.rules.maps) {
      const classIri = (m.subject.class_iris ?? [])[0] ?? ''
      if (classIri && !knownVocabForIri(classIri)) push(kindLabelOf(ds.rules, m), 'class')
      for (const p of m.properties) {
        if (linkTarget(ds.rules, p)) continue
        if (p.predicate_iri && !knownVocabForIri(p.predicate_iri)) {
          push(termName(ds.rules, p), 'property')
        }
      }
    }
  }
  return out
}

/** 名前を IRI でカタログに問い合わせる標準の語（POST /api/ground/terms の `iris`）。
 *  合成で標準の箱になりうる語 = 設計が直に使う既知の語彙の語と、対応の両端の既知の
 *  語彙の語。接地の候補から来る箱は、候補そのものに名前がある。 */
export function collectStandardIris(
  datasets: { rules: DatasetRules }[],
  alignments: Alignment[] = [],
): string[] {
  const out = new Set<string>()
  const push = (iri: string | undefined) => {
    if (iri && !isPlumbing(iri) && knownVocabForIri(iri)) out.add(iri)
  }
  for (const ds of datasets) {
    for (const m of ds.rules.maps) {
      for (const iri of m.subject.class_iris ?? []) push(iri)
      for (const p of m.properties) push(p.predicate_iri)
    }
  }
  for (const a of alignments) {
    push(a.source)
    push(a.target)
  }
  return [...out]
}

/** `GET /api/kinds/counts` を、地図の節のデータセット id（カタログの `id`）→ 種類 IRI →
 *  件数 に組み替える。API の `dataset_id` は登録 id なので、カタログ側を
 *  `datasetApiId` で登録 id にして突き合わせる（⭐`live-…` のまま突き合わせると
 *  一致が 0 件になり、件数が全部消える）。ハブ・未公開は対象外。
 *  ⭐API が上限で切れた（`truncated`）ときは null。取れた分だけ渡すと、末尾側の
 *  データセットの箱が件数なしで黙って欠けるので、従来の全体件数に落とす。 */
export function classCountsByCatalogId(
  counts: KindCounts,
  datasets: { id: string; live?: { meta: { id: string } } | null }[],
): Record<string, Record<string, number>> | null {
  if (counts.truncated) return null
  const byRegistered = new Map<string, Record<string, number>>()
  for (const g of counts.graphs) {
    if (g.hub || !g.dataset_id) continue
    const m = byRegistered.get(g.dataset_id) ?? {}
    for (const k of g.kinds) m[k.class_iri] = (m[k.class_iri] ?? 0) + k.count
    byRegistered.set(g.dataset_id, m)
  }
  const out: Record<string, Record<string, number>> = {}
  for (const d of datasets) {
    const m = byRegistered.get(datasetApiId(d))
    if (m) out[d.id] = m
  }
  return out
}

export function composeVocabGraph(inputs: {
  datasets: { id: string; name: string; rules: DatasetRules }[]
  /** クラス IRI → 実体の件数（公開グラフの実測）。未公開の設計は無くてよい。 */
  classCounts?: Record<string, number>
  /** データセット id（`datasets[].id`）→ クラス IRI → 件数。渡されたときはこちらだけを
   *  引く（同じ種類 IRI を別のデータセットが使っていても、その箱には自分の件数が出る。
   *  全体の合計は混ぜない）。取れなかったとき（API 失敗・古いサーバ）は渡さず、
   *  従来の `classCounts` に落とす。 */
  classCountsByDataset?: Record<string, Record<string, number>>
  /** 語の名前 → 接地候補（POST /api/ground/terms の返答そのまま）。 */
  candidates?: Record<string, GroundCandidate[]>
  /** 語 IRI → カタログの名前（POST /api/ground/terms の `names`）。 */
  standardNames?: Record<string, string>
  alignments?: Alignment[]
  /** 鋳造済みの共有のことば（GET /api/vocab/shared の terms）。標準の帯の上に帯を足す。 */
  sharedTerms?: SharedTerm[]
  /** 箱の中に並べる項目数の上限。超過分は 1 行の「…ほか N 項目」に畳む。 */
  maxFields?: number
  words: {
    more: (n: number) => string
    count: (n: number) => string
    aligned: string
    /** 共有のことばの節の小見出し（省略時は空）。 */
    shared?: (kids: number, orphan: boolean) => string
  }
}): VocabShape {
  const {
    datasets,
    classCounts = {},
    classCountsByDataset,
    candidates = {},
    standardNames = {},
    alignments = [],
    words,
  } = inputs
  const maxFields = inputs.maxFields ?? 6
  const nodes: VocabNode[] = []
  const edges: VocabEdge[] = []
  const clusters: VocabCluster[] = []
  /** 標準語彙の節は語 IRI で 1 つ（複数データセットの線が同じ節に集まるのが主役）。 */
  const standard = new Map<string, VocabNode>()
  // 語 IRI → 人向けの名前（候補の全部と、IRI で引いたカタログの名前から作る）。
  // 読み順: 表示名 → 名前 → ローカル名。符号だけの IRI（ローカル名が読めない語彙）
  // でも、カタログの名前で箱を出すため。
  const nameByIri = new Map<string, string>()
  for (const list of Object.values(candidates)) {
    for (const c of list) {
      if (nameByIri.has(c.iri)) continue
      const human = (c.label ?? '').trim() || (c.name ?? '').trim()
      if (human) nameByIri.set(c.iri, human)
    }
  }
  for (const [iri, name] of Object.entries(standardNames)) {
    const human = name.trim()
    if (human && !nameByIri.has(iri)) nameByIri.set(iri, human)
  }
  const ensureStandard = (iri: string, vocabTitle: string, termKind?: 'class' | 'property'): VocabNode => {
    let n = standard.get(iri)
    if (!n) {
      n = {
        id: iri,
        label: nameByIri.get(iri) ?? localName(iri),
        tone: 'record',
        vocab: vocabTitle,
      }
      standard.set(iri, n)
    }
    if (termKind && !n.termKind) n.termKind = termKind
    return n
  }
  /** 語 IRI →（それを名乗る/使う）種類の節 id。対応の線の足場。 */
  const anchorByIri = new Map<string, string>()
  let items = 0
  let usedCount = 0
  let candCount = 0

  for (const ds of datasets) {
    clusters.push({ id: ds.id, label: ds.name })
    for (const m of ds.rules.maps) {
      const nodeId = `${ds.id}::${m.id}`
      const classIri = (m.subject.class_iris ?? [])[0] ?? ''
      const kindLabel = kindLabelOf(ds.rules, m)
      const countSource = classCountsByDataset ? (classCountsByDataset[ds.id] ?? {}) : classCounts
      const count = classIri ? countSource[classIri] : undefined
      const own = m.properties.filter((p) => !linkTarget(ds.rules, p))
      items += m.properties.length
      const fields: ShapeField[] = own.slice(0, maxFields).map((p) => ({
        name: termName(ds.rules, p),
        unit: p.unit,
      }))
      if (own.length > maxFields) fields.push({ name: words.more(own.length - maxFields) })
      nodes.push({
        id: nodeId,
        label: count != null ? `${kindLabel}（${words.count(count)}）` : kindLabel,
        tone: 'record',
        fields,
        cluster: ds.id,
        ...(classIri ? { iri: classIri } : {}),
      })
      if (classIri && !anchorByIri.has(classIri)) anchorByIri.set(classIri, nodeId)
      // 対応（alignment）の足場: この種類が名乗る/使う語はすべてここに繋がる。
      for (const p of m.properties) {
        if (p.predicate_iri && !anchorByIri.has(p.predicate_iri)) {
          anchorByIri.set(p.predicate_iri, nodeId)
        }
      }

      // データの中のつながり（灰の実線）
      const drawn = new Set<string>()
      for (const p of m.properties) {
        const target = linkTarget(ds.rules, p)
        if (!target || target.id === m.id) continue
        const to = `${ds.id}::${target.id}`
        if (drawn.has(to)) continue
        drawn.add(to)
        edges.push({ from: nodeId, to, label: termName(ds.rules, p), kind: 'link' })
      }

      // 標準語の使用（緑の実線・確定）: 既知名前空間の述語/クラス。配管は描かない。
      const usedHere = new Set<string>()
      const markUsed = (iri: string, label: string, termKind: 'class' | 'property') => {
        if (!iri || isPlumbing(iri) || usedHere.has(iri)) return
        const vocab = knownVocabForIri(iri)
        if (!vocab) return
        usedHere.add(iri)
        ensureStandard(iri, vocab.prefix.replace(/:$/, ''), termKind)
        edges.push({ from: nodeId, to: iri, label, kind: 'used' })
        usedCount += 1
      }
      for (const p of m.properties) markUsed(p.predicate_iri, termName(ds.rules, p), 'property')
      for (const iri of m.subject.class_iris ?? []) markUsed(iri, kindLabel, 'class')

      // 接地の候補（琥珀の点線・exact 級のみ）: 自前で鋳た語だけが対象。
      const candHere = new Set<string>()
      const candidateOf = (name: string, mintedIri: string, termKind: 'class' | 'property') => {
        if (!mintedIri || knownVocabForIri(mintedIri)) return
        const best = (candidates[name] ?? [])[0]
        if (!best || candHere.has(best.iri)) return
        candHere.add(best.iri)
        ensureStandard(best.iri, best.vocab_title || best.prefix, termKind)
        edges.push({ from: nodeId, to: best.iri, label: name, kind: 'candidate' })
        candCount += 1
      }
      for (const p of m.properties) {
        if (!linkTarget(ds.rules, p)) candidateOf(termName(ds.rules, p), p.predicate_iri, 'property')
      }
      if (classIri) candidateOf(kindLabel, classIri, 'class')
    }
  }

  // データセット間の対応（青の点線・両向き）: 両端が解決できる事実だけ描く。
  // ⊂（subClassOf / subPropertyOf）は向きのない「対応」ではなく、上位への線（'upper'・向きあり）。
  // 共有のことばを片端に持つ線は下の共有語の帯で描く。
  const sharedTerms = inputs.sharedTerms ?? []
  const sharedByIri = new Map(sharedTerms.map((t) => [t.iri, t]))
  let alignCount = 0
  const alignDrawn = new Set<string>()
  const resolve = (iri: string, termKind?: 'class' | 'property'): string | undefined => {
    const anchor = anchorByIri.get(iri)
    if (anchor) return anchor
    if (standard.has(iri)) return iri
    const vocab = knownVocabForIri(iri)
    if (vocab) return ensureStandard(iri, vocab.prefix.replace(/:$/, ''), termKind).id
    return undefined
  }
  for (const a of alignments) {
    if (sharedByIri.has(a.source) || sharedByIri.has(a.target)) continue
    const from = resolve(a.source)
    const to = resolve(a.target)
    if (!from || !to || from === to) continue
    const directed = isDirectedRelation(a.relation)
    const key = directed ? `up ${from} ${to}` : `eq ${[from, to].sort().join(' ')}`
    if (alignDrawn.has(key)) continue
    alignDrawn.add(key)
    if (directed) {
      const rel = relationKey(a.relation)
      edges.push({ from, to, label: '⊂', kind: 'upper', relation: rel })
    } else {
      edges.push({ from, to, label: words.aligned, kind: 'alignment', both: true })
    }
    alignCount += 1
  }

  // 共有のことば: 標準の帯の上の帯。語はすべて節にし（データセットが 0 件でも描く）、
  // 線は両端が解決できるものだけ（種類の箱・標準の語。共有語どうしは「全体」の図で描く）。
  const sharedNodes: VocabNode[] = []
  const kindIds = new Set(nodes.filter((n) => n.cluster).map((n) => n.id))
  const kidsOf = new Map<string, Set<string>>() // 共有語 → 掛かる種類の箱
  const upperDrawn = new Set<string>()
  const upperEdges: VocabEdge[] = []
  const kindCount = new Map<string, number | undefined>() // 箱 id → 件数
  for (const ds of datasets) {
    const countSource = classCountsByDataset ? (classCountsByDataset[ds.id] ?? {}) : classCounts
    for (const m of ds.rules.maps) {
      const classIri = (m.subject.class_iris ?? [])[0] ?? ''
      kindCount.set(`${ds.id}::${m.id}`, classIri ? countSource[classIri] : undefined)
    }
  }
  for (const ln of sharedLines(sharedTerms, alignments)) {
    const fromShared = sharedByIri.has(ln.from)
    const toShared = sharedByIri.has(ln.to)
    if (fromShared && toShared) continue
    const sharedIri = fromShared ? ln.from : ln.to
    const otherIri = fromShared ? ln.to : ln.from
    const boxId = anchorByIri.get(otherIri)
    const isStd = !boxId && !!knownVocabForIri(otherIri) && !isPlumbing(otherIri)
    const other = boxId ?? (isStd ? otherIri : undefined)
    if (!other) continue
    const [from, to] = fromShared ? [sharedIri, other] : [other, sharedIri]
    const rel = relationKey(ln.relation)
    const key = `${from} ${to} ${rel}`
    if (upperDrawn.has(key)) continue
    upperDrawn.add(key)
    const eq = !isDirectedRelation(ln.relation)
    upperEdges.push({
      from,
      to,
      label: eq ? '≡' : '⊂',
      kind: 'upper',
      relation: rel,
      ...(eq ? { both: true } : {}),
    })
    if (isStd) {
      const vocab = knownVocabForIri(otherIri)!
      ensureStandard(otherIri, vocab.prefix.replace(/:$/, ''))
    }
    // 子: 共有語の下に掛かる種類の箱（⊂ で下から掛かるもの・≡）。
    if (boxId && kindIds.has(boxId) && (!fromShared || eq)) {
      const set = kidsOf.get(sharedIri) ?? new Set<string>()
      set.add(boxId)
      kidsOf.set(sharedIri, set)
    }
  }
  for (const t of sharedTerms) {
    const kids = kidsOf.get(t.iri) ?? new Set<string>()
    let count: number | undefined
    if (t.kind === 'class') {
      for (const id of kids) {
        const c = kindCount.get(id)
        if (c != null) count = (count ?? 0) + c
      }
    }
    sharedNodes.push({
      id: t.iri,
      label: t.label,
      tone: 'record',
      vocab: words.shared ? words.shared(kids.size, !t.wired) : '',
      termKind: t.kind,
      shared: { slug: t.slug, kind: t.kind, orphan: !t.wired, kids: kids.size, ...(count != null ? { count } : {}) },
    })
  }
  edges.push(...upperEdges)

  nodes.push(...sharedNodes, ...standard.values())
  return {
    nodes,
    edges,
    clusters,
    stats: {
      datasets: datasets.length,
      kinds: nodes.filter((n) => n.cluster).length,
      items,
      used: usedCount,
      candidates: candCount,
      alignments: alignCount,
      shared: sharedNodes.length,
    },
  }
}

/** 「詳しく」の節を、線の起点に選べる丸へ（種類の箱・共有のことば・標準の語）。選べない節は null。 */
export function pickEndOfVocabNode(n: VocabNode | undefined): PickEnd | null {
  if (!n) return null
  if (n.shared) return { id: n.id, iri: n.id, label: n.label, role: 'shared', termKind: n.shared.kind }
  if (n.cluster) {
    return n.iri ? { id: n.id, iri: n.iri, label: n.label, role: 'dataset', termKind: 'class' } : null
  }
  return { id: n.id, iri: n.id, label: n.label, role: 'standard', termKind: standardTermKind(n.id, n.termKind) }
}
