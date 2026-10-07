// 「共通のことば」の地図の「全体」表示（案 B）の**データと配置**（shared-vocab-graph.md §6）。
//
// 種類を**丸**で置き（丸の大きさ＝表示している中の件数、面積が対数に比例）、データセットの枠で囲み、つながりの
// **ハブ**を真ん中の列に置く。標準のことばは一番下の帯。項目つきの箱で描く「詳しく」
// （`composeVocabGraph` → `VocabMap`）とは別の絵で、項目は描かない。
//
// 配置は力学ではなく**決め打ち**（箱が毎回動かない・Asterism の他の図と同じ）:
//   ・3 列 = 左のデータセット / 真ん中のハブ / 右のデータセット。データセットは入力順に
//     左右交互、各列の中は縦に積む。
//   ・枠の中は件数の多い順に、1 行あたり最大 4 つで折り返す。
//   ・ハブはつながるデータセットの重心の高さの順（同じなら名前順）。
// 並びは入力順と辞書順だけで決まる（同じ入力は同じ図）。
import type { Alignment, CrosswalkPerspective } from './crosswalkApi'
import { conceptName, perspectiveDisplayName } from './crosswalkLabels'
import type { DatasetRules, KindCounts } from './galleryApi'
import { rulesShape } from './shapeGraph'
import { knownVocabForIri, localName } from './vocab'
import { kindLabelOf, PLUMBING_NS } from './vocabGraph'

export const R_MIN = 16
export const R_MAX = 52
const PER_ROW = 4
const CELL_GAP = 14
/** 丸の下の名前（2 行まで）と件数に取る高さ。名前は丸の中に入れない（小さい丸では読めない）。 */
export const LABEL_H = 46
/** 種類の丸の名前の幅。 */
export const LABEL_W = 104
/** ハブの丸の名前の幅（ハブは図の主役なので広めに取る）。 */
export const HUB_LABEL_W = 132
const ROW_GAP = 8
const FRAME_PAD_X = 18
const FRAME_PAD_TOP = 42
const FRAME_PAD_BOT = 14
const FRAME_MIN_W = 180
const FRAME_GAP = 36
const COL_GAP = 170
const HUB_ROW_GAP = 40
const BAND_GAP = 70
const BAND_PAD_TOP = 52
const BAND_PAD_SIDE = 22
const BAND_PAD_BOT = 20
export const STD_W = 190
export const STD_H = 50
const STD_GAP = 18

/** 半径がこれ以上の丸は、件数を丸の中に短い形で書く（それ未満は名前の下に「N 件」）。 */
export const INSIDE_R = 22
export const countInside = (r: number): boolean => r >= INSIDE_R

/** 件数の短い形（ja「120万」「3.4万」・en「1.2M」「34K」）。 */
export function compactCount(n: number, lang: string): string {
  return new Intl.NumberFormat(lang, { notation: 'compact', maximumFractionDigits: 1, useGrouping: false }).format(n)
}

/**
 * 丸の大きさ。**その図に描く丸の中**の最小〜最大の件数で対数をとり、**面積**が対数に比例するよう
 * 半径は √t（`r = rMin + (rMax - rMin) * √t`）。件数が 1 つだけ・全部同じなら中間（t = 0.5）。
 * 件数の無い丸（未取得・0 件）は rMin。固定の上限で頭打ちしない。
 */
export function sizeScale(
  counts: (number | undefined)[],
  rMin: number,
  rMax: number,
): (count: number | undefined) => number {
  const known = counts.filter((n): n is number => n != null && n > 0)
  const lo = known.length ? Math.log10(Math.min(...known) + 1) : 0
  const hi = known.length ? Math.log10(Math.max(...known) + 1) : 0
  return (count) => {
    if (count == null || !(count > 0)) return rMin
    const t = hi > lo ? (Math.log10(count + 1) - lo) / (hi - lo) : 0.5
    return Math.round(rMin + (rMax - rMin) * Math.sqrt(Math.min(1, Math.max(0, t))))
  }
}

/** `GET /api/kinds/counts` のうち、ハブのグラフの種類 IRI → 件数。 */
export function hubCountsOf(counts: KindCounts): Record<string, number> {
  const out: Record<string, number> = {}
  // 件数が上限で切れたら途中の件数は嘘になる。空にして「件数なし」の最小の丸に落とす。
  if (counts.truncated) return out
  for (const g of counts.graphs) {
    if (!g.hub) continue
    for (const k of g.kinds) out[k.class_iri] = (out[k.class_iri] ?? 0) + k.count
  }
  return out
}

export interface OverviewInput {
  /** `id` はカタログの id（画面遷移・件数の鍵）、`apiId` は登録 id（つながりの参加者と突き合わせる）。 */
  datasets: { id: string; apiId: string; name: string; rules: DatasetRules }[]
  classCounts?: Record<string, number>
  classCountsByDataset?: Record<string, Record<string, number>>
  crosswalks?: CrosswalkPerspective[]
  /** ハブのグラフの種類 IRI → 件数（{@link hubCountsOf}）。 */
  hubCounts?: Record<string, number>
  standardNames?: Record<string, string>
  alignments?: Alignment[]
  /** 名前のないハブの代わりの語。 */
  unnamedHub: string
  /** 周りだけ開く（フォーカス）で省いた種類の数（データセットの id → 数）。枠に「ほか N 種類」と書く。 */
  omitted?: Record<string, number>
}

export interface OverviewCircle {
  id: string
  /** 所属の枠（データセットの id）。 */
  dataset: string
  classIri: string
  label: string
  count?: number
  r: number
  /** データセットごとの俯瞰だけ: この丸（データセット）が持つ種類の数。 */
  kindCount?: number
  /** 中心の座標。 */
  x: number
  y: number
}
export interface OverviewFrame {
  id: string
  label: string
  x: number
  y: number
  w: number
  h: number
  /** 周りだけ開いたとき、この枠から省いた種類の数。 */
  omitted?: number
}
export interface OverviewHub {
  id: string
  label: string
  count?: number
  r: number
  x: number
  y: number
}
export interface OverviewStd {
  id: string
  label: string
  vocab: string
  x: number
  y: number
  w: number
  h: number
}
export type OverviewEdgeKind = 'link' | 'hub' | 'standard' | 'alignment'
export interface OverviewEdge {
  from: string
  to: string
  kind: OverviewEdgeKind
  /** 対応は両向き。 */
  both?: boolean
  /** データセットごとの俯瞰だけ: ハブへ参加している種類の数（線の title「N 種類が参加」）。 */
  kinds?: number
  /** 端の丸・枠の縁にそろえた線の両端（図の座標）。 */
  x1: number
  y1: number
  x2: number
  y2: number
}
export interface OverviewLayout {
  circles: OverviewCircle[]
  frames: OverviewFrame[]
  hubs: OverviewHub[]
  stds: OverviewStd[]
  band: { x: number; y: number; w: number; h: number } | null
  /** データセットごとの俯瞰だけ: 真ん中の「つながり」の帯。 */
  hubBand?: { x: number; y: number; w: number; h: number } | null
  /** 'dataset' = データセットごとの俯瞰（丸 = データセット）。無ければ種類まで。 */
  level?: 'dataset'
  edges: OverviewEdge[]
  width: number
  height: number
}

export type Anchor =
  | { type: 'circle'; x: number; y: number; r: number }
  | { type: 'rect'; x: number; y: number; w: number; h: number }

export const centerOf = (a: Anchor) =>
  a.type === 'circle' ? { x: a.x, y: a.y } : { x: a.x + a.w / 2, y: a.y + a.h / 2 }

/** `a` の縁のうち、`toward` へ向かう線が出るところ。 */
export function edgePoint(a: Anchor, toward: { x: number; y: number }): { x: number; y: number } {
  const c = centerOf(a)
  const dx = toward.x - c.x
  const dy = toward.y - c.y
  const len = Math.hypot(dx, dy)
  if (len === 0) return c
  if (a.type === 'circle') return { x: c.x + (dx / len) * a.r, y: c.y + (dy / len) * a.r }
  const hw = a.w / 2
  const hh = a.h / 2
  const t = Math.min(dx === 0 ? Infinity : hw / Math.abs(dx), dy === 0 ? Infinity : hh / Math.abs(dy))
  return { x: c.x + dx * t, y: c.y + dy * t }
}

const isPlumbing = (iri: string) => PLUMBING_NS.some((ns) => iri.startsWith(ns))

export function layoutKindOverview(input: OverviewInput): OverviewLayout {
  const { datasets, classCounts = {}, classCountsByDataset, crosswalks = [], hubCounts = {} } = input
  const standardNames = input.standardNames ?? {}

  // ── 1. 枠ごとの丸（同じ種類は rulesShape が畳んだものを使う）と、種類どうしの線 ──
  interface Draft {
    id: string
    dataset: string
    classIri: string
    label: string
    count?: number
    r: number
    order: number
  }
  interface FrameDraft {
    ds: OverviewInput['datasets'][number]
    drafts: Draft[]
    links: [string, string][]
    w: number
    h: number
    /** 丸の中心（枠の左上からの相対）。 */
    at: Map<string, { x: number; y: number }>
  }
  const staged = datasets.map((ds) => {
    const shape = rulesShape(ds.rules, { label: (m) => kindLabelOf(ds.rules, m) })
    const countSource = classCountsByDataset ? (classCountsByDataset[ds.id] ?? {}) : classCounts
    const drafts: Draft[] = shape.nodes.map((n, order) => {
      const m = ds.rules.maps.find((x) => x.id === n.id)!
      const classIri = (m.subject.class_iris ?? [])[0] ?? ''
      const count = classIri ? countSource[classIri] : undefined
      return { id: `${ds.id}::${n.id}`, dataset: ds.id, classIri, label: n.label, count, r: R_MIN, order }
    })
    // 件数の多い順（件数なしは最後・同数は入力順）。
    drafts.sort((a, b) => (b.count ?? -1) - (a.count ?? -1) || a.order - b.order)
    const links = shape.edges.map((e) => [`${ds.id}::${e.from}`, `${ds.id}::${e.to}`] as [string, string])
    return { ds, drafts, links }
  })
  // 丸の大きさは、この図に描く種類の件数ぜんぶで 1 つのスケール（並べる前に決める）。
  const kindR = sizeScale(staged.flatMap((f) => f.drafts.map((d) => d.count)), R_MIN, R_MAX)
  for (const f of staged) for (const d of f.drafts) d.r = kindR(d.count)
  const frameDrafts: FrameDraft[] = staged.map(({ ds, drafts, links }) => {
    // 枠の中の並べかた: 1 行 4 つ。1 つの枠の升の幅は、中でいちばん大きい丸で揃える。
    const maxR = Math.max(R_MIN, ...drafts.map((d) => d.r))
    const cellW = Math.max(2 * maxR, LABEL_W) + CELL_GAP
    const cols = Math.min(PER_ROW, Math.max(1, drafts.length))
    const w = Math.max(FRAME_MIN_W, cols * cellW - CELL_GAP + FRAME_PAD_X * 2)
    const at = new Map<string, { x: number; y: number }>()
    let y = FRAME_PAD_TOP
    for (let i = 0; i < drafts.length; i += PER_ROW) {
      const row = drafts.slice(i, i + PER_ROW)
      const rowR = Math.max(...row.map((d) => d.r))
      const rowW = row.length * cellW - CELL_GAP
      const left = (w - rowW) / 2
      row.forEach((d, k) => at.set(d.id, { x: left + k * cellW + cellW / 2 - CELL_GAP / 2, y: y + rowR }))
      y += 2 * rowR + LABEL_H + ROW_GAP
    }
    const h = y - ROW_GAP + FRAME_PAD_BOT
    return { ds, drafts, links, w, h, at }
  })

  // ── 2. 3 列に置く（左右交互・各列は縦に積む） ──
  const left = frameDrafts.filter((_, i) => i % 2 === 0)
  const right = frameDrafts.filter((_, i) => i % 2 === 1)
  const colW = (col: FrameDraft[]) => Math.max(0, ...col.map((f) => f.w))
  const hubColW = Math.max(2 * R_MAX, HUB_LABEL_W)
  const hubX = colW(left) + COL_GAP // ハブ列の左端
  const rightX = hubX + hubColW + COL_GAP
  const frames: OverviewFrame[] = []
  const circles: OverviewCircle[] = []
  const place = (col: FrameDraft[], x: number) => {
    let y = 0
    for (const f of col) {
      const omitted = input.omitted?.[f.ds.id]
      frames.push({
        id: f.ds.id,
        label: f.ds.name,
        x,
        y,
        w: f.w,
        h: f.h,
        ...(omitted && omitted > 0 ? { omitted } : {}),
      })
      for (const d of f.drafts) {
        const p = f.at.get(d.id)!
        circles.push({
          id: d.id,
          dataset: d.dataset,
          classIri: d.classIri,
          label: d.label,
          count: d.count,
          r: d.r,
          x: x + p.x,
          y: y + p.y,
        })
      }
      y += f.h + FRAME_GAP
    }
    return y - FRAME_GAP
  }
  const leftH = place(left, 0)
  const rightH = right.length ? place(right, rightX) : 0
  // 枠は入力順に並べ直す（描く順・テストの読みやすさ）。
  const order = new Map(datasets.map((d, i) => [d.id, i]))
  frames.sort((a, b) => order.get(a.id)! - order.get(b.id)!)
  const columnsH = Math.max(leftH, rightH, 0)
  const width = right.length ? rightX + colW(right) : hubX + hubColW

  const frameById = new Map(frames.map((f) => [f.id, f]))
  const circleById = new Map(circles.map((c) => [c.id, c]))

  // ── 3. ハブ（概念ごとに 1 つ）と、参加者からの線の足場 ──
  interface HubDraft {
    id: string
    label: string
    count?: number
    r: number
    /** 線の出どころ（丸 id か枠 id）。 */
    sources: string[]
  }
  const datasetByApiId = new Map(datasets.map((d) => [d.apiId, d]))
  const hubDrafts: HubDraft[] = []
  for (const p of crosswalks) {
    const fallback = perspectiveDisplayName(p)
    for (const c of p.config?.concepts ?? []) {
      const count = c.class_iri ? hubCounts[c.class_iri] : undefined
      const sources: string[] = []
      for (const part of c.participants ?? []) {
        const ds = datasetByApiId.get(part.dataset_id)
        if (!ds) continue
        const hit = part.subject_class
          ? circles.find((x) => x.dataset === ds.id && x.classIri === part.subject_class)
          : undefined
        const src = hit ? hit.id : ds.id
        if (!sources.includes(src)) sources.push(src)
      }
      hubDrafts.push({
        id: `hub:${p.perspective_id}:${c.name}`,
        label: conceptName(c.name, c.concept_label) ?? fallback ?? input.unnamedHub,
        count,
        r: R_MIN,
        sources,
      })
    }
  }
  // ハブの丸はハブの件数で別のスケール（種類の丸とは比べない）。
  const hubR = sizeScale(hubDrafts.map((h) => h.count), R_MIN, R_MAX)
  for (const h of hubDrafts) h.r = hubR(h.count)
  // 重心の高さ（つながる枠の中心の高さの平均）が近い順。つながりの無いハブは最後。
  const frameCy = (id: string) => {
    const f = frameById.get(circleById.get(id)?.dataset ?? id)
    return f ? f.y + f.h / 2 : 0
  }
  const centroid = (h: HubDraft) =>
    h.sources.length ? h.sources.reduce((s, id) => s + frameCy(id), 0) / h.sources.length : Infinity
  const sortedHubs = [...hubDrafts].sort(
    (a, b) => centroid(a) - centroid(b) || a.label.localeCompare(b.label, 'ja'),
  )
  const hubs: OverviewHub[] = []
  let hubBottom = -HUB_ROW_GAP
  for (const h of sortedHubs) {
    const c = centroid(h)
    const want = Number.isFinite(c) ? c : hubBottom + HUB_ROW_GAP + h.r
    const y = Math.max(want, hubBottom + HUB_ROW_GAP + h.r)
    hubs.push({ id: h.id, label: h.label, count: h.count, r: h.r, x: hubX + hubColW / 2, y })
    hubBottom = y + h.r + LABEL_H
  }
  const bodyH = Math.max(columnsH, hubs.length ? hubBottom - LABEL_H : 0)

  // ── 4. 標準のことば（種類の class_iris のうち既知の語彙の語）と、対応の足場 ──
  const stdMap = new Map<string, { label: string; vocab: string }>()
  const ensureStd = (iri: string) => {
    if (!stdMap.has(iri)) {
      const v = knownVocabForIri(iri)!
      stdMap.set(iri, {
        label: (standardNames[iri] ?? '').trim() || localName(iri),
        vocab: v.prefix.replace(/:$/, ''),
      })
    }
  }
  const usable = (iri: string) => !!iri && !isPlumbing(iri) && !!knownVocabForIri(iri)
  const stdLinks: [string, string][] = [] // 丸 id → 語 IRI
  for (const f of frameDrafts) {
    for (const d of f.drafts) {
      const m = f.ds.rules.maps.find((x) => `${f.ds.id}::${x.id}` === d.id)!
      for (const iri of m.subject.class_iris ?? []) {
        if (!usable(iri)) continue
        ensureStd(iri)
        stdLinks.push([d.id, iri])
      }
    }
  }
  // 対応: 少なくとも片端が種類の丸。もう片端は種類・既知の語彙の語。項目の対応は描かない。
  // 同じ種類 IRI を複数のデータセットが名乗ることがある。対応はその全ての丸に張る（標準の線と揃える）。
  const circlesOfIri = new Map<string, string[]>()
  for (const c of circles) {
    if (!c.classIri) continue
    const list = circlesOfIri.get(c.classIri) ?? []
    list.push(c.id)
    circlesOfIri.set(c.classIri, list)
  }
  const alignLinks: [string, string][] = []
  const alignSeen = new Set<string>()
  const pushAlign = (from: string, to: string) => {
    if (from === to) return
    const key = [from, to].sort().join('\u0000')
    if (alignSeen.has(key)) return
    alignSeen.add(key)
    for (const id of [from, to]) if (!circleById.has(id)) ensureStd(id)
    alignLinks.push([from, to])
  }
  for (const a of input.alignments ?? []) {
    const ends = [a.source, a.target].map((iri) => circlesOfIri.get(iri) ?? [])
    const resolveOther = (iri: string): string[] => {
      const own = circlesOfIri.get(iri)
      if (own) return own
      if (stdMap.has(iri) || usable(iri)) return [iri]
      return []
    }
    let froms: string[] = []
    let tos: string[] = []
    if (ends[0].length && ends[1].length) [froms, tos] = [ends[0], ends[1]]
    else if (ends[0].length) [froms, tos] = [ends[0], resolveOther(a.target)]
    else if (ends[1].length) [froms, tos] = [resolveOther(a.source), ends[1]]
    for (const f of froms) for (const t of tos) pushAlign(f, t)
  }

  const stdIds = [...stdMap.keys()]
  const stds: OverviewStd[] = []
  let band: OverviewLayout['band'] = null
  let height = bodyH
  let totalW = width
  if (stdIds.length) {
    const perRow = Math.max(1, Math.floor((Math.max(width, STD_W + BAND_PAD_SIDE * 2) - BAND_PAD_SIDE * 2 + STD_GAP) / (STD_W + STD_GAP)))
    const rows = Math.ceil(stdIds.length / perRow)
    const innerW = Math.min(stdIds.length, perRow) * STD_W + (Math.min(stdIds.length, perRow) - 1) * STD_GAP
    totalW = Math.max(width, innerW + BAND_PAD_SIDE * 2)
    const bandTop = bodyH + BAND_GAP
    const bandH = BAND_PAD_TOP + rows * (STD_H + STD_GAP) - STD_GAP + BAND_PAD_BOT
    band = { x: 0, y: bandTop, w: totalW, h: bandH }
    stdIds.forEach((iri, i) => {
      const r = Math.floor(i / perRow)
      const inRow = Math.min(perRow, stdIds.length - r * perRow)
      const rowW = inRow * STD_W + (inRow - 1) * STD_GAP
      const x0 = (totalW - rowW) / 2
      const s = stdMap.get(iri)!
      stds.push({
        id: iri,
        label: s.label,
        vocab: s.vocab,
        x: x0 + (i - r * perRow) * (STD_W + STD_GAP),
        y: bandTop + BAND_PAD_TOP + r * (STD_H + STD_GAP),
        w: STD_W,
        h: STD_H,
      })
    })
    height = bandTop + bandH
  }

  // ── 5. 線（両端は丸・枠・箱の縁にそろえる） ──
  const anchors = new Map<string, Anchor>()
  for (const f of frames) anchors.set(f.id, { type: 'rect', x: f.x, y: f.y, w: f.w, h: f.h })
  for (const c of circles) anchors.set(c.id, { type: 'circle', x: c.x, y: c.y, r: c.r })
  for (const h of hubs) anchors.set(h.id, { type: 'circle', x: h.x, y: h.y, r: h.r })
  for (const s of stds) anchors.set(s.id, { type: 'rect', x: s.x, y: s.y, w: s.w, h: s.h })
  const edges: OverviewEdge[] = []
  const edgeSeen = new Set<string>()
  const addEdge = (from: string, to: string, kind: OverviewEdgeKind, both?: boolean) => {
    const a = anchors.get(from)
    const b = anchors.get(to)
    const key = `${kind}\u0000${from}\u0000${to}`
    if (!a || !b || edgeSeen.has(key)) return
    edgeSeen.add(key)
    const p1 = edgePoint(a, centerOf(b))
    const p2 = edgePoint(b, centerOf(a))
    edges.push({ from, to, kind, ...(both ? { both } : {}), x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y })
  }
  for (const f of frameDrafts) for (const [a, b] of f.links) addEdge(a, b, 'link')
  for (const h of hubDrafts) for (const s of h.sources) addEdge(s, h.id, 'hub')
  for (const [c, iri] of stdLinks) addEdge(c, iri, 'standard')
  for (const [a, b] of alignLinks) addEdge(a, b, 'alignment', true)

  return { circles, frames, hubs, stds, band, edges, width: totalW, height }
}

/** 「全体」表示の集計の帯（図に描いたものだけを数える。項目や接地の候補は「詳しく」の数字）。 */
export function overviewStats(layout: OverviewLayout): {
  datasets: number
  kinds: number
  hubs: number
  standards: number
  records: number
} {
  if (layout.level === 'dataset') {
    // データセットごとの俯瞰: 丸はデータセット。種類は各データセットの種類の合計。
    return {
      datasets: layout.circles.length,
      kinds: layout.circles.reduce((sum, c) => sum + (c.kindCount ?? 0), 0),
      hubs: layout.hubs.length,
      standards: 0,
      records: layout.circles.reduce((sum, c) => sum + (c.count ?? 0), 0),
    }
  }
  return {
    datasets: layout.frames.length,
    kinds: layout.circles.length,
    hubs: layout.hubs.length,
    standards: layout.stds.length,
    records: layout.circles.reduce((sum, c) => sum + (c.count ?? 0), 0),
  }
}
