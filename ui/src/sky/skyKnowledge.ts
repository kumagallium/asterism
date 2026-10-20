// 星図の上の帯（用語の札）を、知識の線の構造で並べる。純関数・決定論（乱数・時刻を使わない）。
//
// 考え方は②の図（`themes/conceptMapLayout.ts`・K52 の段配置）と同じ:
//   - 「一部」の先（全体）が上の段、その一部が下の段。
//   - 「下げる／上げる／変える」の原因（from）は、その結果（to）の下の段。
//   → 線 `from → to` は、いつも「from が to より下の段」。段 = 最長の道（結果を 0 とする）。
//   - 段の中は左から右へ。結果の真下に原因が来るように、重心で何度か並べ直す（交差を減らす）。
//
// 連結成分ごとに「かたまり」を作り、左から棚に詰める。札が KNOW_MAX を超える成分は、超えた分を
// 段詰め（従来の並べ方）に戻す。線は札の縁から縁へ（札・札の下の小さな字を通さない）、小さな札
// （一部・↓ ↑ ↕）は線の中ほどに置き、重なるなら線に沿ってずらし、それでも重なるなら省く。

/** 知識の段どうしの間隔（札の中心から中心）。札の下の字・線・小さな札が入る高さ。 */
export const KNOW_PITCH = 80
/** 1 つの連結成分で段に並べる札の上限。超えた分は段詰めに戻す。 */
export const KNOW_MAX = 20
/** 段の中の札どうしの間・かたまりどうしの間。 */
const GAP_X = 16
const GAP_BLOCK = 30

export interface KItem {
  id: string
  /** 置きたい x（星の重心）。並びの手がかりにだけ使う。 */
  x: number
  w: number
}
export interface KLink {
  from: string
  to: string
}

export interface KPos {
  x: number
  /** 知識の段（0 が一番上）。棚をまたいで通し番号。 */
  row: number
}

export interface KnowledgePlan {
  pos: Map<string, KPos>
  /** 段の数（知識の線でつながる札が無ければ 0）。 */
  rows: number
  /** 一番右の札の右端（x）。無ければ 0。 */
  right: number
}

const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)

/** 並びを保ったまま、希望の位置（札の中心 `ds`）にできるだけ近く、札どうしが重ならないよう詰める（Abacus 法）。左端 lo・右端 hi の内側。 */
export function packRow(ds: number[], ws: number[], gap: number, lo: number, hi: number): number[] {
  interface C {
    n: number
    q: number
    w: number
    x: number
  }
  const clamp = (c: C): void => {
    const x = c.q / c.n
    c.x = Math.max(lo, Math.min(Math.max(lo, hi - c.w), x))
  }
  const cs: C[] = []
  for (let i = 0; i < ds.length; i++) {
    const c: C = { n: 1, q: ds[i] - ws[i] / 2, w: ws[i], x: 0 }
    clamp(c)
    cs.push(c)
    while (cs.length > 1) {
      const cur = cs[cs.length - 1]
      const prev = cs[cs.length - 2]
      if (prev.x + prev.w + gap <= cur.x + 1e-9) break
      prev.q += cur.q - cur.n * (prev.w + gap)
      prev.n += cur.n
      prev.w += gap + cur.w
      clamp(prev)
      cs.pop()
    }
  }
  const out: number[] = []
  let k = 0
  for (const c of cs) {
    let x = c.x
    for (let j = 0; j < c.n; j++, k++) {
      out.push(x + ws[k] / 2)
      x += ws[k] + gap
    }
  }
  return out
}

interface Block {
  ids: string[]
  /** 札の中心の x（かたまりの左端を 0 とする）・段（かたまりの中の通し番号）。 */
  x: Map<string, number>
  row: Map<string, number>
  rows: number
  width: number
}

/** 線 `from → to` を「from が to より下」にする段（最長の道）。輪になる線は 1 本捨てて段を決める。 */
function layerOf(ids: string[], links: KLink[]): Map<string, number> {
  const order = new Map(ids.map((id, i) => [id, i]))
  const out = new Map<string, string[]>(ids.map((id) => [id, []]))
  for (const l of links) out.get(l.from)?.push(l.to)
  for (const v of out.values()) v.sort((a, b) => (order.get(a) as number) - (order.get(b) as number))
  // 深さ優先で、いま辿っている道に戻る線（輪を作る線）を捨てる
  const state = new Map<string, 0 | 1 | 2>()
  const kept = new Map<string, string[]>(ids.map((id) => [id, []]))
  const visit = (u: string): void => {
    state.set(u, 1)
    for (const v of out.get(u) ?? []) {
      if (state.get(v) === 1) continue
      kept.get(u)?.push(v)
      if (!state.has(v)) visit(v)
    }
    state.set(u, 2)
  }
  for (const id of ids) if (!state.has(id)) visit(id)
  const layer = new Map<string, number>()
  const get = (u: string): number => {
    const got = layer.get(u)
    if (got !== undefined) return got
    let r = 0
    for (const v of kept.get(u) ?? []) r = Math.max(r, get(v) + 1)
    layer.set(u, r)
    return r
  }
  for (const id of ids) get(id)
  return layer
}

function layoutBlock(items: KItem[], links: KLink[], avail: number): Block {
  const ids = items.map((i) => i.id)
  const w = new Map(items.map((i) => [i.id, i.w]))
  const hint = new Map(items.map((i) => [i.id, i.x]))
  const layer = layerOf(ids, links)
  const nLayers = Math.max(...ids.map((id) => layer.get(id) as number)) + 1
  // 段ごとの札（置きたい x の順）。1 段が幅を超えるなら、段を分ける（折り返し）
  const byLayer: string[][] = Array.from({ length: nLayers }, () => [])
  for (const id of [...ids].sort((a, b) => (hint.get(a) as number) - (hint.get(b) as number) || cmp(a, b))) {
    byLayer[layer.get(id) as number].push(id)
  }
  const rows: string[][] = []
  for (const L of byLayer) {
    let cur: string[] = []
    let curW = 0
    for (const id of L) {
      const iw = w.get(id) as number
      if (cur.length && curW + GAP_X + iw > avail) {
        rows.push(cur)
        cur = []
        curW = 0
      }
      curW += (cur.length ? GAP_X : 0) + iw
      cur.push(id)
    }
    if (cur.length) rows.push(cur)
  }
  const rowOf = new Map<string, number>()
  rows.forEach((r, i) => r.forEach((id) => rowOf.set(id, i)))
  // つながる相手（上＝結果・下＝原因）。段をまたぐ線だけ
  const up = new Map<string, string[]>(ids.map((id) => [id, []]))
  const down = new Map<string, string[]>(ids.map((id) => [id, []]))
  for (const l of links) {
    const a = rowOf.get(l.from) as number
    const b = rowOf.get(l.to) as number
    if (a === b) continue
    const [lowerId, upperId] = a > b ? [l.from, l.to] : [l.to, l.from]
    up.get(lowerId)?.push(upperId)
    down.get(upperId)?.push(lowerId)
  }
  const x = new Map<string, number>()
  for (const id of ids) x.set(id, Math.min(avail, Math.max(0, (hint.get(id) as number) - 0)))
  const place = (r: number, nb: Map<string, string[]>): void => {
    const row = rows[r]
    const want = new Map<string, number>()
    for (const id of row) {
      const ns = nb.get(id) ?? []
      want.set(id, ns.length ? ns.reduce((s, n) => s + (x.get(n) as number), 0) / ns.length : (x.get(id) as number))
    }
    const idx = new Map(row.map((id, i) => [id, i]))
    row.sort((a, b) => (want.get(a) as number) - (want.get(b) as number) || (idx.get(a) as number) - (idx.get(b) as number))
    const xs = packRow(
      row.map((id) => want.get(id) as number),
      row.map((id) => w.get(id) as number),
      GAP_X,
      0,
      avail,
    )
    row.forEach((id, i) => x.set(id, xs[i]))
  }
  place(0, up)
  for (let it = 0; it < 4; it++) {
    for (let r = 1; r < rows.length; r++) place(r, up)
    for (let r = rows.length - 2; r >= 0; r--) place(r, down)
  }
  for (let r = 1; r < rows.length; r++) place(r, up)
  // 左端を 0 にそろえる
  let minL = Infinity
  let maxR = -Infinity
  for (const id of ids) {
    minL = Math.min(minL, (x.get(id) as number) - (w.get(id) as number) / 2)
    maxR = Math.max(maxR, (x.get(id) as number) + (w.get(id) as number) / 2)
  }
  for (const id of ids) x.set(id, (x.get(id) as number) - minL)
  return { ids, x, row: rowOf, rows: rows.length, width: maxR - minL }
}

/**
 * 知識の線でつながる札を、連結成分ごとのかたまりにして段に並べる。
 * `items` は用語の札（下書きの札は含めない）。`links` は `from → to`（from が to の一部・原因）。
 * 線の無い札・成分の KNOW_MAX を超えた札は `pos` に入らない（呼び出し側が段詰めで置く）。
 */
export function planKnowledge(items: KItem[], links: KLink[], W: number, pad: number): KnowledgePlan {
  const byId = new Map(items.map((i) => [i.id, i]))
  const seen = new Set<string>()
  const ls: KLink[] = []
  for (const l of links) {
    const key = `${l.from}>${l.to}`
    if (l.from === l.to || !byId.has(l.from) || !byId.has(l.to) || seen.has(key)) continue
    seen.add(key)
    ls.push(l)
  }
  const order = [...items].sort((a, b) => a.x - b.x || cmp(a.id, b.id)).map((i) => i.id)
  const rank = new Map(order.map((id, i) => [id, i]))
  const adj = new Map<string, Set<string>>()
  for (const l of ls) {
    if (!adj.has(l.from)) adj.set(l.from, new Set())
    if (!adj.has(l.to)) adj.set(l.to, new Set())
    adj.get(l.from)?.add(l.to)
    adj.get(l.to)?.add(l.from)
  }
  const avail = Math.max(60, W - pad * 2)
  const comps: string[][] = []
  const done = new Set<string>()
  for (const s of order) {
    if (!adj.has(s) || done.has(s)) continue
    const comp: string[] = []
    const queue = [s]
    done.add(s)
    while (queue.length) {
      const u = queue.shift() as string
      comp.push(u)
      for (const v of [...(adj.get(u) ?? [])].sort((a, b) => (rank.get(a) as number) - (rank.get(b) as number))) {
        if (!done.has(v)) {
          done.add(v)
          queue.push(v)
        }
      }
    }
    comps.push(comp)
  }
  const blocks: { b: Block; first: number }[] = []
  for (const comp of comps) {
    // 札が多すぎる成分は、幅優先で最初の KNOW_MAX だけを段に並べる（残りは段詰め）
    const take = new Set(comp.slice(0, KNOW_MAX))
    const sub = [...take].sort((a, b) => (rank.get(a) as number) - (rank.get(b) as number))
    const subLinks = ls.filter((l) => take.has(l.from) && take.has(l.to))
    if (subLinks.length === 0) continue
    const linked = new Set(subLinks.flatMap((l) => [l.from, l.to]))
    const ids = sub.filter((id) => linked.has(id))
    const b = layoutBlock(
      ids.map((id) => ({ id, x: (byId.get(id) as KItem).x - pad, w: (byId.get(id) as KItem).w })),
      subLinks,
      avail,
    )
    blocks.push({ b, first: rank.get(ids[0]) as number })
  }
  // 大きいかたまりから。同じ大きさは左にあるほうから
  blocks.sort((p, q) => q.b.ids.length - p.b.ids.length || p.first - q.first)
  const pos = new Map<string, KPos>()
  let rows = 0
  let right = 0
  let i = 0
  while (i < blocks.length) {
    // 棚（左から詰める。入らなければ次の棚）
    let cursor = pad
    let shelfRows = 0
    while (i < blocks.length) {
      const { b } = blocks[i]
      if (cursor > pad && cursor + b.width > W - pad) break
      for (const id of b.ids) {
        pos.set(id, { x: cursor + (b.x.get(id) as number), row: rows + (b.row.get(id) as number) })
      }
      cursor += b.width + GAP_BLOCK
      right = Math.max(right, cursor - GAP_BLOCK)
      shelfRows = Math.max(shelfRows, b.rows)
      i++
    }
    rows += shelfRows
  }
  return { pos, rows, right }
}

// ── 線の道すじと小さな札 ───────────────────────────────────────────

export interface ConceptEdgeGeom {
  /** 始点・終点（札の縁）と、曲げの制御点。 */
  x1: number
  y1: number
  cx: number
  cy: number
  x2: number
  y2: number
  /** 線の中ほど（小さな札を置く所）。 */
  mx: number
  my: number
}

export interface RouteCard {
  id: string
  x: number
  y: number
  w: number
  h: number
  /** 札の下の小さな字の半幅（無ければ 0）。 */
  capHalf: number
  /** 札の上の小さな字（「項目」・「下書き」）の半幅（無ければ 0）。 */
  topHalf: number
}
export interface RouteEdge {
  from: string
  to: string
  /** 小さな札の幅（一部・↓ ↑ ↕）。 */
  pillW: number
}
export interface RouteOut {
  geom: ConceptEdgeGeom
  pill: { x: number; y: number } | null
}

const PILL_H = 16
const EDGE_PAD = 3
const CAP_H = 16
const TOP_H = 12

interface Rect {
  l: number
  r: number
  t: number
  b: number
  id: string
}
const hit = (a: { l: number; r: number; t: number; b: number }, c: { l: number; r: number; t: number; b: number }): boolean =>
  a.l < c.r && a.r > c.l && a.t < c.b && a.b > c.t

/** 線（2 次ベジェ）の t の点。 */
export function bezierAt(g: ConceptEdgeGeom, t: number): { x: number; y: number } {
  const u = 1 - t
  return {
    x: u * u * g.x1 + 2 * u * t * g.cx + t * t * g.x2,
    y: u * u * g.y1 + 2 * u * t * g.cy + t * t * g.y2,
  }
}

const geomOf = (x1: number, y1: number, x2: number, y2: number, bow: number): ConceptEdgeGeom => {
  const dx = x2 - x1
  const dy = y2 - y1
  const len = Math.hypot(dx, dy) || 1
  const cx = (x1 + x2) / 2 - (dy / len) * bow
  const cy = (y1 + y2) / 2 + (dx / len) * bow
  return { x1, y1, cx, cy, x2, y2, mx: 0.25 * x1 + 0.5 * cx + 0.25 * x2, my: 0.25 * y1 + 0.5 * cy + 0.25 * y2 }
}

/** 札の中心から相手へ向かう線が、札の縁（半幅 hw・半高 hh）と交わる点。 */
export function clipToCard(x: number, y: number, hw: number, hh: number, tx: number, ty: number): { x: number; y: number } {
  const dx = tx - x
  const dy = ty - y
  if (dx === 0 && dy === 0) return { x, y }
  const k = Math.min(dx === 0 ? Infinity : hw / Math.abs(dx), dy === 0 ? Infinity : hh / Math.abs(dy))
  return { x: x + dx * k, y: y + dy * k }
}

/**
 * 知識の線すべての道すじ（札の縁から縁へ）と小さな札の位置を決める。決定論。
 *  - 段をまたぐ線は、上の札の下の縁・下の札の上の縁へつなぐ。同じ縁に何本も来るときは、相手の x の順に
 *    縁の上で散らす。札の下の「まだ星がない」・上の「項目」の字は避けて縁につなぐ（札が狭くて避けられないときだけ字の上）。
 *  - 線が別の札・その小さな字を通るときは、横に膨らませて避ける（避けられなければそのまま）。
 *  - 小さな札は線の中ほど。札・字・ほかの小さな札に重なるときは線に沿ってずらし、それでも重なるなら null。
 */
export function routeConceptEdges(cards: RouteCard[], edges: RouteEdge[], W: number): RouteOut[] {
  const byId = new Map(cards.map((c) => [c.id, c]))
  const rects: Rect[] = []
  for (const c of cards) {
    rects.push({ id: c.id, l: c.x - c.w / 2 - 2, r: c.x + c.w / 2 + 2, t: c.y - c.h / 2 - 2, b: c.y + c.h / 2 + 2 })
    if (c.capHalf > 0) rects.push({ id: c.id, l: c.x - c.capHalf, r: c.x + c.capHalf, t: c.y + c.h / 2, b: c.y + c.h / 2 + CAP_H })
    if (c.topHalf > 0) rects.push({ id: c.id, l: c.x - c.topHalf, r: c.x + c.topHalf, t: c.y - c.h / 2 - TOP_H, b: c.y - c.h / 2 })
  }
  // 段をまたぐ線の縁の取り分け
  type Side = 'top' | 'bottom'
  interface Use {
    edge: number
    card: RouteCard
    side: Side
    other: RouteCard
  }
  const uses = new Map<string, Use[]>()
  const vertical = new Map<number, { aSide: Side; bSide: Side }>()
  edges.forEach((e, i) => {
    const a = byId.get(e.from)
    const b = byId.get(e.to)
    if (!a || !b) return
    if (Math.abs(a.y - b.y) <= (a.h + b.h) / 2 + 6) return
    const aUp = a.y < b.y
    vertical.set(i, { aSide: aUp ? 'bottom' : 'top', bSide: aUp ? 'top' : 'bottom' })
    const push = (card: RouteCard, side: Side, other: RouteCard): void => {
      const k = `${card.id}|${side}`
      if (!uses.has(k)) uses.set(k, [])
      uses.get(k)?.push({ edge: i, card, side, other })
    }
    push(a, aUp ? 'bottom' : 'top', b)
    push(b, aUp ? 'top' : 'bottom', a)
  })
  const portX = new Map<string, number>() // `${edge}|${card.id}` → x
  for (const list of uses.values()) {
    const { card, side } = list[0]
    const zone = side === 'bottom' ? card.capHalf : card.topHalf
    const lo = card.x - card.w / 2 + 4
    const hi = card.x + card.w / 2 - 4
    // 字から離れた縁を先に（余裕 6px）。札が狭くて取れなければ、字のすぐ外（余裕 2px）まで寄せる。
    let slots: number[] = []
    for (const margin of zone > 0 ? [6, 2] : [0]) {
      slots = []
      for (let x = lo; x <= hi + 1e-9; x += 6) if (zone <= 0 || Math.abs(x - card.x) >= zone + margin) slots.push(x)
      if (zone > 0 && Math.abs(hi - card.x) >= zone + margin && !slots.includes(hi)) slots.push(hi)
      if (slots.length > 0) break
    }
    const free = slots.length > 0 ? slots : [card.x]
    const sorted = [...list].sort((p, q) => p.other.x - q.other.x || p.edge - q.edge)
    let prev = -Infinity
    for (const u of sorted) {
      const want = Math.min(hi, Math.max(lo, u.other.x))
      const pickFrom = (min: number): number | null => {
        let best: number | null = null
        for (const s of free) {
          if (s < min) continue
          if (best === null || Math.abs(s - want) < Math.abs(best - want) - 1e-9) best = s
        }
        return best
      }
      const s = pickFrom(prev + 8) ?? pickFrom(-Infinity) ?? card.x
      portX.set(`${u.edge}|${card.id}`, s)
      prev = s
    }
  }

  const placed: Rect[] = []
  const out: RouteOut[] = []
  edges.forEach((e, i) => {
    const a = byId.get(e.from)
    const b = byId.get(e.to)
    if (!a || !b) {
      out.push({ geom: geomOf(0, 0, 0, 0, 0), pill: null })
      return
    }
    const v = vertical.get(i)
    let p1: { x: number; y: number }
    let p2: { x: number; y: number }
    if (v) {
      p1 = { x: portX.get(`${i}|${a.id}`) as number, y: a.y + (v.aSide === 'bottom' ? 1 : -1) * (a.h / 2 + EDGE_PAD) }
      p2 = { x: portX.get(`${i}|${b.id}`) as number, y: b.y + (v.bSide === 'bottom' ? 1 : -1) * (b.h / 2 + EDGE_PAD) }
    } else {
      p1 = clipToCard(a.x, a.y, a.w / 2 + EDGE_PAD, a.h / 2 + EDGE_PAD, b.x, b.y)
      p2 = clipToCard(b.x, b.y, b.w / 2 + EDGE_PAD, b.h / 2 + EDGE_PAD, a.x, a.y)
    }
    const len = Math.hypot(p2.x - p1.x, p2.y - p1.y) || 1
    const base = v ? Math.min(10, len * 0.1) : Math.min(26, len * 0.14)
    // 道すじ: 札・小さな字を通らない最小の曲げ（左右）を探す
    const crosses = (g: ConceptEdgeGeom): boolean => {
      for (let k = 1; k < 20; k++) {
        const p = bezierAt(g, k / 20)
        if (p.x < 4 || p.x > W - 4) return true
        for (const r of rects) {
          if (r.id === a.id || r.id === b.id) continue
          if (p.x > r.l && p.x < r.r && p.y > r.t && p.y < r.b) return true
        }
      }
      return false
    }
    let geom = geomOf(p1.x, p1.y, p2.x, p2.y, base)
    if (crosses(geom)) {
      const tries: number[] = []
      for (let m = 20; m <= 240; m += 20) tries.push(m, -m)
      for (const m of tries) {
        const g = geomOf(p1.x, p1.y, p2.x, p2.y, m)
        if (!crosses(g)) {
          geom = g
          break
        }
      }
    }
    // 小さな札
    let pill: { x: number; y: number } | null = null
    // 線の上（中ほどから線に沿って）→ それでも置けなければ線のすぐ横
    const side = e.pillW / 2 + 5
    const cands: { t: number; dx: number }[] = [
      ...[0.5, 0.4, 0.6, 0.32, 0.68, 0.24, 0.76].map((t) => ({ t, dx: 0 })),
      ...[0.5, 0.4, 0.6].flatMap((t) => [{ t, dx: side }, { t, dx: -side }]),
    ]
    for (const { t, dx } of cands) {
      const q0 = bezierAt(geom, t)
      const p = { x: q0.x + dx, y: q0.y }
      const r = { l: p.x - e.pillW / 2 - 1, r: p.x + e.pillW / 2 + 1, t: p.y - PILL_H / 2 - 1, b: p.y + PILL_H / 2 + 1 }
      if (p.x - e.pillW / 2 < 2 || p.x + e.pillW / 2 > W - 2) continue
      if (rects.some((q) => hit(r, q)) || placed.some((q) => hit(r, q))) continue
      pill = p
      placed.push({ ...r, id: '' })
      break
    }
    out.push({ geom, pill })
  })
  return out
}
