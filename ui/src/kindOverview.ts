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

/** 件数の短い形（有効数字 3 桁。ja「120万」「1980万」「2.3億」・en「1.2M」「19.8M」）。
 *  小数 1 桁で切ると ja で「1978.3万」のような読みにくい形になるため、桁数ではなく有効数字で揃える。
 *  正確な数は丸の title に出す。 */
export function compactCount(n: number, lang: string): string {
  return new Intl.NumberFormat(lang, { notation: 'compact', maximumSignificantDigits: 3 }).format(n)
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
  /** 別の丸をよけて曲げるときの二次ベジェの制御点（図の座標）。無ければ直線。 */
  cx?: number
  cy?: number
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

/** 線がよけるもの。丸（中心と半径）か、四角（丸の下の名前・枠の題）。 */
export type Obstacle = { x: number; y: number; r: number } | { x0: number; y0: number; x1: number; y1: number }

/** 点と障害物の縁の距離（中に入っていれば負）。 */
function gapTo(pt: { x: number; y: number }, o: Obstacle): number {
  if ('r' in o) return Math.hypot(pt.x - o.x, pt.y - o.y) - o.r
  const dx = Math.max(o.x0 - pt.x, 0, pt.x - o.x1)
  const dy = Math.max(o.y0 - pt.y, 0, pt.y - o.y1)
  if (dx > 0 || dy > 0) return Math.hypot(dx, dy)
  return -Math.min(pt.x - o.x0, o.x1 - pt.x, pt.y - o.y0, o.y1 - pt.y)
}

/** 余白の分だけ障害物を太らせる。 */
function inflate(o: Obstacle, m: number): Obstacle {
  return 'r' in o ? { ...o, r: o.r + m } : { x0: o.x0 - m, y0: o.y0 - m, x1: o.x1 + m, y1: o.y1 + m }
}

/** 名前の幅の見積もり（px）。全角は字の大きさ、半角はその 0.62 倍（描画の字は測れないので見積もる）。 */
export function textWidth(text: string, fontPx: number): number {
  let w = 0
  for (const ch of text) w += /[\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF]/.test(ch) ? fontPx : fontPx * 0.62
  return w
}

/** 丸の下の名前と件数の行が占める四角。KindOverviewMap の CircleBox と App.css と同じ寸法:
 *  名前の枠は margin-top 3px・高さ 28px 固定（11px・行 14px・2 行まで。1 行でも 28px）、件数の行はその下 15px（nowrap）。
 *  幅は名前（最大 labelW で折り返す）と件数の字の広い方（字の幅は見積もり）。 */
export function labelRect(
  c: { x: number; y: number; r: number },
  name: string,
  labelW: number,
  countText?: string,
): Obstacle {
  const nameW = Math.min(textWidth(name, 11), labelW)
  const countW = countText ? textWidth(countText, 11) : 0
  const half = Math.max(nameW, countW) / 2
  const top = c.y + c.r + 3
  return { x0: c.x - half, y0: top, x1: c.x + half, y1: top + 28 + (countText ? 15 : 0) }
}

/** 丸の下に出る件数の行の字（見積もり用・ja の形）。KindOverviewMap の countText と同じ出し分け:
 *  種類・ハブ = 件数が丸の中に入らないときだけ「N 件」、データセット = 「N 種類」（件数が丸の外なら「N 種類・M 件」）。 */
export function countLineText(
  o: { r: number; count?: number; kindCount?: number },
  level: 'kind' | 'dataset',
): string | undefined {
  const n = (v: number) => v.toLocaleString('en-US')
  if (level === 'dataset' && o.kindCount != null) {
    return o.count != null && !countInside(o.r) ? `${o.kindCount} 種類・${n(o.count)} 件` : `${o.kindCount} 種類`
  }
  return o.count != null && !countInside(o.r) ? `${n(o.count)} 件` : undefined
}

/** 枠の左上の題（0.85rem・太字。枠の内側の余白 0.5rem / 0.8rem）と、あれば「ほか N 種類」
 *  （.kind-ov-frame-omitted: top 24px・left 14px・11px）が占める四角。 */
export function frameTitleRect(f: { x: number; y: number; w: number; omitted?: number }, title: string): Obstacle {
  const titleW = textWidth(title, 13.6)
  const omittedW = f.omitted ? textWidth(`ほか ${f.omitted} 種類`, 11) : 0
  const w = Math.min(Math.max(titleW, omittedW), f.w - 26)
  return { x0: f.x + 13, y0: f.y + 8, x1: f.x + 13 + w, y1: f.y + (f.omitted ? 40 : 28) }
}
/** 線が丸の縁からあけておく余白。 */
export const ROUTE_MARGIN = 6
/** 曲げる量（頂点のずらし）の刻み。 */
export const ROUTE_STEP = 12
/** 刻みの倍数を何回まで試すか（＋−交互なので最大 ±MAX_STEPS·STEP）。 */
export const ROUTE_MAX_STEPS = 14
/** 曲線を調べる点の数（両端を含めて +1 点）。 */
export const ROUTE_SAMPLES = 24

const quadAt = (p: { x: number; y: number }, c: { x: number; y: number }, q: { x: number; y: number }, t: number) => ({
  x: (1 - t) * (1 - t) * p.x + 2 * t * (1 - t) * c.x + t * t * q.x,
  y: (1 - t) * (1 - t) * p.y + 2 * t * (1 - t) * c.y + t * t * q.y,
})

/** 二次ベジェ（p, c, q）を等間隔に調べ、どの障害物の縁からも余白がいちばん小さい所の値（負 = 中）。
 *  曲線は p・c・q の外接四角の中にあるので、その四角に掛からない障害物は調べない（規模が大きいときの速さ）。
 *  掛かる障害物が無ければ Infinity。調べる点は ROUTE_SAMPLES か、線の長さ 4px ごとの多い方
 *  （細い名前の四角を点の間で取り逃がさない）。 */
function clearance(p: { x: number; y: number }, c: { x: number; y: number }, q: { x: number; y: number }, obs: Obstacle[]): number {
  const bx0 = Math.min(p.x, c.x, q.x)
  const bx1 = Math.max(p.x, c.x, q.x)
  const by0 = Math.min(p.y, c.y, q.y)
  const by1 = Math.max(p.y, c.y, q.y)
  const near = obs.filter((o) =>
    'r' in o
      ? o.x + o.r >= bx0 && o.x - o.r <= bx1 && o.y + o.r >= by0 && o.y - o.r <= by1
      : o.x1 >= bx0 && o.x0 <= bx1 && o.y1 >= by0 && o.y0 <= by1,
  )
  if (near.length === 0) return Infinity
  const approx = Math.hypot(c.x - p.x, c.y - p.y) + Math.hypot(q.x - c.x, q.y - c.y)
  const n = Math.max(ROUTE_SAMPLES, Math.ceil(approx / 4))
  let worst = Infinity
  for (let i = 0; i <= n; i++) {
    const pt = quadAt(p, c, q, i / n)
    for (const o of near) worst = Math.min(worst, gapTo(pt, o))
  }
  return worst
}

/**
 * 線 p→q（両端の丸の中心）が端点でない丸や、丸の下の名前・枠の題（`obstacles`）を通るとき、よける二次ベジェの制御点を返す。
 * 離れていれば null（直線のまま）。頂点を中点から法線（from→to を左に 90° 回した向きが ＋）へ
 * STEP·(1, −1, 2, −2, …) ずらして順に試す。選び方（丸をよけることを、名前をよけることより先にする）:
 * 1. 丸からも名前からも r + MARGIN 以上離れる最初のもの。
 * 2. 無ければ、丸からは離れるもののうち名前からの余白がいちばん大きいもの（直線が丸から離れているなら、
 *    直線より名前の余白が大きいときだけ曲げる）。
 * 3. 丸からも離れきらなければ、丸の余白がいちばん大きいもの（直線より悪ければ直線のまま）。同点は試した順で先。乱数・時刻は使わない。
 */
export function routeAround(
  p: { x: number; y: number },
  q: { x: number; y: number },
  obstacles: Obstacle[],
  opts: {
    margin?: number
    step?: number
    maxSteps?: number
    /** 制御点 c のときに実際に描く線の両端（丸・枠の縁）。渡せば判断も描く線で行う（中心どうしの曲線と描く曲線のずれを無くす）。 */
    endsFor?: (c: { x: number; y: number }) => [{ x: number; y: number }, { x: number; y: number }]
  } = {},
): { cx: number; cy: number } | null {
  const margin = opts.margin ?? ROUTE_MARGIN
  const step = opts.step ?? ROUTE_STEP
  const maxSteps = opts.maxSteps ?? ROUTE_MAX_STEPS
  const len = Math.hypot(q.x - p.x, q.y - p.y)
  if (len === 0) return null
  const need = obstacles.map((o) => inflate(o, margin))
  const circ = need.filter((o) => 'r' in o)
  const rects = need.filter((o) => !('r' in o))
  const clr = (c: { x: number; y: number }) => {
    const [a, b] = opts.endsFor ? opts.endsFor(c) : [p, q]
    return { c: clearance(a, c, b, circ), l: clearance(a, c, b, rects) }
  }
  const mx = (p.x + q.x) / 2
  const my = (p.y + q.y) / 2
  const straight = clr({ x: mx, y: my })
  if (straight.c >= 0 && straight.l >= 0) return null
  // 法線の ＋ = 画面（y が下向き）で見て進行方向の左。(dx, dy) → (dy, -dx)。
  const nx = (q.y - p.y) / len
  const ny = -(q.x - p.x) / len
  let circleOk: { cx: number; cy: number; l: number } | null = null
  let bestC: { cx: number; cy: number; c: number } | null = null
  for (let k = 1; k <= maxSteps; k++) {
    for (const sign of [1, -1]) {
      const d = sign * k * step
      const cand = { cx: mx + nx * 2 * d, cy: my + ny * 2 * d }
      const v = clr({ x: cand.cx, y: cand.cy })
      if (v.c >= 0 && v.l >= 0) return cand
      if (v.c >= 0 && (!circleOk || v.l > circleOk.l)) circleOk = { ...cand, l: v.l }
      if (!bestC || v.c > bestC.c) bestC = { ...cand, c: v.c }
    }
  }
  const pick = (x: { cx: number; cy: number } | null) => (x ? { cx: x.cx, cy: x.cy } : null)
  if (straight.c >= 0) return circleOk && circleOk.l > straight.l ? pick(circleOk) : null
  if (circleOk) return pick(circleOk)
  // 曲げても直線以上に丸から離れないなら、直線に戻す（直線で丸に入っていなかった線を曲げて入れない）。
  return bestC && bestC.c >= straight.c ? pick(bestC) : null
}

/** 両端の縁にそろえた線の座標。障害物をよけるときは制御点（cx, cy）も付く。 */
export function routedLine(
  a: Anchor,
  b: Anchor,
  obstacles: Obstacle[],
): Pick<OverviewEdge, 'x1' | 'y1' | 'x2' | 'y2'> & { cx?: number; cy?: number } {
  const ca = centerOf(a)
  const cb = centerOf(b)
  const route = routeAround(ca, cb, obstacles, { endsFor: (c) => [edgePoint(a, c), edgePoint(b, c)] })
  if (!route) {
    const p1 = edgePoint(a, cb)
    const p2 = edgePoint(b, ca)
    return { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y }
  }
  const ctrl = { x: route.cx, y: route.cy }
  const p1 = edgePoint(a, ctrl)
  const p2 = edgePoint(b, ctrl)
  return { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, cx: route.cx, cy: route.cy }
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
    // 障害物 = 端点でない丸（種類の丸・ハブの丸）と、その下の名前、端点でない枠の題。
    // 端が枠のときは、その枠の中の丸も除く（線は枠の縁から出る）。端点の丸の名前は除く（下へ出る線は必ず通る）。
    const others = [...circles, ...hubs].filter(
      (o) => o.id !== from && o.id !== to && !('dataset' in o && (o.dataset === from || o.dataset === to)),
    )
    const obstacles: Obstacle[] = [
      ...others.map((o) => ({ x: o.x, y: o.y, r: o.r })),
      ...others.map((o) =>
        labelRect(o, o.label, 'dataset' in o ? LABEL_W : HUB_LABEL_W, countLineText(o, 'kind')),
      ),
      ...frames.filter((f) => f.id !== from && f.id !== to).map((f) => frameTitleRect(f, f.label)),
    ]
    edges.push({ from, to, kind, ...(both ? { both } : {}), ...routedLine(a, b, obstacles) })
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
