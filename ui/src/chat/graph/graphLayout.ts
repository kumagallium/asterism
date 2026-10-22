// 1 枚の知識の図の配置（純関数・PR 11f）。3 段（用語の帯／データの星座／標準用語の地平線）をやめ、
//   概念 = 札（一種・一部が段を作る）／ データ = 札の中の行に置く星（星・名前・件数）／ 名前のない星 = 最下段 ／ 標準の語 = 札の中の 1 行。
//
// 返す型は `sky/skyLayout.ts` の `SkyLayout` と同じ（`chatState`・`ChartPane` の選択・濃淡・輪・外す提案の印をそのまま動かすため）。
// 新しいのは `nodes` の座標と `edges[].geom`（線の道すじ）だけ。
//
// 守ること:
//   ・乱数も時刻も使わない。同じデータ・同じ幅は同じ座標（並びの手がかりは名前のハッシュ）。
//   ・段配置の規則は `shapeGraph.rowsOf`／`workspace/conceptMapLayout` と同じ: 一種・一部の線（from が下・to が上）だけが段を作り、
//     下げる・上げる・変える・同じもの は段を作らない（同じ段の横か、段をまたぐ短い線）。段の中は重心（親の平均）で並べて交差を減らす。
//   ・横幅に収まらない段は折り返す。全部の札の `x ± w/2` は `[PAD, width - PAD]` の内側。札どうしは 14px 以上あける。
//   ・線は札の縁で止める（札も印も貫かない）。段をまたぐ線は直線か 1 回折れ、同じ段の離れた札を結ぶ線は段の上を直角に 2 回折れてまたぐ。曲線は使わない。
//   ・星の大きさは件数の固定の対数（ほかの星に依らない。データが増えても既にある星の大きさは変わらない）。
import type { DraftSky, SkyData, SkyDataset, SkyKnowledgeRelation, SkyTerm } from '../../sky/skyApi'
import type { ConceptEdgeGeom } from '../../sky/skyKnowledge'
import {
  PAD,
  TERM_MAX_W,
  draftCardConcepts,
  draftNodeId,
  hash01,
  hubNodeId,
  kindId,
  orderedDatasets,
  termId,
  termLabel,
  type DraftEdgeInfo,
  type DraftNode,
  type HubNode,
  type KindNode,
  type SkyCluster,
  type SkyEdge,
  type SkyLayout,
  type SkyNode,
  type TermNode,
} from '../../sky/skyLayout'

// ── 寸法（px） ─────────────────────────────────────────────────
export const GRAPH_HEIGHT_MIN = 320
/** 札の高さ（標準の語の 1 行が入る札は高い）。札の中の余白は 8px。 */
export const CARD_H = 28
export const CARD_H_STD = 44
export const CARD_PAD = 8
export const CARD_MIN_W = 72
/** 札どうしの間（最小）。線の小さな札が入る隣どうしは、その分だけ広げる。 */
export const CARD_GAP = 14
/** 段と段のあいだ（線・小さな札・「提案」の印が入る高さ）。 */
export const CHANNEL = 48
/** 図の上・下の余白。 */
export const TOP = 44
export const BOTTOM = 24
/** 札の中の星の行（1 行に星 1 つ: 星・名前・件数）の高さと、名前の行との間。 */
export const STAR_ROW_H = 16
export const STAR_ROW_PAD = 4
/** 最下段の星 1 つの横の幅と、名前（12px）＋件数（11px）の行の高さ。 */
export const STAR_SLOT_WIDE = 58
export const STAR_LABEL_H = 30
/** 最下段の見出しの行の高さ。 */
export const HEAD_H = 18
/** 印（提案・外す提案）の字の幅（札の左上の外・11px）。線は印の右から出る。 */
export const markTextWidth = (lang: 'ja' | 'en', remove: boolean): number => (lang === 'en' ? (remove ? 46 : 52) : remove ? 46 : 24)
/** 同じ段の離れた札を結ぶ線が段の上をまたぐ高さ（札の上の縁から。離れるほど高く）。 */
export const LIFT_BASE = 18
export const LIFT_STEP = 8

/** 最下段の星の半径（件数の固定の対数: 1 件 5・10 件 7・100 件 9・1,000 件 12・1 万件以上 13）。 */
export const starRadius = (count: number): number => (count > 0 ? Math.min(13, 5 + 2.2 * Math.log10(count)) : 4)
/** 札の中の行の星の半径（1 件 3・10 件 4・100 件 5・1,000 件以上 6）。 */
export const rowStarRadius = (count: number): number => (count > 0 ? Math.min(6, 3 + Math.log10(count)) : 3)
/** 件数の字の幅の見積もり（「1,234 件」）。 */
const countTextW = (count: number): number => String(count).length * 7.5 + 18

type Pt = { x: number; y: number }

/** 字の幅の見積もり（全角は px×1.04・半角は px×0.58）。 */
export function textWidth(text: string, px: number): number {
  let w = 0
  for (const ch of text) w += (ch.codePointAt(0) ?? 0) > 0xff ? px * 1.04 : px * 0.58
  return w
}

/** 折れ線の点（始点・折れ目・終点）。 */
export function geomPoints(g: ConceptEdgeGeom): Pt[] {
  if (g.pts && g.pts.length >= 2) return g.pts.map((p) => ({ x: p.x, y: p.y }))
  const pts: Pt[] = [{ x: g.x1, y: g.y1 }]
  if (g.bx !== undefined && g.by !== undefined) {
    pts.push({ x: g.bx, y: g.by })
    if (g.bx2 !== undefined && g.by2 !== undefined) pts.push({ x: g.bx2, y: g.by2 })
  }
  pts.push({ x: g.x2, y: g.y2 })
  return pts
}

function geomOf(pts: Pt[], mid?: Pt): ConceptEdgeGeom {
  const a = pts[0]
  const z = pts[pts.length - 1]
  if (pts.length === 2) {
    const m = { x: (a.x + z.x) / 2, y: (a.y + z.y) / 2 }
    return { x1: a.x, y1: a.y, cx: m.x, cy: m.y, x2: z.x, y2: z.y, mx: (mid ?? m).x, my: (mid ?? m).y }
  }
  const b = pts[1]
  const g: ConceptEdgeGeom = { x1: a.x, y1: a.y, cx: b.x, cy: b.y, x2: z.x, y2: z.y, mx: (mid ?? b).x, my: (mid ?? b).y, bx: b.x, by: b.y }
  if (pts.length >= 4) {
    g.bx2 = pts[2].x
    g.by2 = pts[2].y
  }
  if (pts.length >= 5) g.pts = pts.map((q) => ({ x: q.x, y: q.y }))
  return g
}

/** 同じ点が続く所を 1 つにする（折れ目が重なった道すじ）。 */
function dedupe(pts: Pt[]): Pt[] {
  const out: Pt[] = []
  for (const q of pts) {
    const last = out[out.length - 1]
    if (last && Math.abs(last.x - q.x) < 0.5 && Math.abs(last.y - q.y) < 0.5) continue
    out.push(q)
  }
  return out
}

const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)
const mean = (xs: number[]): number => xs.reduce((s, v) => s + v, 0) / xs.length

// ── 線分と四角の交わり（線が札を貫かないかの確かめ） ──────────────
interface Rect {
  l: number
  r: number
  t: number
  b: number
}
function segHitsRect(a: Pt, z: Pt, q: Rect): boolean {
  let t0 = 0
  let t1 = 1
  const dx = z.x - a.x
  const dy = z.y - a.y
  const clip = (p: number, d: number): boolean => {
    if (p === 0) return d >= 0
    const r = d / p
    if (p < 0) {
      if (r > t1) return false
      if (r > t0) t0 = r
    } else {
      if (r < t0) return false
      if (r < t1) t1 = r
    }
    return true
  }
  return clip(-dx, a.x - q.l) && clip(dx, q.r - a.x) && clip(-dy, a.y - q.t) && clip(dy, q.b - a.y) && t1 >= t0
}

/** 折れ線の終点から線に沿って `d` 戻った点（線より短ければ null）。 */
function alongFromEnd(pts: Pt[], d: number): Pt | null {
  let left = d
  for (let i = pts.length - 1; i > 0; i--) {
    const a = pts[i]
    const b = pts[i - 1]
    const len = Math.hypot(b.x - a.x, b.y - a.y)
    if (left <= len) {
      const t = len === 0 ? 0 : left / len
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
    }
    left -= len
  }
  return null
}

// ── 段 ───────────────────────────────────────────────────────────
/** 一種・一部の線で段を決める（`lower` が下・`upper` が上）。最長の道。輪は訪問済みで止める。線のない札は null。 */
export function layerDepths(ids: readonly string[], hier: readonly { lower: string; upper: string }[]): Map<string, number> {
  const known = new Set(ids)
  const uppers = new Map<string, string[]>()
  const linked = new Set<string>()
  for (const h of hier) {
    if (h.lower === h.upper || !known.has(h.lower) || !known.has(h.upper)) continue
    const arr = uppers.get(h.lower) ?? []
    if (!arr.includes(h.upper)) arr.push(h.upper)
    uppers.set(h.lower, arr)
    linked.add(h.lower)
    linked.add(h.upper)
  }
  const depth = new Map<string, number>()
  const seen = new Set<string>()
  const depthOf = (id: string): number => {
    const d = depth.get(id)
    if (d !== undefined) return d
    if (seen.has(id)) return 0
    seen.add(id)
    const ps = uppers.get(id) ?? []
    const v = ps.length === 0 ? 0 : Math.max(...ps.map(depthOf)) + 1
    depth.set(id, v)
    return v
  }
  for (const id of [...ids].sort(cmp)) if (linked.has(id)) depthOf(id)
  return depth
}

/** 全部の札の段: 一種・一部の線のある札はその深さ、無い札は横の線でつながる相手と同じ段、どれにもつながらなければ最上段。 */
function assignLayers(
  ids: readonly string[],
  hier: readonly { lower: string; upper: string }[],
  side: readonly { a: string; b: string }[],
  widthOf: (id: string) => number,
  avail: number,
): Map<string, number> {
  const layer = layerDepths(ids, hier)
  const adj = new Map<string, string[]>(ids.map((i) => [i, []]))
  for (const s of side) {
    adj.get(s.a)?.push(s.b)
    adj.get(s.b)?.push(s.a)
  }
  const queue = [...layer.keys()].sort((a, b) => (layer.get(a) as number) - (layer.get(b) as number) || cmp(a, b))
  for (let qi = 0; qi < queue.length; qi++) {
    const cur = queue[qi]
    for (const nb of [...(adj.get(cur) ?? [])].sort(cmp)) {
      if (layer.has(nb)) continue
      layer.set(nb, layer.get(cur) as number)
      queue.push(nb)
    }
  }
  // どの線にもつながらない札: 最上段に並べて収まるならそこへ。収まらなければ、段を折り返して一種・一部の線を長くしないよう、
  // 一番下の段へ（shapeGraph.rowsOf の「線のない箱は下に」と同じ）。
  const free = ids.filter((id) => !layer.has(id))
  if (free.length > 0) {
    const hierMax = layer.size > 0 ? Math.max(...layer.values()) : -1
    const top = [...layer.keys()].filter((id) => layer.get(id) === 0)
    const all = [...top, ...free]
    const need = all.reduce((s, id) => s + widthOf(id), 0) + 24 * (all.length - 1)
    const to = hierMax < 0 || need <= avail ? 0 : hierMax + 1
    for (const id of free) layer.set(id, to)
  }
  return layer
}

/** 横の線（原因 → 結果）でつながる札が隣り合うように、つながりごとに原因から結果へたどる順。決定論。 */
function chainOrder(ids: readonly string[], side: readonly { a: string; b: string }[]): string[] {
  const out = new Map<string, string[]>(ids.map((i) => [i, []]))
  const into = new Map<string, number>(ids.map((i) => [i, 0]))
  const nbrs = new Map<string, string[]>(ids.map((i) => [i, []]))
  for (const s of side) {
    if (s.a === s.b || !out.has(s.a) || !out.has(s.b)) continue
    out.get(s.a)?.push(s.b)
    into.set(s.b, (into.get(s.b) ?? 0) + 1)
    nbrs.get(s.a)?.push(s.b)
    nbrs.get(s.b)?.push(s.a)
  }
  const h = (id: string): number => hash01(id, 'order')
  const byHash = (a: string, b: string): number => h(a) - h(b) || cmp(a, b)
  const comp = new Map<string, number>()
  const comps: string[][] = []
  for (const id of [...ids].sort(byHash)) {
    if (comp.has(id)) continue
    const members: string[] = []
    const stack = [id]
    comp.set(id, comps.length)
    while (stack.length) {
      const cur = stack.pop() as string
      members.push(cur)
      for (const nb of nbrs.get(cur) ?? []) {
        if (comp.has(nb)) continue
        comp.set(nb, comps.length)
        stack.push(nb)
      }
    }
    comps.push(members)
  }
  // 大きいかたまりから。同じ大きさはハッシュ
  const orderIdx = comps.map((_, i) => i).sort((a, b) => comps[b].length - comps[a].length || byHash(comps[a][0], comps[b][0]) || a - b)
  const result: string[] = []
  const done = new Set<string>()
  for (const ci of orderIdx) {
    const start = [...comps[ci]].sort((a, b) => (into.get(a) ?? 0) - (into.get(b) ?? 0) || byHash(a, b))[0]
    const visit = (id: string): void => {
      if (done.has(id)) return
      done.add(id)
      result.push(id)
      for (const nb of [...(out.get(id) ?? [])].sort(byHash)) visit(nb)
      for (const nb of [...(nbrs.get(id) ?? [])].sort(byHash)) visit(nb)
    }
    visit(start)
  }
  return result
}

/** 並びを保ったまま、希望の中心 `ds` にできるだけ近く、札どうしが `gaps[i]` 以上あくように詰める（等方回帰）。左端 lo・右端 hi の内側。 */
export function packCenters(ds: number[], ws: number[], gaps: number[], lo: number, hi: number): number[] {
  const n = ds.length
  const off: number[] = []
  let acc = 0
  for (let i = 0; i < n; i++) {
    off.push(acc)
    acc += ws[i] + (i < n - 1 ? gaps[i] : 0)
  }
  interface B {
    sum: number
    cnt: number
  }
  const bs: B[] = []
  const owner: number[] = []
  for (let i = 0; i < n; i++) {
    bs.push({ sum: ds[i] - ws[i] / 2 - off[i], cnt: 1 })
    owner.push(bs.length - 1)
    while (bs.length > 1) {
      const cur = bs[bs.length - 1]
      const prev = bs[bs.length - 2]
      if (prev.sum / prev.cnt <= cur.sum / cur.cnt + 1e-9) break
      prev.sum += cur.sum
      prev.cnt += cur.cnt
      bs.pop()
      for (let k = 0; k <= i; k++) if (owner[k] >= bs.length) owner[k] = bs.length - 1
    }
  }
  const yMin = lo
  const yMax = Math.max(yMin, hi - acc)
  return ds.map((_, i) => {
    const b = bs[owner[i]]
    const y = Math.min(yMax, Math.max(yMin, b.sum / b.cnt))
    return y + off[i] + ws[i] / 2
  })
}

// ── 入力 ─────────────────────────────────────────────────────────
export interface GraphOptions {
  width: number
  lang?: 'ja' | 'en'
  /** 高さの下限（既定 320）。 */
  minHeight?: number
  /** 下書きの札（提案）。 */
  drafts?: readonly DraftSky[]
  /** 取り込み済みで未公開のデータセット（薄い星）。`sky.datasets` に入っていれば足さない。 */
  proposedDatasets?: readonly SkyDataset[]
}

const isEq = (rel: string): boolean => rel === 'equivalentClass' || rel === 'equivalentProperty'

/** 提案の線の関係 → 本物の知識の線の関係（同じ関係は二重に引かない）。 */
const REAL_RELATION: Record<string, SkyKnowledgeRelation | undefined> = { part: 'isPartOf', lowers: 'lowers', raises: 'raises', affects: 'affects' }

/** 線の小さな札の幅（12px の字＋左右 6px。一部・一種・量は文字、ほかは記号 1 つ）。 */
const PILL_W_MARK = 24
const PILL_W_TEXT: Record<'ja' | 'en', Record<'part' | 'sub' | 'quantity', number>> = {
  ja: { part: 38, sub: 38, quantity: 26 },
  en: { part: 40, sub: 58, quantity: 64 },
}
function conceptPillW(rel: string | undefined, lang: 'ja' | 'en'): number {
  return rel === 'isPartOf' ? PILL_W_TEXT[lang].part : PILL_W_MARK
}
function draftPillW(i: DraftEdgeInfo, lang: 'ja' | 'en'): number {
  if (i.state === 'retarget' || i.state === 'remove') return PILL_W_MARK
  return i.rel === 'sub' || i.rel === 'part' || i.rel === 'quantity' ? PILL_W_TEXT[lang][i.rel] : PILL_W_MARK
}
const PILL_H = 16

// ── 配置の途中の札 ───────────────────────────────────────────────
interface PCard {
  id: string
  label: string
  w: number
  /** 札の高さ（名前の行＋標準の語の行＋星の行）。`baseH` は星の行を除いた高さ。 */
  h: number
  baseH: number
  /** 行に詰めるときの幅（＝ w）。 */
  colW: number
  layer: number
  row: number
  col: number
  x: number
  cy: number
  /** 札の中の行に置く星（データの種類）。 */
  hung: KindNode[]
  /** 札の左上に印（提案・外す提案）が出る札なら、その字の幅（無ければ 0）。線は印の右から出す。 */
  markW: number
  term?: SkyTerm
  draft?: { d: DraftSky; c: DraftSky['concepts'][number]; count: number }
  stdIri?: string
  stdText?: string
}

interface DraftEdgeIn {
  from: string
  to: string
  info: DraftEdgeInfo
}

export function layoutGraph(sky: SkyData, opts: GraphOptions): SkyLayout {
  const W = Math.max(320, Math.round(opts.width))
  const lang = opts.lang ?? 'ja'
  const avail = W - PAD * 2
  const cardMax = Math.min(TERM_MAX_W, Math.max(CARD_MIN_W, Math.floor((avail - CARD_GAP) / 2)))

  // データセット（公開済み → 未公開の順・作成の古い順）
  const have = new Set(sky.datasets.map((d) => d.id))
  const allDatasets = orderedDatasets({
    ...sky,
    datasets: [...sky.datasets, ...(opts.proposedDatasets ?? []).filter((d) => !have.has(d.id))],
  })

  // 星（種類）。大きさは件数の固定の対数（札の中の行に置く星は後で小さい半径に替える）
  const starR = starRadius
  const stars = new Map<string, KindNode>()
  const starOrder: string[] = []
  for (const d of allDatasets) {
    for (const k of d.kinds) {
      const id = kindId(d.id, k.iri)
      if (stars.has(id)) continue
      stars.set(id, {
        id,
        type: 'kind',
        x: 0,
        y: 0,
        label: k.label,
        iri: k.iri,
        datasetId: d.id,
        count: k.count,
        r: starR(k.count),
        ...(d.proposed ? { proposed: true } : {}),
      })
      starOrder.push(id)
    }
  }

  // ── 札 ──
  const stdByIri = new Map(sky.standards.map((s) => [s.iri, s]))
  const cards: PCard[] = []
  const cardById = new Map<string, PCard>()
  const termIdsBySlug = new Map<string, string>()
  const memberIdsOf = new Map<string, string[]>()
  const relOfMember = new Map<string, Map<string, string>>()
  const hungBy = new Map<string, string>()
  const removedTerms = new Set<string>()
  for (const d of opts.drafts ?? []) for (const iri of d.removals?.terms ?? []) removedTerms.add(iri)
  const termsSorted = [...sky.terms].sort((a, b) => cmp(termId(a.iri), termId(b.iri)))
  for (const t of termsSorted) {
    const id = termId(t.iri)
    const label = termLabel(t, lang)
    const seen = new Set<string>()
    const mids: string[] = []
    const rels = new Map<string, string>()
    for (const m of t.members) {
      const sid = kindId(m.dataset_id, m.iri)
      if (!stars.has(sid) || seen.has(sid)) continue
      seen.add(sid)
      mids.push(sid)
      rels.set(sid, m.relation)
      if (!hungBy.has(sid)) hungBy.set(sid, id)
    }
    memberIdsOf.set(id, mids)
    relOfMember.set(id, rels)
    const std = t.standard ? stdByIri.get(t.standard.iri) : undefined
    const stdText = std ? `≡ ${std.prefix}:${std.label}` : undefined
    const nameW = textWidth(label, 12) + 2 * CARD_PAD
    const stdW = stdText ? textWidth(stdText, 12) + 2 * CARD_PAD : 0
    const w = Math.min(cardMax, Math.max(CARD_MIN_W, Math.ceil(Math.max(nameW, stdW))))
    const c: PCard = {
      id,
      label,
      w,
      h: stdText ? CARD_H_STD : CARD_H,
      baseH: stdText ? CARD_H_STD : CARD_H,
      colW: w,
      layer: 0,
      row: 0,
      col: 0,
      x: 0,
      cy: 0,
      hung: [],
      markW: removedTerms.has(t.iri) ? markTextWidth(lang, true) : 0,
      term: t,
      ...(std ? { stdIri: std.iri, stdText: `${std.prefix}:${std.label}` } : {}),
    }
    cards.push(c)
    cardById.set(id, c)
    termIdsBySlug.set(t.slug, id)
  }
  // 提案の札と、提案の線の端（作る札は下書きの札・既にある用語は用語の札。標準の語は札を持たない）
  const draftEnds = new Map<string, Map<string, string>>()
  for (const d of opts.drafts ?? []) {
    const ends = new Map<string, string>()
    draftEnds.set(d.draft_id, ends)
    const cs = draftCardConcepts(d)
    for (const c of cs) {
      const id = draftNodeId(d.draft_id, c.key)
      if (cardById.has(id)) continue
      const w = Math.min(cardMax, Math.max(CARD_MIN_W, Math.ceil(textWidth(c.label, 12) + 2 * CARD_PAD)))
      const pc: PCard = {
        id,
        label: c.label,
        w,
        h: CARD_H,
        baseH: CARD_H,
        colW: w,
        layer: 0,
        row: 0,
        col: 0,
        x: 0,
        cy: 0,
        hung: [],
        markW: c.state === 'remove' ? markTextWidth(lang, true) : c.state === 'thin' || c.state === undefined || c.proposed === true ? markTextWidth(lang, false) : 0,
        draft: { d, c, count: cs.length },
      }
      cards.push(pc)
      cardById.set(id, pc)
      ends.set(c.key, id)
    }
    for (const c of d.concepts) {
      if (!c.use.startsWith('existing:')) continue
      const tid = termIdsBySlug.get(c.use.slice('existing:'.length))
      if (tid) ends.set(c.key, tid)
    }
  }

  // ── 線の下ごしらえ ──
  const draftEdgesIn: DraftEdgeIn[] = []
  for (const d of opts.drafts ?? []) {
    const ends = draftEnds.get(d.draft_id) as Map<string, string>
    d.relations.forEach((r, i) => {
      const a = ends.get(r.from)
      const b = ends.get(r.to)
      if (!a || !b || a === b) return
      draftEdgesIn.push({
        from: a,
        to: b,
        info: {
          draftId: d.draft_id,
          rel: r.rel,
          state: r.state ?? 'thin',
          index: r.index === undefined ? i : r.index,
          fromKey: r.from,
          toKey: r.to,
          problem: r.problem === true,
        },
      })
    })
  }
  interface ConceptIn {
    from: string
    to: string
    relation: SkyKnowledgeRelation
  }
  const conceptsIn: ConceptIn[] = []
  const conceptSeen = new Set<string>()
  for (const l of sky.edges ?? []) {
    const from = termId(l.from)
    const to = termId(l.to)
    const key = `${from}>${to}>${l.relation}`
    if (from === to || conceptSeen.has(key) || !cardById.get(from)?.term || !cardById.get(to)?.term) continue
    conceptSeen.add(key)
    conceptsIn.push({ from, to, relation: l.relation })
  }
  const draftSeen = new Set<string>()
  const draftsKept: DraftEdgeIn[] = []
  for (const e of draftEdgesIn) {
    const key = `${e.info.draftId}>${e.from}>${e.to}>${e.info.rel}>${e.info.state}`
    if (draftSeen.has(key)) continue
    draftSeen.add(key)
    const real = REAL_RELATION[e.info.rel]
    // 薄い提案の線だけ、既にある本物の線と同じ関係なら引かない（二重に重ねない）
    if (e.info.state === 'thin' && real && conceptSeen.has(`${e.from}>${e.to}>${real}`)) continue
    draftsKept.push(e)
  }
  // 段を作る線（一種・一部）と、段を作らない線
  const hier: { lower: string; upper: string }[] = []
  const side: { a: string; b: string }[] = []
  for (const c of conceptsIn) {
    if (c.relation === 'isPartOf') hier.push({ lower: c.from, upper: c.to })
    else side.push({ a: c.from, b: c.to })
  }
  for (const e of draftsKept) {
    if (e.info.state === 'retarget' || e.info.rel === 'quantity') continue
    if (e.info.rel === 'sub' || e.info.rel === 'part') hier.push({ lower: e.from, upper: e.to })
    else side.push({ a: e.from, b: e.to })
  }

  // ── 星を札の中の行に（1 行に星 1 つ: 星・名前・件数。札はその分だけ高く・必要なら広く） ──
  const hungOf = new Map<string, KindNode[]>()
  for (const sid of starOrder) {
    const tid = hungBy.get(sid)
    if (!tid) continue
    hungOf.set(tid, [...(hungOf.get(tid) ?? []), stars.get(sid) as KindNode])
  }
  for (const c of cards) {
    const ks = hungOf.get(c.id) ?? []
    c.hung = ks
    if (ks.length === 0) continue
    let rowW = 0
    for (const k of ks) {
      k.card = c.id
      k.r = rowStarRadius(k.count)
      rowW = Math.max(rowW, 2 * k.r + 6 + textWidth(k.label, 12) + 6 + countTextW(k.count))
    }
    c.w = Math.min(cardMax, Math.max(c.w, Math.ceil(rowW + 2 * CARD_PAD)))
    c.colW = c.w
    c.h = c.baseH + STAR_ROW_PAD + ks.length * STAR_ROW_H
    for (const k of ks) k.rowW = c.w - 2 * CARD_PAD - 2 * k.r - 6
  }

  // ── 段 → 段の中の並び（重心） ──
  const ids = cards.map((c) => c.id)
  const layerOf = assignLayers(ids, hier, side, (id) => (cardById.get(id) as PCard).colW, avail)
  const chain = chainOrder(ids, side)
  const rank = new Map(chain.map((id, i) => [id, i]))
  const nLayers = ids.length ? Math.max(...ids.map((i) => layerOf.get(i) as number)) + 1 : 0
  const layers: string[][] = Array.from({ length: nLayers }, () => [])
  for (const id of chain) layers[layerOf.get(id) as number].push(id)
  const uppersOf = new Map<string, string[]>()
  const lowersOf = new Map<string, string[]>()
  for (const h of hier) {
    if (!cardById.has(h.lower) || !cardById.has(h.upper) || h.lower === h.upper) continue
    uppersOf.set(h.lower, [...(uppersOf.get(h.lower) ?? []), h.upper])
    lowersOf.set(h.upper, [...(lowersOf.get(h.upper) ?? []), h.lower])
  }
  const fracOf = (): Map<string, number> => {
    const m = new Map<string, number>()
    for (const l of layers) l.forEach((id, i) => m.set(id, (i + 0.5) / l.length))
    return m
  }
  // 一種・一部の線を 1 本も持たない札は、重心では決まらない。横の線でつながる相手（重心のある札）のすぐ隣に、
  // どれにもつながらなければ段の右に、つながりの並び順（chain）で置く。
  const anyHier = new Set([...uppersOf.keys(), ...lowersOf.keys()])
  const sideAdj = new Map<string, string[]>(ids.map((i) => [i, []]))
  for (const s2 of side) {
    sideAdj.get(s2.a)?.push(s2.b)
    sideAdj.get(s2.b)?.push(s2.a)
  }
  const sortLayer = (l: number, nbrs: Map<string, string[]>): void => {
    const f = fracOf()
    const keys = new Map<string, number>()
    const inLayer = new Set(layers[l])
    const anchored: string[] = []
    for (const id of layers[l]) {
      const ns = (nbrs.get(id) ?? []).filter((n) => f.has(n))
      if (ns.length) keys.set(id, mean(ns.map((n) => f.get(n) as number)))
      else if (anyHier.has(id)) keys.set(id, f.get(id) as number)
      else continue
      anchored.push(id)
    }
    const queue = [...anchored].sort((a, b) => (keys.get(a) as number) - (keys.get(b) as number) || cmp(a, b))
    for (let qi = 0; qi < queue.length; qi++) {
      const cur = queue[qi]
      for (const nb of [...(sideAdj.get(cur) ?? [])].sort((a, b) => (rank.get(a) as number) - (rank.get(b) as number))) {
        if (!inLayer.has(nb) || keys.has(nb)) continue
        keys.set(nb, (keys.get(cur) as number) + 1e-4)
        queue.push(nb)
      }
    }
    for (const id of layers[l]) if (!keys.has(id)) keys.set(id, 1 + (rank.get(id) as number) / Math.max(1, ids.length))
    layers[l] = [...layers[l]].sort(
      (a, b) => (keys.get(a) as number) - (keys.get(b) as number) || (rank.get(a) as number) - (rank.get(b) as number) || cmp(a, b),
    )
  }
  for (let it = 0; it < 4; it++) {
    for (let l = 1; l < nLayers; l++) sortLayer(l, uppersOf)
    for (let l = nLayers - 2; l >= 0; l--) sortLayer(l, lowersOf)
  }

  // ── 段を行にする（幅を超えたら折り返す） ──
  // 隣り合う札のあいだ: 横の線（小さな札が入る）でつながるなら広げる
  const sidePill = new Map<string, number>()
  const noteSide = (a: string, b: string, pw: number): void => {
    const k = a < b ? `${a}|${b}` : `${b}|${a}`
    sidePill.set(k, Math.max(sidePill.get(k) ?? 0, pw))
  }
  for (const c of conceptsIn) if (c.relation !== 'isPartOf') noteSide(c.from, c.to, conceptPillW(c.relation, lang))
  for (const e of draftsKept) {
    if (e.info.rel === 'sub' || e.info.rel === 'part' || e.info.state === 'retarget') continue
    noteSide(e.from, e.to, draftPillW(e.info, lang))
  }
  const gapBetween = (a: string, b: string): number => {
    const pw = sidePill.get(a < b ? `${a}|${b}` : `${b}|${a}`)
    return pw === undefined ? CARD_GAP : CARD_GAP + pw + 16
  }
  interface Row {
    ids: string[]
    layer: number
    gaps: number[]
    width: number
  }
  const rows: Row[] = []
  layers.forEach((l, li) => {
    let cur: Row | null = null
    for (const id of l) {
      const c = cardById.get(id) as PCard
      if (cur) {
        const g = gapBetween(cur.ids[cur.ids.length - 1], id)
        if (cur.width + g + c.colW <= avail + 1e-9) {
          cur.gaps.push(g)
          cur.ids.push(id)
          cur.width += g + c.colW
          continue
        }
      }
      cur = { ids: [id], layer: li, gaps: [], width: c.colW }
      rows.push(cur)
    }
  })
  rows.forEach((r, ri) =>
    r.ids.forEach((id, ci) => {
      const c = cardById.get(id) as PCard
      c.row = ri
      c.col = ci
      c.layer = r.layer
    }),
  )
  // x: まず中央にそろえ、親（下の段は上の段・上の段は下の段）の中心に寄せて詰める
  const packRow = (r: Row, want: (id: string) => number | undefined): void => {
    const ws = r.ids.map((id) => (cardById.get(id) as PCard).colW)
    const ds = r.ids.map((id, i) => want(id) ?? (cardById.get(id) as PCard).x ?? i)
    const xs = packCenters(ds, ws, r.gaps, PAD, W - PAD)
    r.ids.forEach((id, i) => {
      ;(cardById.get(id) as PCard).x = xs[i]
    })
  }
  for (const r of rows) {
    const start = PAD + (avail - r.width) / 2
    let x = start
    r.ids.forEach((id, i) => {
      const c = cardById.get(id) as PCard
      c.x = x + c.colW / 2
      x += c.colW + (r.gaps[i] ?? 0)
    })
  }
  const around = (map: Map<string, string[]>) => (id: string): number | undefined => {
    const ns = (map.get(id) ?? []).map((n) => cardById.get(n)?.x).filter((v): v is number => v !== undefined)
    return ns.length ? mean(ns) : undefined
  }
  for (let it = 0; it < 4; it++) {
    for (let ri = 0; ri < rows.length; ri++) packRow(rows[ri], around(uppersOf))
    for (let ri = rows.length - 1; ri >= 0; ri--) packRow(rows[ri], around(lowersOf))
  }
  for (let ri = 0; ri < rows.length; ri++) packRow(rows[ri], around(uppersOf))

  // ── y: 行ごとに札の中心・星の吊り ──
  interface RowY {
    cy: number
    maxH: number
    top: number
    bottom: number
  }
  const rowY: RowY[] = []
  let y = TOP
  for (const r of rows) {
    const cs = r.ids.map((id) => cardById.get(id) as PCard)
    const maxH = Math.max(...cs.map((c) => c.h))
    const cy = y + maxH / 2
    for (const c of cs) c.cy = cy
    rowY.push({ cy, maxH, top: y, bottom: y + maxH })
    y += maxH + CHANNEL
  }
  const conceptBottom = rows.length ? rowY[rowY.length - 1].bottom : 0

  // ── 星の置き場所（札の中の行: 左の余白に星、その右に名前と件数） ──
  const nodes = new Map<string, SkyNode>()
  const clusters: SkyCluster[] = []
  const edges: SkyEdge[] = []
  const hungIds = new Set<string>()
  for (const c of cards) {
    const top = c.cy - c.h / 2
    c.hung.forEach((k, i) => {
      k.x = c.x - c.w / 2 + CARD_PAD + k.r
      k.y = top + c.baseH + STAR_ROW_PAD + STAR_ROW_H * (i + 0.5)
      hungIds.add(k.id)
    })
  }

  // ── 結ばれていない星 → 最下段「名前のない星」（データセットごとに左から。幅を超えたら折り返す） ──
  const lonely = starOrder.filter((id) => !hungIds.has(id)).map((id) => stars.get(id) as KindNode)
  let end = rows.length ? conceptBottom : 0
  let bottomHeadY = 0
  if (lonely.length > 0) {
    bottomHeadY = rows.length ? conceptBottom + 28 : TOP - 6
    const slotOf = (k: KindNode): number => Math.min(96, Math.max(STAR_SLOT_WIDE, Math.ceil(textWidth(k.label, 12)) + 4))
    interface Chunk {
      d: SkyDataset
      ks: KindNode[]
      w: number
      first: boolean
    }
    const chunks: Chunk[] = []
    for (const d of allDatasets) {
      const ks = lonely.filter((k) => k.datasetId === d.id)
      if (ks.length === 0) continue
      let cur: KindNode[] = []
      let w = 0
      let first = true
      const flush = (): void => {
        if (cur.length === 0) return
        const head = first ? Math.min(avail, textWidth(d.label, 12) + 12) : 0
        chunks.push({ d, ks: cur, w: Math.max(head, w), first })
        first = false
        cur = []
        w = 0
      }
      for (const k of ks) {
        const s = slotOf(k)
        if (cur.length > 0 && w + s > avail) flush()
        cur.push(k)
        w += s
      }
      flush()
    }
    // 行に詰める（続きの塊は新しい行から）
    const lines: Chunk[][] = []
    let cursor = PAD
    for (const ch of chunks) {
      const last = lines[lines.length - 1]
      if (!last || !ch.first || cursor + ch.w > W - PAD + 1e-9) {
        lines.push([ch])
        cursor = PAD + ch.w + 20
      } else {
        last.push(ch)
        cursor += ch.w + 20
      }
    }
    let ly = bottomHeadY + HEAD_H
    for (const line of lines) {
      const rMax = Math.max(...line.flatMap((c) => c.ks.map((k) => k.r)))
      let x0 = PAD
      for (const ch of line) {
        let x = x0
        for (const k of ch.ks) {
          const s = slotOf(k)
          k.x = x + s / 2
          k.y = ly + HEAD_H + rMax
          x += s
        }
        if (ch.first) {
          const h = HEAD_H + 2 * rMax + 3 + STAR_LABEL_H
          clusters.push({
            datasetId: ch.d.id,
            label: ch.d.label,
            cx: x0 + ch.w / 2,
            cy: ly + h / 2,
            w: ch.w,
            h,
            labelMaxW: ch.w,
            nodeIds: lonely.filter((k) => k.datasetId === ch.d.id).map((k) => k.id),
          })
        }
        x0 += ch.w + 20
      }
      ly += HEAD_H + 2 * rMax + 3 + STAR_LABEL_H + 8
    }
    end = ly - 8
  }

  // ── 視点（ハブ）: 最下段の右に小さな札 ──
  const starsByIri = new Map<string, string[]>()
  for (const id of starOrder) {
    const k = stars.get(id) as KindNode
    starsByIri.set(k.iri, [...(starsByIri.get(k.iri) ?? []), id])
  }
  const hubNodes: HubNode[] = []
  if (sky.hubs.length > 0) {
    const hy0 = end + (end > 0 ? 18 : TOP)
    let x = W - PAD
    let hy = hy0
    for (const h of sky.hubs) {
      const w = Math.max(44, Math.ceil(textWidth(h.label, 11)) + 14)
      if (x - w < PAD && x < W - PAD) {
        x = W - PAD
        hy += 44
      }
      const ids = [...new Set(h.members.flatMap((iri) => starsByIri.get(iri) ?? []))]
      hubNodes.push({ id: hubNodeId(h.id), type: 'hub', x: x - w / 2, y: hy + 11, label: h.label, hubId: h.id, memberIds: ids, w, lane: 0 })
      x -= w
    }
    end = hy + 11 + 14 + 14
  }

  // ── 札・星の node ──
  for (const c of cards) {
    if (c.term) {
      const t = c.term
      const rels = relOfMember.get(c.id) as Map<string, string>
      const node: TermNode = {
        id: c.id,
        type: 'term',
        x: c.x,
        y: c.cy,
        label: c.label,
        iri: t.iri,
        termKind: t.kind,
        w: c.w,
        h: c.h,
        memberIds: memberIdsOf.get(c.id) as string[],
        stdId: c.stdIri ? `s|${c.stdIri}` : null,
        ...(c.stdText ? { stdLabel: c.stdText, stdY: c.cy - c.h / 2 + 30 } : {}),
        lane: c.row,
      }
      nodes.set(c.id, node)
      for (const sid of node.memberIds) {
        const k = stars.get(sid) as KindNode
        edges.push({ kind: 'member', from: sid, to: c.id, eq: isEq(rels.get(sid) ?? 'subClassOf') })
        void k
      }
    } else if (c.draft) {
      const { d, c: dc, count } = c.draft
      const node: DraftNode = {
        id: c.id,
        type: 'draft',
        x: c.x,
        y: c.cy,
        label: c.label,
        draftId: d.draft_id,
        themeLabel: d.theme_label,
        conceptKey: dc.key,
        state: dc.state ?? 'thin',
        proposed: dc.proposed === true,
        termKind: dc.kind,
        conceptCount: count,
        w: c.w,
        h: c.h,
        lane: c.row,
      }
      nodes.set(c.id, node)
    }
  }
  for (const id of starOrder) nodes.set(id, stars.get(id) as KindNode)
  for (const h of hubNodes) {
    nodes.set(h.id, h)
    for (const sid of h.memberIds) edges.push({ kind: 'hub', from: sid, to: h.id })
  }

  // ── 線 ──
  for (const c of conceptsIn) edges.push({ kind: 'concept', from: c.from, to: c.to, relation: c.relation })
  for (const e of draftsKept) edges.push({ kind: 'draft', from: e.from, to: e.to, draft: e.info })

  // ── 線の道すじ ──
  const rowOfCard = (c: PCard): RowY => rowY[c.row]
  const box = (c: PCard): Rect => ({ l: c.x - c.w / 2 - 2, r: c.x + c.w / 2 + 2, t: c.cy - c.h / 2 - 2, b: c.cy + c.h / 2 + 2 })
  /** 印（提案・外す提案）の字の場所（札の左上の外: 左の縁から 4px・上の縁の上 15px）。線も小さな札もここを通らない。 */
  const markBox = (c: PCard): Rect | null =>
    c.markW > 0 ? { l: c.x - c.w / 2 + 2, r: c.x - c.w / 2 + 6 + c.markW, t: c.cy - c.h / 2 - 15, b: c.cy - c.h / 2 } : null
  /** 札の上の縁から線を出す x（印の右 6px から・縁の 10px 内側）。 */
  const exitX = (v: number, c: PCard): number => {
    const r = c.x + c.w / 2 - 10
    const l = Math.min(r, c.x - c.w / 2 + (c.markW > 0 ? c.markW + 12 : 10))
    return Math.min(r, Math.max(l, v))
  }
  /** 札の下の縁に線を入れる x（縁の 10px 内側）。 */
  const entryX = (v: number, c: PCard): number => Math.min(c.x + c.w / 2 - 10, Math.max(c.x - c.w / 2 + 10, v))
  interface Obstacle {
    owner: PCard | null
    q: Rect
  }
  const obstacles: Obstacle[] = []
  for (const c of cards) {
    obstacles.push({ owner: c, q: box(c) })
    const m = markBox(c)
    if (m) obstacles.push({ owner: c, q: m })
  }
  /** 線（折れ線）が、両端の札（本体と印）以外の札・印を貫かないか。両端の印は出口の選び方で避ける。 */
  const clear = (pts: Pt[], a: PCard, b: PCard): boolean =>
    !obstacles.some((o) => o.owner !== a && o.owner !== b && pts.slice(1).some((p, i) => segHitsRect(pts[i], p, o.q)))
  /** 札の縁の上で、点 `p` に一番近い所（1px 外）。 */
  const nearestEdge = (c: PCard, p: Pt): Pt => {
    const l = c.x - c.w / 2
    const r = c.x + c.w / 2
    const t = c.cy - c.h / 2
    const b = c.cy + c.h / 2
    const x = Math.min(r, Math.max(l, p.x))
    const y = Math.min(b, Math.max(t, p.y))
    const d = [x - l, r - x, y - t, b - y]
    const i = d.indexOf(Math.min(...d))
    return i === 0 ? { x: l - 1, y } : i === 1 ? { x: r + 1, y } : i === 2 ? { x, y: t - 1 } : { x, y: b + 1 }
  }

  // 星 → 札: 札の中の行にある星は線を引かない（札の中にある）。別の札の中の星から来る線は、札の外へ出てから相手の近い縁へ
  for (const e of edges) {
    if (e.kind !== 'member') continue
    const k = stars.get(e.from) as KindNode
    const c = cardById.get(e.to) as PCard
    const host = k.card ? cardById.get(k.card) : undefined
    if (!host || host === c) continue
    const toward = c.x >= host.x
    const start = { x: k.x + (toward ? k.r + 2 : -k.r - 2), y: k.y }
    const out = { x: toward ? host.x + host.w / 2 + 12 : host.x - host.w / 2 - 12, y: k.y }
    const endPt = nearestEdge(c, out)
    const cands: Pt[][] = [[start, out, endPt], [start, endPt]]
    e.geom = geomOf(cands.find((pts) => clear(pts, host, c)) ?? cands[0])
  }

  /** 間の行の札の隙間（左右の余白も）の中心 x。どの間の行の札からも 6px 以上離れた x だけ。`near` に近い順。 */
  const gapXs = (between: readonly number[], near: number): number[] => {
    const xs = new Set<number>()
    for (const ri of between) {
      const cs = rows[ri].ids.map((id) => cardById.get(id) as PCard).sort((p, q) => p.x - q.x)
      let prev = PAD + 4
      for (const c of cs) {
        const l = c.x - c.w / 2
        if (l - prev >= 12) xs.add((prev + l) / 2)
        prev = c.x + c.w / 2
      }
      if (W - PAD - 4 - prev >= 12) xs.add((prev + W - PAD - 4) / 2)
    }
    const ok = (x: number): boolean =>
      between.every((ri) => rows[ri].ids.every((id) => {
        const c = cardById.get(id) as PCard
        return x <= c.x - c.w / 2 - 6 || x >= c.x + c.w / 2 + 6
      }))
    return [...xs].filter(ok).sort((p, q) => Math.abs(p - near) - Math.abs(q - near) || p - q)
  }

  const route = (a: PCard, b: PCard): { pts: Pt[]; at: Pt[] } => {
    if (a.row === b.row) {
      const left = a.x < b.x ? a : b
      const right = left === a ? b : a
      if (Math.abs(a.col - b.col) === 1) {
        // 隣の札: 縁から縁へ水平の線
        const p1 = { x: left.x + left.w / 2, y: left.cy }
        const p2 = { x: right.x - right.w / 2, y: right.cy }
        const mid = { x: (p1.x + p2.x) / 2, y: p1.y }
        return { pts: left === a ? [p1, p2] : [p2, p1], at: [mid] }
      }
      // 隣でない同じ段の札: 段の上を直角に 2 回折れてまたぐ（離れるほど高く。前の段の下から 8px は空ける）
      const span = Math.abs(a.col - b.col)
      const rowTop = rowOfCard(a).top
      const prevBottom = a.row > 0 ? rowY[a.row - 1].bottom : 0
      const lift = Math.max(prevBottom + 8, rowTop - (LIFT_BASE + LIFT_STEP * Math.min(4, span - 1)))
      const xa = exitX(a.x + (b.x > a.x ? a.w / 4 : -a.w / 4), a)
      const xb = exitX(b.x + (a.x > b.x ? b.w / 4 : -b.w / 4), b)
      const pa = { x: xa, y: a.cy - a.h / 2 }
      const pb = { x: xb, y: b.cy - b.h / 2 }
      const pts = [pa, { x: xa, y: lift }, { x: xb, y: lift }, pb]
      return {
        pts,
        at: [{ x: (xa + xb) / 2, y: lift }, { x: xa + (xb - xa) / 3, y: lift }, { x: xa + (2 * (xb - xa)) / 3, y: lift }],
      }
    }
    const reversed = a.row > b.row
    const up = reversed ? b : a
    const lo2 = reversed ? a : b
    const upBottom = up.cy + up.h / 2
    const loTop = lo2.cy - lo2.h / 2
    const ax = entryX(lo2.x, up)
    const bx = exitX(up.x, lo2)
    const yb = loTop - 26
    const midY = (upBottom + loTop) / 2
    const cands: Pt[][] = []
    const bulges: Pt[][] = []
    if (Math.abs(ax - bx) < 2) {
      const x = (ax + bx) / 2
      cands.push([{ x, y: upBottom }, { x, y: loTop }])
      // まっすぐ降ろすと間の札を貫くなら、横にふくらませる
      for (const d of [48, -48, 96, -96]) bulges.push([{ x, y: upBottom }, { x: x + d, y: midY }, { x, y: loTop }])
    } else {
      const vFirst = [{ x: ax, y: upBottom }, { x: ax, y: yb }, { x: bx, y: loTop }] // 上の札から垂直に降り、下の札へ斜め
      const diag = [{ x: ax, y: upBottom }, { x: bx, y: loTop }]
      const vLast = [{ x: ax, y: upBottom }, { x: bx, y: upBottom + 26 }, { x: bx, y: loTop }] // 斜めに降り、下の札へ垂直
      // 下の札に印があって線が左から来るなら、斜めの線が印の字に掛かる。垂直に入る道を先に
      cands.push(...(lo2.markW > 0 && ax < bx ? [vLast, vFirst, diag] : [vFirst, diag, vLast]))
      for (const d of [48, -48, 96, -96]) bulges.push([{ x: ax, y: upBottom }, { x: (ax + bx) / 2 + d, y: midY }, { x: bx, y: loTop }])
    }
    // 2 つ以上の段をまたぐ線は、間の段の札の隙間を直角に通る道も試す（上の札の下の段のあいだ → 隙間 → 下の札の上の段のあいだ）
    if (lo2.row - up.row >= 2) {
      const between: number[] = []
      for (let ri = up.row + 1; ri < lo2.row; ri++) between.push(ri)
      // 横に走る所は、上の札の行（一番高い札）の下と、下の札の行の上（段のあいだの端）
      const y1 = rowY[up.row].bottom + 12
      const y2 = rowY[lo2.row].top - 12
      for (const gx of gapXs(between, (ax + bx) / 2).slice(0, 4)) {
        cands.push(dedupe([{ x: ax, y: upBottom }, { x: ax, y: y1 }, { x: gx, y: y1 }, { x: gx, y: y2 }, { x: bx, y: y2 }, { x: bx, y: loTop }]))
      }
    }
    cands.push(...bulges)
    const pts = cands.find((c) => clear(c, up, lo2)) ?? cands[0]
    // 小さな札は、下の札の手前（段のあいだ）から、線に沿って上へずらしながら置き場所を探す。最後に上の札の手前
    const at: Pt[] = []
    let total = 0
    for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y)
    for (const d of [22, 40, 58, 76, 94, 112, total - 22, total - 40]) {
      if (d < 10 || d > total - 10) continue
      const p = alongFromEnd(pts, d)
      if (p) at.push(p)
    }
    return { pts: reversed ? [...pts].reverse() : pts, at }
  }
  const placedPills: Rect[] = []
  const pillObstacles: Rect[] = [
    ...cards.map(box),
    ...cards.map(markBox).filter((b): b is Rect => b !== null),
    ...[...stars.values()].filter((k) => !k.card).map((k) => ({ l: k.x - k.r - 2, r: k.x + k.r + 2, t: k.y - k.r - 2, b: k.y + k.r + 2 })),
  ]
  for (const e of edges) {
    if (e.kind !== 'concept' && e.kind !== 'draft') continue
    const a = cardById.get(e.from)
    const b = cardById.get(e.to)
    if (!a || !b) continue
    const r = route(a, b)
    const pw = e.kind === 'draft' ? draftPillW(e.draft as DraftEdgeInfo, lang) : conceptPillW(e.relation, lang)
    let pill: Pt | null = null
    for (const p of r.at) {
      const q: Rect = { l: p.x - pw / 2, r: p.x + pw / 2, t: p.y - PILL_H / 2, b: p.y + PILL_H / 2 }
      const clashes = (o: Rect): boolean => q.l < o.r + 2 && q.r > o.l - 2 && q.t < o.b + 2 && q.b > o.t - 2
      if (q.l < 0 || q.r > W) continue
      if (placedPills.some(clashes) || pillObstacles.some(clashes)) continue
      placedPills.push(q)
      pill = p
      break
    }
    e.geom = geomOf(r.pts, pill ?? undefined)
    e.pill = pill
    e.pillW = pw
  }

  const height = Math.max(opts.minHeight ?? GRAPH_HEIGHT_MIN, Math.ceil(end + BOTTOM))
  void bottomHeadY
  return { width: W, height, mode: 'stars', bands: bands(bottomHeadY), nodes, clusters, edges }
}

/** 帯は使わない。`midTop` に「名前のない星」の見出しの y を入れて、描く側が見出しを置く。 */
function bands(headY: number): SkyLayout['bands'] {
  return { namesLanes: 0, namesLabelY: 0, midTop: headY, midBot: 0, hubTop: 0, hubLanes: 0, stdLanes: 0, horizonY: 0, stdLabelY: 0 }
}
