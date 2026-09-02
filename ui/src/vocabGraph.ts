// 「共通の言葉」の育つ地図の**データ**（shared-vocab-graph.md）。
//
// 複数データセットの保存済み取り込みルールを 1 枚の図に重ね、標準語彙との接点
// （使用・候補）とデータセット間の対応を足す。描画（VocabGraph.tsx）とは切り離す —
// 1 データセット分の絵は ⑤/詳細の `rulesShape` と同じ判定で組む（同じ設計は
// どの画面でも同じ形に見える）。すべて決定論・入力順を保つ。
import type { Alignment } from './crosswalkApi'
import type { DatasetRules, RuleMap, RuleProperty } from './galleryApi'
import type { GroundCandidate } from './groundingApi'
import type { ShapeEdge, ShapeField, ShapeNode } from './shapeGraph'
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
 *  青点線＝データセット間の対応（crosswalk の既存色に合わせる）。 */
export type VocabEdgeKind = 'link' | 'used' | 'candidate' | 'alignment'

export interface VocabEdge extends ShapeEdge {
  kind: VocabEdgeKind
  /** 対応は両向き（どちらが先という話ではない）。 */
  both?: boolean
}

export interface VocabNode extends ShapeNode {
  /** 所属データセット。標準語彙の節は持たない（下の帯に置かれる）。 */
  cluster?: string
  /** 標準語彙の節だけが持つ: どの語彙か（QUDT / schema.org …）。 */
  vocab?: string
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
}

export interface VocabShape {
  nodes: VocabNode[]
  edges: VocabEdge[]
  clusters: VocabCluster[]
  stats: VocabStats
}

/** ある項目の行き先が別の種類そのものか（`rulesShape` と同じ 3 判定）。 */
function linkTarget(rules: DatasetRules, p: RuleProperty): RuleMap | undefined {
  return rules.maps.find(
    (x) =>
      (p.parent_map != null && p.parent_map === x.id) ||
      (!!p.template && p.template === x.subject.template) ||
      (!!p.constant && p.constant_is_iri === true && p.constant === x.subject.constant),
  )
}

/** 図で人が読む項目名（`rulesShape` と同じ優先順位）。 */
function termName(rules: DatasetRules, p: RuleProperty): string {
  return p.label || rules.labels?.[p.predicate_iri] || localName(p.predicate_iri)
}

/** 図で人が読む種類名（`rulesShape` の既定ラベルと同じ優先順位）。 */
function kindLabelOf(rules: DatasetRules, m: RuleMap): string {
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

export function composeVocabGraph(inputs: {
  datasets: { id: string; name: string; rules: DatasetRules }[]
  /** クラス IRI → 実体の件数（公開グラフの実測）。未公開の設計は無くてよい。 */
  classCounts?: Record<string, number>
  /** 語の名前 → 接地候補（POST /api/ground/terms の返答そのまま）。 */
  candidates?: Record<string, GroundCandidate[]>
  alignments?: Alignment[]
  /** 箱の中に並べる項目数の上限。超過分は 1 行の「…ほか N 項目」に畳む。 */
  maxFields?: number
  words: {
    more: (n: number) => string
    count: (n: number) => string
    aligned: string
  }
}): VocabShape {
  const { datasets, classCounts = {}, candidates = {}, alignments = [], words } = inputs
  const maxFields = inputs.maxFields ?? 6
  const nodes: VocabNode[] = []
  const edges: VocabEdge[] = []
  const clusters: VocabCluster[] = []
  /** 標準語彙の節は語 IRI で 1 つ（複数データセットの線が同じ節に集まるのが主役）。 */
  const standard = new Map<string, VocabNode>()
  const ensureStandard = (iri: string, vocabTitle: string): VocabNode => {
    let n = standard.get(iri)
    if (!n) {
      n = { id: iri, label: localName(iri), tone: 'record', vocab: vocabTitle }
      standard.set(iri, n)
    }
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
      const count = classIri ? classCounts[classIri] : undefined
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
      const markUsed = (iri: string, label: string) => {
        if (!iri || isPlumbing(iri) || usedHere.has(iri)) return
        const vocab = knownVocabForIri(iri)
        if (!vocab) return
        usedHere.add(iri)
        ensureStandard(iri, vocab.prefix.replace(/:$/, ''))
        edges.push({ from: nodeId, to: iri, label, kind: 'used' })
        usedCount += 1
      }
      for (const p of m.properties) markUsed(p.predicate_iri, termName(ds.rules, p))
      for (const iri of m.subject.class_iris ?? []) markUsed(iri, kindLabel)

      // 接地の候補（琥珀の点線・exact 級のみ）: 自前で鋳た語だけが対象。
      const candHere = new Set<string>()
      const candidateOf = (name: string, mintedIri: string) => {
        if (!mintedIri || knownVocabForIri(mintedIri)) return
        const best = (candidates[name] ?? [])[0]
        if (!best || candHere.has(best.iri)) return
        candHere.add(best.iri)
        ensureStandard(best.iri, best.vocab_title || best.prefix)
        edges.push({ from: nodeId, to: best.iri, label: name, kind: 'candidate' })
        candCount += 1
      }
      for (const p of m.properties) {
        if (!linkTarget(ds.rules, p)) candidateOf(termName(ds.rules, p), p.predicate_iri)
      }
      if (classIri) candidateOf(kindLabel, classIri)
    }
  }

  // データセット間の対応（青の点線・両向き）: 両端が解決できる事実だけ描く。
  let alignCount = 0
  const alignDrawn = new Set<string>()
  for (const a of alignments) {
    const resolve = (iri: string): string | undefined => {
      const anchor = anchorByIri.get(iri)
      if (anchor) return anchor
      if (standard.has(iri)) return iri
      const vocab = knownVocabForIri(iri)
      if (vocab) return ensureStandard(iri, vocab.prefix.replace(/:$/, '')).id
      return undefined
    }
    const from = resolve(a.source)
    const to = resolve(a.target)
    if (!from || !to || from === to) continue
    const key = [from, to].sort().join(' ')
    if (alignDrawn.has(key)) continue
    alignDrawn.add(key)
    edges.push({ from, to, label: words.aligned, kind: 'alignment', both: true })
    alignCount += 1
  }

  nodes.push(...standard.values())
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
    },
  }
}
