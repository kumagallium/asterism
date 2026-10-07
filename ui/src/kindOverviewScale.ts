// 「共通のことば」の「全体」表示を**大量のデータセットに耐える形**にする部分
// （shared-vocab-graph.md §6）。決め打ちの配置・乱数も時刻も使わない（同じ入力は同じ図）。
//
//   ・段の選び方: 小さいときは種類まで（`layoutKindOverview`）、超えたらデータセットごと
//     （`layoutDatasetOverview`）。
//   ・フォーカス: 俯瞰でデータセット・ハブを押したら、その周りだけを種類まで描く。
//     配置関数は変えず、入力を絞るだけ（`focusOverview`）。
//   ・線の強弱: 載せた丸につながる線だけ濃くする className（`edgeClassName`）。
import type { Alignment } from './crosswalkApi'
import { conceptName, perspectiveDisplayName } from './crosswalkLabels'
import type { DatasetRules } from './galleryApi'
import {
  edgePoint,
  HUB_LABEL_W,
  LABEL_H,
  radiusOf,
  R_MAX,
  R_MIN,
  type Anchor,
  type OverviewCircle,
  type OverviewEdge,
  type OverviewEdgeKind,
  type OverviewHub,
  type OverviewInput,
  type OverviewLayout,
} from './kindOverview'
import { rulesShape } from './shapeGraph'

/** これ以下なら種類まで（丸）で全部描く。 */
export const KIND_LEVEL_MAX_DATASETS = 6
export const KIND_LEVEL_MAX_KINDS = 40
/** データセットの丸の半径の上限。 */
export const DS_R_MAX = 56
/** データセットの丸の名前の幅。 */
export const DS_LABEL_W = 112

export type OverviewLevel = 'kind' | 'dataset'

export function chooseLevelByCounts(datasets: number, kinds: number): OverviewLevel {
  return datasets <= KIND_LEVEL_MAX_DATASETS && kinds <= KIND_LEVEL_MAX_KINDS ? 'kind' : 'dataset'
}

type DatasetIn = OverviewInput['datasets'][number]

/** 畳んだあとの種類の数（図の丸の数と同じ）。 */
const kindsOf = (rules: DatasetRules) => rulesShape(rules).nodes.length

export function chooseOverviewLevel(datasets: Pick<DatasetIn, 'rules'>[]): OverviewLevel {
  return chooseLevelByCounts(
    datasets.length,
    datasets.reduce((sum, d) => sum + kindsOf(d.rules), 0),
  )
}

// ── ハブ ──
export interface HubInfo {
  /** 種類まで図のハブと同じ id（`hub:<perspective>:<concept>`）。 */
  id: string
  label: string
  count?: number
  r: number
  /** 参加者。`datasetId` はカタログの id（描いていないデータセットは除く）。 */
  parts: { datasetId: string; classIri?: string }[]
}

export function collectHubs(input: OverviewInput): HubInfo[] {
  const byApi = new Map(input.datasets.map((d) => [d.apiId, d]))
  const out: HubInfo[] = []
  for (const p of input.crosswalks ?? []) {
    const fallback = perspectiveDisplayName(p)
    for (const c of p.config?.concepts ?? []) {
      const count = c.class_iri ? input.hubCounts?.[c.class_iri] : undefined
      const parts: HubInfo['parts'] = []
      for (const part of c.participants ?? []) {
        const ds = byApi.get(part.dataset_id)
        if (ds) parts.push({ datasetId: ds.id, classIri: part.subject_class || undefined })
      }
      out.push({
        id: `hub:${p.perspective_id}:${c.name}`,
        label: conceptName(c.name, c.concept_label) ?? fallback ?? input.unnamedHub,
        count,
        r: radiusOf(count),
        parts,
      })
    }
  }
  return out
}

// ── フォーカス（周りだけ開く） ──
export type OverviewFocus = { type: 'dataset' | 'hub'; id: string }

const classIriOf = (m: DatasetRules['maps'][number]) => (m.subject.class_iris ?? [])[0] ?? ''

/**
 * 俯瞰で押した 1 つの周りだけに入力を絞る。
 *  ・データセット D: D の全部の種類・D が参加するハブ・そのハブの他の参加者の「参加している種類だけ」
 *  ・ハブ H: H と、H に参加する全部のデータセットの「参加している種類だけ」
 * 省いた種類の数は `omitted`（枠に「ほか N 種類」）。見つからなければ null。
 */
export function focusOverview(
  input: OverviewInput,
  focus: OverviewFocus,
): { input: OverviewInput; label: string } | null {
  const hubs = collectHubs(input)
  let label: string
  let chosen: HubInfo[]
  // データセット id → 残す種類 IRI（null = 全部）
  const keep = new Map<string, Set<string> | null>()
  if (focus.type === 'dataset') {
    const ds = input.datasets.find((d) => d.id === focus.id)
    if (!ds) return null
    label = ds.name
    chosen = hubs.filter((h) => h.parts.some((p) => p.datasetId === ds.id))
    keep.set(ds.id, null)
  } else {
    const hub = hubs.find((h) => h.id === focus.id)
    if (!hub) return null
    label = hub.label
    chosen = [hub]
  }
  for (const h of chosen) {
    for (const p of h.parts) {
      if (keep.has(p.datasetId) && keep.get(p.datasetId) === null) continue
      if (!p.classIri) {
        keep.set(p.datasetId, null) // 種類を指していない参加者は絞れない（データセット全体）
        continue
      }
      const set = keep.get(p.datasetId) ?? new Set<string>()
      set.add(p.classIri)
      keep.set(p.datasetId, set)
    }
  }
  const omitted: Record<string, number> = {}
  const datasets = input.datasets
    .filter((d) => keep.has(d.id))
    .map((d) => {
      const want = keep.get(d.id)
      if (!want) return d
      const maps = d.rules.maps.filter((m) => want.has(classIriOf(m)))
      if (maps.length === 0) return d // 合う種類が無ければ絞らない（空の枠は描かない）
      const rules = { ...d.rules, maps }
      const gone = kindsOf(d.rules) - kindsOf(rules)
      if (gone > 0) omitted[d.id] = gone
      return { ...d, rules }
    })
  const chosenIds = new Set(chosen.map((h) => h.id))
  const crosswalks = (input.crosswalks ?? [])
    .map((p) => ({
      ...p,
      config: p.config
        ? {
            ...p.config,
            concepts: (p.config.concepts ?? []).filter((c) => chosenIds.has(`hub:${p.perspective_id}:${c.name}`)),
          }
        : p.config,
    }))
    .filter((p) => (p.config?.concepts ?? []).length > 0)
  return { input: { ...input, datasets, crosswalks, omitted }, label }
}

// ── 線の強弱 ──
export function edgeClassName(kind: OverviewEdgeKind, from: string, to: string, active: string | null): string {
  const base = `kind-ov-edge kind-ov-edge--${kind}`
  if (!active) return base
  return `${base} ${from === active || to === active ? 'is-hot' : 'is-dim'}`
}

// ── 交差の数（試験・測定用） ──
type Seg = Pick<OverviewEdge, 'from' | 'to' | 'x1' | 'y1' | 'x2' | 'y2'>
const orient = (ax: number, ay: number, bx: number, by: number, cx: number, cy: number) =>
  Math.sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))

/** 線どうしが真ん中で交わる数。端の節を共有する線の組は数えない。 */
export function countCrossings(edges: Seg[]): number {
  let n = 0
  for (let i = 0; i < edges.length; i++) {
    const a = edges[i]
    for (let j = i + 1; j < edges.length; j++) {
      const b = edges[j]
      if (a.from === b.from || a.from === b.to || a.to === b.from || a.to === b.to) continue
      const o1 = orient(a.x1, a.y1, a.x2, a.y2, b.x1, b.y1)
      const o2 = orient(a.x1, a.y1, a.x2, a.y2, b.x2, b.y2)
      const o3 = orient(b.x1, b.y1, b.x2, b.y2, a.x1, a.y1)
      const o4 = orient(b.x1, b.y1, b.x2, b.y2, a.x2, a.y2)
      if (o1 * o2 < 0 && o3 * o4 < 0) n++
    }
  }
  return n
}

// ── データセットごとの俯瞰 ──
const WIDTH_START = 1400
const WIDTH_STEP = 200
const WIDTH_MAX = 4000
/** 高さの目安。収まらなければ図を横へ広げる。 */
export const HEIGHT_TARGET = 1500
const CELL_GAP = 16
const ROW_GAP = 12
const SIDE_GAP = 40
const BAND_TITLE_H = 40
const BAND_PAD_BOT = 16
const BAND_PAD_SIDE = 22
const SWEEPS = 4

export function layoutDatasetOverview(
  input: OverviewInput,
  opts: { width?: number; sweeps?: number } = {},
): OverviewLayout {
  if (opts.width) return build(input, opts.width, opts.sweeps ?? SWEEPS)
  let width = WIDTH_START
  let out = build(input, width, opts.sweeps ?? SWEEPS)
  // 高さが目安を超えるときは、行の数を減らすために図を広げる（縮小の下限で窓に収まらなくなる前に）。
  while (out.height > HEIGHT_TARGET && width < WIDTH_MAX) {
    width += WIDTH_STEP
    out = build(input, width, opts.sweeps ?? SWEEPS)
  }
  return out
}

function build(input: OverviewInput, width: number, sweeps: number): OverviewLayout {
  const { datasets, classCounts = {}, classCountsByDataset } = input
  // ── 1. データセットごとの丸 ──
  interface Info {
    ds: DatasetIn
    idx: number
    kinds: number
    total?: number
    r: number
    classIris: Set<string>
  }
  const infos: Info[] = datasets.map((ds, idx) => {
    const shape = rulesShape(ds.rules)
    const src = classCountsByDataset ? (classCountsByDataset[ds.id] ?? {}) : classCounts
    let total: number | undefined
    const classIris = new Set<string>()
    for (const n of shape.nodes) {
      const m = ds.rules.maps.find((x) => x.id === n.id)!
      const iri = classIriOf(m)
      if (!iri) continue
      classIris.add(iri)
      if (src[iri] != null) total = (total ?? 0) + src[iri]
    }
    return { ds, idx, kinds: shape.nodes.length, total, r: radiusOf(total, DS_R_MAX), classIris }
  })

  // ── 2. ハブと、データセット ↔ ハブのつながり（1 本にまとめる） ──
  const hubInfos = collectHubs(input)
  const hubDs = new Map<string, Map<number, Set<string>>>() // hub → データセット idx → 参加している種類
  const dsHubs = new Map<number, string[]>()
  const idxById = new Map(infos.map((i) => [i.ds.id, i.idx]))
  for (const h of hubInfos) {
    const m = new Map<number, Set<string>>()
    for (const p of h.parts) {
      const i = idxById.get(p.datasetId)!
      const set = m.get(i) ?? new Set<string>()
      set.add(p.classIri ?? '')
      m.set(i, set)
    }
    hubDs.set(h.id, m)
    for (const i of m.keys()) dsHubs.set(i, [...(dsHubs.get(i) ?? []), h.id])
  }

  // ── 3. 升の大きさと置き場（上・下に交互・帯に近い行から詰める） ──
  const maxR = Math.max(R_MIN, ...infos.map((i) => i.r))
  const cellW = Math.max(2 * maxR, DS_LABEL_W) + CELL_GAP
  const perRow = Math.max(1, Math.floor(width / cellW))
  const hubR = Math.max(R_MIN, ...hubInfos.map((h) => h.r))
  const hubCellW = Math.max(2 * R_MAX, HUB_LABEL_W) + CELL_GAP
  const hubPerRow = Math.max(1, Math.floor((width - 2 * BAND_PAD_SIDE) / hubCellW))
  const hubRowH = 2 * hubR + LABEL_H

  // 各段（0 = 上・1 = 下）。つながりの無いデータセットは段の最後に回す。
  const sides: number[][] = [[], []]
  for (const i of infos) sides[i.idx % 2].push(i.idx)
  const rows: { side: 0 | 1; k: number; members: number[] }[] = []
  for (const s of [0, 1] as const) {
    const ordered = [...sides[s].filter((i) => dsHubs.has(i)), ...sides[s].filter((i) => !dsHubs.has(i))]
    for (let k = 0; k * perRow < ordered.length; k++)
      rows.push({ side: s, k, members: ordered.slice(k * perRow, (k + 1) * perRow) })
  }
  const rowH = (members: number[]) => 2 * Math.max(R_MIN, ...members.map((i) => infos[i].r)) + LABEL_H
  const stack = (s: 0 | 1) => rows.filter((r) => r.side === s)
  const upRows = stack(0)
  const downRows = stack(1)
  const sum = (rs: typeof rows) => rs.reduce((t, r, k) => t + rowH(r.members) + (k ? ROW_GAP : 0), 0)
  const U = sum(upRows)

  const hubRowsN = Math.ceil(hubInfos.length / hubPerRow)
  const bandH = hubInfos.length ? BAND_TITLE_H + hubRowsN * hubRowH + (hubRowsN - 1) * ROW_GAP + BAND_PAD_BOT : 0
  const bandTop = U > 0 ? U + SIDE_GAP : 0
  const downStart = bandTop + bandH + (bandH > 0 || U > 0 ? SIDE_GAP : 0)

  const contentW = Math.max(
    200,
    Math.min(perRow, Math.max(0, ...rows.map((r) => r.members.length))) * cellW,
    hubInfos.length ? Math.min(hubPerRow, hubInfos.length) * hubCellW + 2 * BAND_PAD_SIDE : 0,
  )

  // 行の上端（順序に依らない）。上は帯に近い行 k=0 が下。
  const rowTop = new Map<(typeof rows)[number], number>()
  {
    let acc = 0
    upRows.forEach((r, k) => {
      acc += rowH(r.members) + (k ? ROW_GAP : 0)
      rowTop.set(r, bandTop - SIDE_GAP - acc)
    })
    let y = downStart
    for (const r of downRows) {
      rowTop.set(r, y)
      y += rowH(r.members) + ROW_GAP
    }
  }
  const height = downRows.length
    ? downStart + sum(downRows)
    : bandH > 0
      ? bandTop + bandH
      : U

  // ── 4. 並び（Sugiyama の片側交差削減を固定回数・重心の平均） ──
  let hubOrder = hubInfos
    .map((h) => h.id)
    .sort((a, b) => {
      const ha = hubInfos.find((h) => h.id === a)!
      const hb = hubInfos.find((h) => h.id === b)!
      return ha.label.localeCompare(hb.label, 'ja') || a.localeCompare(b)
    })
  const hubSlot = (k: number) => {
    const row = Math.floor(k / hubPerRow)
    const n = Math.min(hubPerRow, hubInfos.length - row * hubPerRow)
    const left = (contentW - n * hubCellW) / 2
    const r = hubInfos.find((h) => h.id === hubOrder[k])!.r
    return {
      x: left + (k - row * hubPerRow) * hubCellW + hubCellW / 2,
      y: bandTop + BAND_TITLE_H + row * (hubRowH + ROW_GAP) + r,
    }
  }
  const hubXs = () => new Map(hubOrder.map((id, k) => [id, hubSlot(k).x]))
  const dsXs = () => {
    const m = new Map<number, number>()
    for (const r of rows) {
      const left = (contentW - r.members.length * cellW) / 2
      r.members.forEach((i, k) => m.set(i, left + k * cellW + cellW / 2))
    }
    return m
  }
  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : Infinity)
  for (let s = 0; s < sweeps; s++) {
    const hx = hubXs()
    for (const r of rows) {
      const key = (i: number) => mean((dsHubs.get(i) ?? []).map((h) => hx.get(h)!))
      r.members.sort((a, b) => {
        const ka = key(a)
        const kb = key(b)
        if (ka === kb) return a - b // 同じ重心・どちらも無しは入力順
        return ka - kb // Infinity（つながり無し）は最後
      })
    }
    const dx = dsXs()
    const hubKey = (id: string) => mean([...hubDs.get(id)!.keys()].map((i) => dx.get(i)!))
    hubOrder = [...hubOrder].sort((a, b) => {
      const ka = hubKey(a)
      const kb = hubKey(b)
      if (ka !== kb) return ka - kb
      const ha = hubInfos.find((h) => h.id === a)!
      const hb = hubInfos.find((h) => h.id === b)!
      return ha.label.localeCompare(hb.label, 'ja') || a.localeCompare(b)
    })
  }

  // ── 5. 座標の確定 ──
  const dx = dsXs()
  const circles: OverviewCircle[] = infos.map((i) => {
    const row = rows.find((r) => r.members.includes(i.idx))!
    return {
      id: i.ds.id,
      dataset: i.ds.id,
      classIri: '',
      label: i.ds.name,
      count: i.total,
      r: i.r,
      kindCount: i.kinds,
      x: dx.get(i.idx)!,
      y: rowTop.get(row)! + i.r,
    }
  })
  const hubs: OverviewHub[] = hubOrder.map((id, k) => {
    const h = hubInfos.find((x) => x.id === id)!
    const p = hubSlot(k)
    return { id, label: h.label, count: h.count, r: h.r, x: p.x, y: p.y }
  })

  // ── 6. 線: データセット → ハブ 1 本・対応はデータセット ↔ データセットの点線 1 本 ──
  const anchors = new Map<string, Anchor>()
  for (const c of circles) anchors.set(c.id, { type: 'circle', x: c.x, y: c.y, r: c.r })
  for (const h of hubs) anchors.set(h.id, { type: 'circle', x: h.x, y: h.y, r: h.r })
  const edges: OverviewEdge[] = []
  const addEdge = (from: string, to: string, kind: OverviewEdgeKind, extra: Partial<OverviewEdge> = {}) => {
    const a = anchors.get(from)
    const b = anchors.get(to)
    if (!a || !b) return
    const ca = { x: a.type === 'circle' ? a.x : 0, y: a.type === 'circle' ? a.y : 0 }
    const cb = { x: b.type === 'circle' ? b.x : 0, y: b.type === 'circle' ? b.y : 0 }
    const p1 = edgePoint(a, cb)
    const p2 = edgePoint(b, ca)
    edges.push({ from, to, kind, ...extra, x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y })
  }
  for (const h of hubInfos) {
    for (const [i, kinds] of hubDs.get(h.id)!) addEdge(infos[i].ds.id, h.id, 'hub', { kinds: kinds.size })
  }
  const dsOfIri = new Map<string, number[]>()
  for (const i of infos) for (const iri of i.classIris) dsOfIri.set(iri, [...(dsOfIri.get(iri) ?? []), i.idx])
  const seen = new Set<string>()
  for (const a of (input.alignments ?? []) as Alignment[]) {
    for (const f of dsOfIri.get(a.source) ?? []) {
      for (const t of dsOfIri.get(a.target) ?? []) {
        if (f === t) continue
        const key = f < t ? `${f}\u0000${t}` : `${t}\u0000${f}`
        if (seen.has(key)) continue
        seen.add(key)
        addEdge(infos[f].ds.id, infos[t].ds.id, 'alignment', { both: true })
      }
    }
  }

  return {
    circles,
    frames: [],
    hubs,
    stds: [],
    band: null,
    hubBand: hubInfos.length ? { x: 0, y: bandTop, w: contentW, h: bandH } : null,
    level: 'dataset',
    edges,
    width: contentW,
    height,
  }
}
