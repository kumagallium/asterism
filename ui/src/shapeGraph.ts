import type { MappingSkeleton, SkeletonMap } from './api'
import type { DatasetRules, RuleMap, RuleProperty } from './galleryApi'

/** かんたん層の「形」の図の**データ**。描画（React Flow）とは切り離してある —
 *  骨格から組むのか取り込みルールから組むのかは④と⑤で違うのに、絵の作り方は
 *  同じであってほしい（④で予告した形が⑤で実線になる、が図の意味なので、2 つの
 *  図が別々の作り方をしていると同じ形に見える保証が無い）。
 *
 *  mermaid 版（`skeletonMermaid` / `rulesMermaid`）は詳細モードに残っている。
 *  あちらは幅いっぱいの静止画で、こちらは触れる図。 */

/** 箱の性格。色ではなく**役割**で持つ（配色は CSS が決める）。 */
export type ShapeTone =
  /** ファイル全体で 1 件のもの。 */
  | 'whole'
  /** 1 件ずつできるもの（表の本体から出る種類）。 */
  | 'record'
  /** ある値そのものが ID になっているもの。 */
  | 'value'

/** 箱の中に並ぶ 1 項目。データセット詳細の構造図だけが持つ（かんたん層の図は
 *  形しか見せない — その段では項目はまだ決まっていない、あるいは別の画面の話）。 */
export interface ShapeField {
  /** 人が読む名前。意味が付いていればそれ、無ければ述語の局所名。 */
  name: string
  /** `xsd:double` などの型。技術情報なので控えめに出す。 */
  type?: string
  unit?: string
  /** 元ファイルの**実データの例**（1 つ）。「中身」タブは「何が入っているか」を
   *  見に来る場所で、項目名と型だけでは中身は分からない — ④「つながりを選ぶ」で
   *  実値を出したのと同じ理由（利用者要望・Phase 2）。 */
  example?: string
}

export interface ShapeNode {
  id: string
  label: string
  tone: ShapeTone
  fields?: ShapeField[]
}

export interface ShapeEdge {
  from: string
  to: string
  label?: string
  /** 「このあと機械が引きます」の予告（点線）。④だけで使う。 */
  pending?: boolean
}

export interface Shape {
  nodes: ShapeNode[]
  edges: ShapeEdge[]
}

const templateVars = (template: string | undefined): string[] =>
  [...(template ?? '').matchAll(/\{([^{}]+)\}/g)].map((m) => m[1])

/** A の ID が B のキーを**真に**含む＝A が細かいほう、B が粗いほう。
 *  `skeletonContainment.embedsKey` と同じ規則（1 つの規則、複数の読み方）。 */
function embedsKey(aVars: Set<string>, bVars: Set<string>): boolean {
  return bVars.size > 0 && bVars.size < aVars.size && [...bVars].every((v) => aVars.has(v))
}

/** ④「ID のつけかた」の形。線が引けるのは ID の入れ子だけで、種類どうしの
 *  本当のつながりは次の段で決まる — それは `pendingEdges` で点線として予告する。 */
export function skeletonShape(
  skeleton: MappingSkeleton,
  opts: {
    label: (m: SkeletonMap) => string
    tone?: (m: SkeletonMap) => ShapeTone
    /** 箱の中に並べる項目。④は設計より前なので、`rules` ではなく**画面の隣の
     *  表と同じ計算**（誰がどの列を持つかの宣言）から渡す。 */
    fields?: (m: SkeletonMap) => ShapeField[]
    edgeLabel?: string
    pendingEdges?: [string, string][]
    pendingLabel?: string
  },
): Shape {
  const nodes: ShapeNode[] = skeleton.maps.map((m) => ({
    id: m.name,
    label: opts.label(m),
    tone: opts.tone?.(m) ?? 'record',
    fields: opts.fields?.(m),
  }))
  const edges: ShapeEdge[] = []
  const drawn = new Set<string>()
  const mark = (a: string, b: string) => {
    drawn.add(`${a}\u0000${b}`)
    drawn.add(`${b}\u0000${a}`)
  }
  for (const a of skeleton.maps) {
    const aVars = new Set(templateVars(a.subject.template))
    for (const b of skeleton.maps) {
      if (a === b) continue
      if (embedsKey(aVars, new Set(templateVars(b.subject.template)))) {
        edges.push({ from: a.name, to: b.name, label: opts.edgeLabel })
        mark(a.name, b.name)
      }
    }
  }
  for (const [from, to] of opts.pendingEdges ?? []) {
    if (drawn.has(`${from}\u0000${to}`)) continue
    if (!nodes.some((n) => n.id === from) || !nodes.some((n) => n.id === to)) continue
    edges.push({ from, to, label: opts.pendingLabel, pending: true })
    mark(from, to)
  }
  return { nodes, edges }
}

/** 同じ種類の箱を 1 つに畳む（ADR kantan K62）。`groups` の各かたまりの先頭を
 *  残し、ほかのメンバーへの線は先頭へ付け替える（同じ線は 1 本・自分への線は
 *  捨てる）。項目は名前で重ねずに足す。図は「種類」を描くもので、map（ファイル
 *  ごとの読み方）を描くものではない — 同じ種類が 2 つの箱に見えると、つながって
 *  いない別物に読める（利用者指摘 2026-09-30）。⑤の骨格の図と⑥・詳細の
 *  取り込みルールの図が同じ 1 本を通る。 */
export function mergeNodes(shape: Shape, groups: readonly (readonly string[])[]): Shape {
  const lead = new Map<string, string>()
  for (const g of groups) for (const name of g.slice(1)) lead.set(name, g[0])
  if (lead.size === 0) return shape
  const to = (id: string) => lead.get(id) ?? id
  const seen = new Set<string>()
  const edges: ShapeEdge[] = []
  for (const e of shape.edges) {
    const moved = { ...e, from: to(e.from), to: to(e.to) }
    const key = [moved.from, moved.to, moved.pending ? 1 : 0].join('\u0001')
    if (moved.from === moved.to || seen.has(key)) continue
    seen.add(key)
    edges.push(moved)
  }
  const extra = new Map<string, ShapeField[]>()
  for (const n of shape.nodes) {
    const l = lead.get(n.id)
    if (l && n.fields) extra.set(l, [...(extra.get(l) ?? []), ...n.fields])
  }
  const nodes = shape.nodes
    .filter((n) => !lead.has(n.id))
    .map((n) => {
      const more = extra.get(n.id)
      if (!more || !n.fields) return n
      const names = new Set(n.fields.map((f) => f.name))
      return { ...n, fields: [...n.fields, ...more.filter((f) => !names.has(f.name))] }
    })
  return { nodes, edges }
}

/** ⑤「ためす」の形 — **保存済みの取り込みルール**そのもの。説明ではなく事実。
 *
 *  線を引くのは、ある種類の項目の**行き先が別の種類そのもの**になっていると
 *  き: join（親マップ参照）・同じ ID の作り方を指すテンプレート・同じ定数 IRI
 *  の 3 通り。値（リテラル）を書いている項目は形ではないので描かない。 */
/** 述語の局所名。`#` と `/` と `:` のどれで切れていても最後の一片を取る。 */
const localName = (iri: string): string => iri.split(/[:#/]/).pop() || iri

/** 項目 `p` の行き先が、種類 `x` の主語そのものか。
 *  api の `target_map`（つながりの検査と同じ判定）を先に見る。変換つきの主語は
 *  RML では関数になり、ここで比べられる文字列を持たない。残りの 3 通りは
 *  `target_map` を返さない古いサーバのため。 */
export function linksTo(p: RuleProperty, x: RuleMap): boolean {
  return (
    (p.target_map != null && p.target_map === x.id) ||
    (p.parent_map != null && p.parent_map === x.id) ||
    (!!p.template && p.template === x.subject.template) ||
    (!!p.constant && p.constant_is_iri === true && p.constant === x.subject.constant)
  )
}

export function rulesShape(
  rules: DatasetRules,
  opts: {
    label?: (m: RuleMap) => string
    /** 箱の中に項目も並べる（データセット詳細の構造図）。かんたん層の図は
     *  形だけなので渡さない。 */
    withFields?: boolean
    /** その項目が読んでいる列の**実データの例**。`(source, column)` で引く —
     *  同じ列名が別ファイルにあり得るので列名だけでは足りない。渡さなければ
     *  例は出ない（従来どおり）。 */
    exampleOf?: (source: string, column: string) => string | undefined
  } = {},
): Shape {
  const defaultLabel = (m: RuleMap): string => {
    const iri = (m.subject.class_iris ?? [])[0]
    const named = iri ? rules.labels?.[iri] : undefined
    return named || (m.subject.classes ?? [])[0]?.split(':').pop() || m.id
  }
  const label = opts.label ?? defaultLabel
  /* 箱の色は ④ では「人がここで決めたこと」（ID にした値・ファイル全体のカード）を
     表していた。⑤ に決めることは無く、取り込みルールからは 1 件のカードなのか
     行の種類なのかも読めない（`crystal/{No}` の No は前置きの列で、テンプレートを
     見ても行を回しているかは分からない）。**分からないことを塗り分けない** —
     ⑤ の箱はどれも同じ色にして、形だけを④と揃える。 */
  const isLink = (p: RuleProperty): RuleMap | undefined =>
    rules.maps.find((x) => linksTo(p, x))
  const nodes: ShapeNode[] = rules.maps.map((m) => ({
    id: m.id,
    label: label(m),
    tone: 'record',
    // 線になる項目は箱の中に重ねて書かない。矢印がそれを言っている。
    fields: opts.withFields
      ? m.properties
          .filter((p) => !isLink(p))
          .map((p) => ({
            name: p.label || rules.labels?.[p.predicate_iri] || localName(p.predicate),
            type: p.datatype ? localName(p.datatype) : undefined,
            unit: p.unit,
            example: p.reference
              ? opts.exampleOf?.(m.source ?? '', p.reference)
              : undefined,
          }))
      : undefined,
  }))
  const edges: ShapeEdge[] = []
  const drawn = new Set<string>()
  for (const a of rules.maps) {
    for (const p of a.properties) {
      const b = rules.maps.find((x) => x.id !== a.id && linksTo(p, x))
      if (!b || drawn.has(`${a.id}\u0000${b.id}`)) continue
      drawn.add(`${a.id}\u0000${b.id}`)
      edges.push({
        from: a.id,
        to: b.id,
        label: p.label || localName(p.predicate) || undefined,
      })
    }
  }
  /* 同じ ID の作り方と同じ種類名を持つ map は、同じ実体を作る同じ種類（K62:
     別々のファイルの受け口が 1 つの種類を共有する）。1 つの箱に畳む。 */
  const bySig = new Map<string, string[]>()
  for (const m of rules.maps) {
    if (!m.subject.template) continue
    const sig = [m.subject.template, ...(m.subject.classes ?? [])].join('\u0001')
    bySig.set(sig, [...(bySig.get(sig) ?? []), m.id])
  }
  return mergeNodes(
    { nodes, edges },
    [...bySig.values()].filter((g) => g.length > 1),
  )
}

export const NODE_W = 128
export const NODE_H = 46
/** 項目つきの箱の見出しと 1 項目の高さ。**CSS と一致していること** — 並べかたは
 *  自前の計算なので、実際の高さとずれると箱が重なる。 */
export const FIELD_H = 21
export const FIELDS_PAD = 10

const GAP_X = 22
const GAP_Y = 68

/** 箱ひとつの高さ。項目を畳んでいれば見出しだけ。 */
export function nodeHeight(node: ShapeNode, folded = false): number {
  const fields = folded ? [] : (node.fields ?? [])
  return fields.length === 0 ? NODE_H : NODE_H + FIELDS_PAD + fields.length * FIELD_H
}

/** 段ごとの箱の並び（上の段から）。座標を決める前の「どの箱がどの行か」。
 *
 *  上から下へ「細かいもの → 粗いもの」。矢印の向き（A の ID が B を含む・A が
 *  B を指す）がそのまま下向きになるので、④と⑤で同じ形なら**同じ絵**になる。
 *  循環があっても止まらないように、訪問済みの節は深さを伸ばさない。
 *
 *  ⭐**線のある箱の段は折り返さない。** 折り返すと同じ親の子が上下に積まれ、
 *  下の子へ向かう線が上の子の箱の裏を通って「親 → 子 → 孫」の鎖に読める
 *  （実機 2026-09-30: 値段の記録 → 食材名・店名 が、値段の記録 → 食材名 → 店名 に
 *  見えた）。線のない箱は、折り返しても嘘にならない — 線のある箱の下に
 *  `perRow` ずつ折り返して並べる。 */
export function rowsOf(shape: Shape, perRow = 2): string[][] {
  const per = Math.max(1, perRow)
  const known = new Set(shape.nodes.map((n) => n.id))
  // 有効な線 = 両端が箱にあり、自分自身へ戻らないもの。
  const valid = shape.edges.filter((e) => e.from !== e.to && known.has(e.from) && known.has(e.to))
  const linked = new Set<string>()
  const incoming = new Map<string, string[]>()
  for (const n of shape.nodes) incoming.set(n.id, [])
  for (const e of valid) {
    incoming.get(e.to)?.push(e.from)
    linked.add(e.from)
    linked.add(e.to)
  }

  const depth = new Map<string, number>()
  const seen = new Set<string>()
  const depthOf = (id: string): number => {
    const known = depth.get(id)
    if (known !== undefined) return known
    if (seen.has(id)) return 0 // 循環: これ以上たどらない
    seen.add(id)
    const preds = incoming.get(id) ?? []
    const d = preds.length === 0 ? 0 : Math.max(...preds.map(depthOf)) + 1
    depth.set(id, d)
    return d
  }
  for (const n of shape.nodes) if (linked.has(n.id)) depthOf(n.id)

  // 同じ段の中の並びは入力順（決定論 — 同じ設計は毎回同じ絵）。
  const byDepth = new Map<number, string[]>()
  for (const n of shape.nodes) {
    if (!linked.has(n.id)) continue
    const d = depth.get(n.id) ?? 0
    const row = byDepth.get(d) ?? []
    row.push(n.id)
    byDepth.set(d, row)
  }
  const rows: string[][] = []
  for (const d of [...byDepth.keys()].sort((a, b) => a - b)) rows.push(byDepth.get(d) ?? [])
  // 線のない箱は下に、入力順で `perRow` ずつ折り返す。
  const loose = shape.nodes.filter((n) => !linked.has(n.id)).map((n) => n.id)
  for (let i = 0; i < loose.length; i += per) rows.push(loose.slice(i, i + per))
  return rows
}

export interface LayoutOpts {
  /** 線のない箱を 1 行に横並びにする上限。細い列（かんたん層）は 2、幅のある
   *  画面は増やす。線のある箱の段には効かない（折り返さない）。 */
  perRow?: number
  /** 箱の幅。項目を並べる図は広く取る。 */
  nodeWidth?: number
  /** 箱の高さ。項目の数で変わるので呼ぶ側が決める。 */
  heightOf?: (node: ShapeNode) => number
}

/** 席 = 段をまたぐ線 1 本が、途中の段 1 つで通るところ。線は席のまん中（`x`）を、
 *  段の上端（`top`）から下端（`bottom`）までまっすぐ降りる。 */
export interface Lane {
  x: number
  top: number
  bottom: number
}

/** 線 1 本の通り道。 */
export interface Route {
  /** 出どころの段の下端。出どころの箱がその段でいちばん高い箱でなければ、線は
   *  ここまでまっすぐ降りてから曲がる — 自分の箱の下辺から曲がり始めると、隣の
   *  高い箱の裏を通る。 */
  drop?: number
  /** 途中の段の席（上の段から）。段をまたがない線は空。 */
  via: Lane[]
}

export interface Arranged {
  pos: Map<string, { x: number; y: number }>
  /** 線の通り道。`shape.edges` と同じ並び。 */
  routes: Route[]
  /** 図の大きさ。席は箱ではないので、箱の座標からは分からない。 */
  width: number
  height: number
}

/** 席の幅。名前を出さない線は下限、名前を出す線は名前が入る幅（箱の幅まで）。
 *  名前が席に入れば、途中の段の箱がどれだけ高くても、名前は箱に重ならない。 */
const SEAT_MIN = 24
const SEAT_PAD = 4

/** 線の名前を出すか。`shape.edges` と同じ並びで、出さない線は undefined。
 *  出さないのは、名前が無い・予告の線・同じ文言を既に出したとき。 */
function shownNames(shape: Shape): (string | undefined)[] {
  const said = new Set<string>()
  return shape.edges.map((e) => {
    const skip = !e.label || e.pending || said.has(e.label)
    if (e.label) said.add(e.label)
    return skip ? undefined : e.label
  })
}

/** 段組みの座標と、線の通り道。React Flow は自分で並べてくれないので、ここで決める。
 *  どの箱がどの行かは `rowsOf` が決め、ここは行ごとに中央へ寄せて積む。
 *
 *  ⭐**線は箱の裏を通らない。** 線が通ってよいのは、出どころの箱の真下（その段の
 *  下端まで）・段と段の間・席の 3 つだけ。
 *
 *  段をまたぐ線には、途中の段に席を取る。線は箱の下辺の中央から上辺の中央へ
 *  引くので、席が無いと、途中の段の箱の裏を通る（実例: 行の種類が結晶を指し、
 *  どちらも同じ文書を指す設計。行の種類 → 文書 の線が結晶の箱の裏に隠れ、線の
 *  名前も見えなかった。箱が横に並ぶ段では、まん中の箱から出た線に読めた）。
 *  席は箱と同じ行に並び、線は席を通る（`pointOnEdge`・`edgePath`）。
 *
 *  席を入れるのは、出どころと行き先をまっすぐ結んだ線にいちばん近いすき間。
 *  同じ近さなら右。箱の並びは入力順のまま変えない（決定論 — 同じ設計は毎回同じ絵）。
 *  同じすき間に席が 2 つ以上入るときは、線どうしが交わりにくい順に並べる。 */
export function arrange(shape: Shape, opts: LayoutOpts = {}): Arranged {
  const nodeW = opts.nodeWidth ?? NODE_W
  const heightOf = opts.heightOf ?? ((n: ShapeNode) => nodeHeight(n))
  const byId = new Map(shape.nodes.map((n) => [n.id, n]))
  const rows = rowsOf(shape, opts.perRow)
  const pitch = nodeW + GAP_X

  // 箱の高さがまちまちなので段の間隔は一定にできない。行ごとに積み上げる。
  const tops: number[] = []
  const talls: number[] = []
  let top = 0
  for (const row of rows) {
    const tallest = Math.max(...row.map((id) => heightOf(byId.get(id)!)))
    tops.push(top)
    talls.push(tallest)
    top += tallest + GAP_Y
  }

  /** `slot` = 席を入れるすき間（0 = 左端の箱の左、n = 右端の箱の右）。
   *  `ideal` = 出どころと行き先をまっすぐ結んだ線が、この段を通るところ。
   *  `next` = この段の次に向かうところ（行き先の箱か、次の段のすき間）。
   *  どれも、席を取る前の座標（図のまん中が 0）。 */
  interface Seat {
    edge: number
    slot: number
    ideal: number
    next: number
    width: number
  }
  const seats: Seat[][] = rows.map(() => [])
  // 席を取る前の箱の中央。席をどのすき間に入れるかは、これで決める —
  // 席を入れたあとの座標で決めると、席どうしが押し合う。
  const at = new Map<string, { row: number; x: number }>()
  rows.forEach((row, r) =>
    row.forEach((id, i) => at.set(id, { row: r, x: (i - (row.length - 1) / 2) * pitch })),
  )
  const names = shownNames(shape)
  shape.edges.forEach((e, edge) => {
    const a = at.get(e.from)
    const b = at.get(e.to)
    // 上へ戻る線（循環）は、席を取らない。
    if (!a || !b || b.row - a.row < 2) return
    const name = names[edge]
    const width = name
      ? Math.min(nodeW, Math.max(SEAT_MIN, labelSize(name).w + 2 * SEAT_PAD))
      : SEAT_MIN
    const y0 = tops[a.row] + heightOf(byId.get(e.from)!)
    const y1 = tops[b.row]
    const own: Seat[] = []
    for (let r = a.row + 1; r < b.row; r++) {
      const through = a.x + ((b.x - a.x) * (tops[r] + talls[r] / 2 - y0)) / (y1 - y0)
      // 端数の違いで順番が変わらないように丸める。
      const ideal = Math.round(through * 1000) / 1000
      // いちばん近いすき間。同じ近さなら右（右から調べて、近くなったときだけ替える）。
      const n = rows[r].length
      let slot = n
      let best = Infinity
      for (let k = n; k >= 0; k--) {
        const d = Math.abs((k - n / 2) * pitch - ideal)
        if (d < best - 1e-6) {
          best = d
          slot = k
        }
      }
      own.push({ edge, slot, ideal, next: b.x, width })
      seats[r].push(own[own.length - 1])
    }
    own.forEach((s, i) => {
      const below = own[i + 1]
      if (below) s.next = (below.slot - rows[a.row + 2 + i].length / 2) * pitch
    })
  })

  // 段ごとの並び: すき間 0 の席 → 箱 0 → すき間 1 の席 → 箱 1 → …
  interface Item {
    w: number
    id?: string
    seat?: Seat
  }
  const widthOf = (items: Item[]) =>
    items.reduce((sum, it) => sum + it.w, 0) + (items.length - 1) * GAP_X
  // 図の幅は、席の順番によらない。
  const canvasW = Math.max(
    nodeW,
    ...rows.map((row, r) =>
      widthOf([...row.map(() => ({ w: nodeW })), ...seats[r].map((s) => ({ w: s.width }))]),
    ),
  )

  const pos = new Map<string, { x: number; y: number }>()
  const routes: Route[] = shape.edges.map(() => ({ via: [] }))
  // 上の段から決める。同じすき間の席の順番は、上の段で通ったところで決まる。
  rows.forEach((row, r) => {
    /** この段の 1 つ上で、線が通ったところ（上の段の席。無ければ、出どころの箱の
     *  中央 — 席を通る線の最初の席は、出どころのすぐ下の段にある）。 */
    const above = (s: Seat): number => {
      const via = routes[s.edge].via
      const lane = via[via.length - 1]
      if (lane) return lane.x
      const p = pos.get(shape.edges[s.edge].from)
      return p ? p.x + nodeW / 2 : 0
    }
    // 線どうしが交わりにくい順: まっすぐ結んだ線が左のものから。同じなら、上の段で
    // 左を通ったものから。それも同じなら、次に左へ向かうものから。最後は入力順。
    const order = seats[r]
      .map((s) => ({ s, above: above(s) }))
      .sort(
        (p, q) =>
          p.s.slot - q.s.slot ||
          p.s.ideal - q.s.ideal ||
          p.above - q.above ||
          p.s.next - q.s.next ||
          p.s.edge - q.s.edge,
      )
    const items: Item[] = []
    for (let k = 0; k <= row.length; k++) {
      for (const { s } of order) if (s.slot === k) items.push({ w: s.width, seat: s })
      if (k < row.length) items.push({ w: nodeW, id: row[k] })
    }
    const left = (canvasW - widthOf(items)) / 2
    let before = 0
    items.forEach((it, i) => {
      // 席の無い段では `left + i * (箱の幅 + すき間)`（今までの式と同じ値になる）。
      const x = left + before + i * GAP_X
      if (it.id !== undefined) pos.set(it.id, { x, y: tops[r] })
      else if (it.seat)
        routes[it.seat.edge].via.push({
          x: x + it.w / 2,
          top: tops[r],
          bottom: tops[r] + talls[r],
        })
      before += it.w
    })
  })
  shape.edges.forEach((e, i) => {
    const p = pos.get(e.from)
    const r = rows.findIndex((row) => row.includes(e.from))
    if (p && r >= 0 && pos.has(e.to)) routes[i].drop = tops[r] + talls[r] + HANDLE_R
  })
  return { pos, routes, width: canvasW, height: Math.max(0, top - GAP_Y) }
}

/** 横に並ぶ段があるか。席も、横に並ぶものに数える。 */
export function hasSideBySide(shape: Shape, perRow = 2): boolean {
  return (
    rowsOf(shape, perRow).some((r) => r.length > 1) ||
    arrange(shape, { perRow }).routes.some((r) => r.via.length > 0)
  )
}

/** 箱の中の字の幅の見積もり（13px・太字）。全角は 13、半角は 8.5 で数える
 *  （大文字の多い英字の名前でも足りなくならない、少し大きめの見積もり）。 */
const nameWidth = (text: string): number => {
  let w = 0
  for (const ch of text) w += (ch.codePointAt(0) ?? 0) > 0xff ? 13 : 8.5
  return w
}
/** 箱の左右の余白（箱 17.6 ＋ 見出し 16 ＋ 枠 2）と、畳むボタン（すき間込みで 26）。
 *  **CSS と一致していること**（`.shape-node`・`.shape-node-head`・`.shape-node-fold`）。 */
const BOX_PAD = 36
const FOLD_W = 26

/** いちばん長い名前が **2 行に収まる**箱の幅（`lo`〜`hi` の間）。
 *
 *  細い列で箱を横に並べるとき、幅を詰めるほど字は縮まずに済むが、詰めすぎると
 *  名前が 3 行になって箱からはみ出す（実機 2026-09-30: 幅 132 で 12 字の名前）。
 *  行の折れ目で 1 字ぶん余るので、半分の幅に 1 字を足す。 */
export function boxWidthFor(shape: Shape, lo: number, hi: number): number {
  let need = lo
  for (const n of shape.nodes) {
    const fold = (n.fields ?? []).length > 0 ? FOLD_W : 0
    need = Math.max(need, Math.ceil(nameWidth(n.label) / 2) + 13 + BOX_PAD + fold)
  }
  return Math.min(hi, need)
}

/** 線 1 本ぶんの名前と、線の上の位置（0 = 出どころ、1 = 行き先）。 */
export interface EdgeLabel {
  text: string
  at: number
}

type Pt = { x: number; y: number }

const NO_ROUTE: Route = { via: [] }

/** 線を、つながった部分に分ける。段の間は曲線（点 4 つ。React Flow の bezier と
 *  同じ式）、出どころの箱の真下と席の中はまっすぐ（点 2 つ）。曲線は両端で真下を
 *  向くので、つなぎ目は折れない。まっすぐな部分が無ければ、曲線 1 つ = React Flow の
 *  既定の線。 */
function partsOf(from: Pt, to: Pt, route: Route): Pt[][] {
  const curve = (a: Pt, b: Pt): Pt[] => {
    const c = b.y >= a.y ? 0.5 * (b.y - a.y) : 0.25 * 25 * Math.sqrt(a.y - b.y)
    return [a, { x: a.x, y: a.y + c }, { x: b.x, y: b.y - c }, b]
  }
  const parts: Pt[][] = []
  let here = from
  // 上へ戻る線（循環）は、降りない。端数のぶん（1 未満）も降りない。
  if (route.drop !== undefined && to.y > from.y && route.drop > from.y + 1) {
    const foot = { x: from.x, y: Math.min(route.drop, to.y) }
    parts.push([from, foot])
    here = foot
  }
  for (const lane of route.via) {
    const top = { x: lane.x, y: lane.top }
    const bottom = { x: lane.x, y: lane.bottom }
    parts.push(curve(here, top), [top, bottom])
    here = bottom
  }
  parts.push(curve(here, to))
  return parts
}

/** 線が React Flow の既定の線と同じ形か（まっすぐな部分を持たない）。 */
export function isPlainEdge(from: Pt, to: Pt, route: Route = NO_ROUTE): boolean {
  return partsOf(from, to, route).length === 1
}

/** 線の上の点（出どころ = 箱の下辺の中央、行き先 = 上辺の中央）。名前を置く位置を
 *  描画の外で決めるので、線の形を写しておく。
 *
 *  まっすぐな部分を持つ線の位置は、縦の長さで測る — 0.5 は、出どころと行き先の
 *  まん中の高さ。持たない線では、bezier の媒介変数そのもの。 */
export function pointOnEdge(from: Pt, to: Pt, at: number, route: Route = NO_ROUTE): Pt {
  const parts = partsOf(from, to, route)
  let part = parts[0]
  let t = at
  if (parts.length > 1) {
    const spans = parts.map((p) => Math.abs(p[p.length - 1].y - p[0].y))
    let rest = Math.min(1, Math.max(0, at)) * spans.reduce((a, b) => a + b, 0)
    let i = 0
    while (i < parts.length - 1 && rest > spans[i]) rest -= spans[i++]
    part = parts[i]
    t = spans[i] > 0 ? rest / spans[i] : 0
  }
  if (part.length === 2) {
    return { x: part[0].x + (part[1].x - part[0].x) * t, y: part[0].y + (part[1].y - part[0].y) * t }
  }
  const u = 1 - t
  const b = (a: number, b1: number, c1: number, d: number) =>
    u * u * u * a + 3 * u * u * t * b1 + 3 * u * t * t * c1 + t * t * t * d
  return {
    x: b(part[0].x, part[1].x, part[2].x, part[3].x),
    y: b(part[0].y, part[1].y, part[2].y, part[3].y),
  }
}

/** 線の形（SVG の path）。`pointOnEdge` と同じ部分から組む。 */
export function edgePath(from: Pt, to: Pt, route: Route = NO_ROUTE): string {
  let d = `M${from.x},${from.y}`
  for (const p of partsOf(from, to, route)) {
    d +=
      p.length === 2
        ? ` L${p[1].x},${p[1].y}`
        : ` C${p[1].x},${p[1].y} ${p[2].x},${p[2].y} ${p[3].x},${p[3].y}`
  }
  return d
}

/** 線の名前の大きさの見積もり（字の数から）。CSS は 11px の字で、React Flow の
 *  背景の余白が左右 2・上下 4。全角は 11.2、半角は 6.5 で数える — 実機の実測
 *  （2026-09-30: 「食材の名前」59.8・「取り込んだ日時」82.1・「isPartOf」45・高さ 22）
 *  を下回らない、少し大きめの見積もり。小さく見積もると、重なっているのに
 *  重なっていないと判断する。 */
export function labelSize(text: string): { w: number; h: number } {
  let w = 0
  for (const ch of text) w += (ch.codePointAt(0) ?? 0) > 0xff ? 11.2 : 6.5
  return { w: w + 4, h: 22 }
}

/** 名前を置ける位置。まん中が先 — 重ならないかぎり、名前は線のまん中に出る。
 *  端は 0.25 と 0.75 まで。それより行き先へ寄せると、名前が矢じりを隠す
 *  （実機 2026-09-30: 0.8 で矢じりの根元が欠けた）。段の間（68）に、上の帯・
 *  下の帯の 2 つが重ならずに入る。 */
const LABEL_AT = [0.5, 0.35, 0.65, 0.25, 0.75]
/** 線の端は、箱の辺から出入り口（handle）の半分だけ外にある。 */
const HANDLE_R = 3
/** ほかの線を覆っているかを調べるときの、線 1 本の刻みと、名前のまわりに取る
 *  余裕。刻みの間をすり抜けた線も、余裕のぶんで拾う。 */
const LINE_STEPS = 48
const LINE_CLEAR = 3
/** 組み合わせを探す手数の上限。線が多い図でも描画を待たせない。 */
const LABEL_SEARCH_LIMIT = 20000

interface Rect {
  x: number
  y: number
  w: number
  h: number
}
const overlapArea = (a: Rect, b: Rect): number => {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
  return w > 0 && h > 0 ? w * h : 0
}

/** 名前を置く四角（線の上の点が中心）。テストが重なりを確かめるのにも使う。 */
export function labelRect(from: Pt, to: Pt, label: EdgeLabel, route: Route = NO_ROUTE): Rect {
  const c = pointOnEdge(from, to, label.at, route)
  const size = labelSize(label.text)
  return { x: c.x - size.w / 2, y: c.y - size.h / 2, w: size.w, h: size.h }
}

export interface EdgeOpts {
  nodeWidth?: number
  heightOf?: (node: ShapeNode) => number
  /** 線の通り道（`arrange` が返したもの）。渡さなければ、どの線も React Flow の
   *  既定の線と同じ形。 */
  routes?: Route[]
}

/** 線の両端と通り道（描かれる線と同じ点）。`shape.edges` と同じ並びで返し、
 *  端の箱が無い線は undefined。出どころは箱の下辺の中央、行き先は上辺の中央。 */
export function edgeEnds(
  shape: Shape,
  pos: Map<string, { x: number; y: number }>,
  opts: EdgeOpts = {},
): ({ from: Pt; to: Pt; route: Route } | undefined)[] {
  const nodeW = opts.nodeWidth ?? NODE_W
  const heightOf = opts.heightOf ?? ((n: ShapeNode) => nodeHeight(n))
  const byId = new Map(shape.nodes.map((n) => [n.id, n]))
  return shape.edges.map((e, i) => {
    const a = byId.get(e.from)
    const pa = pos.get(e.from)
    const pb = pos.get(e.to)
    if (!a || !pa || !pb || !byId.has(e.to)) return undefined
    return {
      from: { x: pa.x + nodeW / 2, y: pa.y + heightOf(a) + HANDLE_R },
      to: { x: pb.x + nodeW / 2, y: pb.y - HANDLE_R },
      route: opts.routes?.[i] ?? NO_ROUTE,
    }
  })
}

/** 線の名前を置く位置を決める。`shape.edges` と同じ並びで返し、名前を出さない
 *  線は undefined。
 *
 *  名前を出さないのは、名前が無い・予告の線・同じ文言を既に出したとき。同じ相手へ
 *  2 本引かれると、細い列では文字どうしが重なって両方読めなくなる（実機
 *  2026-08-29）。予告の線はそもそも名前を持たない — 点線の意味は図の下の注記が
 *  言っている。
 *
 *  位置は**組み合わせで**決める。重なり（名前どうし・名前と箱）がいちばん小さく、
 *  次に、ほかの線を覆う量がいちばん小さく（名前は自分の線だけに乗せる —
 *  親の近くは線が集まるので、そこに置いた長い名前はどの線のものか迷う）、
 *  その中でまん中からの外れがいちばん小さい組み合わせ。1 本ずつ先着で決めると、
 *  先の名前がまん中を取って、あとの名前の逃げ場（上の帯・下の帯のどちらも
 *  まん中と重なる）が無くなる。交わる 2 本の線はまん中が同じ点なので、まん中に
 *  固定もできない。 */
export function edgeLabels(
  shape: Shape,
  pos: Map<string, { x: number; y: number }>,
  opts: EdgeOpts = {},
): (EdgeLabel | undefined)[] {
  const nodeW = opts.nodeWidth ?? NODE_W
  const heightOf = opts.heightOf ?? ((n: ShapeNode) => nodeHeight(n))
  const boxes: Rect[] = []
  for (const n of shape.nodes) {
    const p = pos.get(n.id)
    if (p) boxes.push({ x: p.x, y: p.y, w: nodeW, h: heightOf(n) })
  }

  // 名前を出さない線も、覆われる側として要る。
  const ends = edgeEnds(shape, pos, opts)
  const lines = ends.map((end) =>
    end
      ? Array.from({ length: LINE_STEPS - 1 }, (_, k) =>
          pointOnEdge(end.from, end.to, (k + 1) / LINE_STEPS, end.route),
        )
      : [],
  )

  /** 名前を出す線と、その候補。相手の置き方によらず決まる費用は先に数えておく:
   *  `boxed` = 箱との重なり、`covers` = 覆ってしまうほかの線の長さ（刻みの数）。 */
  interface Want {
    index: number
    text: string
    spots: { rect: Rect; boxed: number; covers: number; off: number }[]
  }
  const wants: Want[] = []
  shownNames(shape).forEach((text, index) => {
    const end = ends[index]
    if (!text || !end) return
    wants.push({
      index,
      text,
      spots: LABEL_AT.map((at) => {
        const rect = labelRect(end.from, end.to, { text, at }, end.route)
        let covers = 0
        lines.forEach((line, other) => {
          if (other === index) return
          for (const p of line) {
            if (
              p.x > rect.x - LINE_CLEAR &&
              p.x < rect.x + rect.w + LINE_CLEAR &&
              p.y > rect.y - LINE_CLEAR &&
              p.y < rect.y + rect.h + LINE_CLEAR
            )
              covers++
          }
        })
        return {
          rect,
          boxed: boxes.reduce((sum, r) => sum + overlapArea(rect, r), 0),
          covers,
          off: Math.abs(at - 0.5),
        }
      }),
    })
  })

  // 費用は（重なりの面積, ほかの線を覆う量, まん中からの外れ）の順に比べる。
  // 名前が読めないのがいちばん悪く、次が「どの線の名前か迷う」こと。
  type Cost = [number, number, number]
  const better = (a: Cost, b: Cost) => {
    if (a[0] !== b[0]) return a[0] < b[0]
    if (a[1] !== b[1]) return a[1] < b[1]
    return a[2] < b[2] - 1e-9
  }
  const sum = (a: Cost, b: Cost): Cost => [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
  const picked: number[] = []
  /** i 本目を s に置いたときに増える費用（箱と、先に置いた名前との重なりを含む）。 */
  const added = (i: number, s: number): Cost => {
    const spot = wants[i].spots[s]
    let overlap = spot.boxed
    for (let j = 0; j < i; j++) overlap += overlapArea(spot.rect, wants[j].spots[picked[j]].rect)
    return [overlap, spot.covers, spot.off]
  }

  // まず 1 本ずつ先着で決める（手数を使い切ったときの答えにもなる）。
  let bestCost: Cost = [0, 0, 0]
  wants.forEach((_, i) => {
    let at = 0
    let cost = added(i, 0)
    for (let s = 1; s < LABEL_AT.length; s++) {
      const c = added(i, s)
      if (better(c, cost)) {
        at = s
        cost = c
      }
    }
    picked[i] = at
    bestCost = sum(bestCost, cost)
  })
  let best = [...picked]

  // そのうえで、もっと良い組み合わせを探す。費用は増えるだけなので、いまの
  // 答え以上になった枝はそこで捨てる（重なりが無い図は、ここで何も探さない）。
  let steps = 0
  const search = (i: number, cost: Cost) => {
    if (!better(cost, bestCost)) return
    if (i === wants.length) {
      best = picked.slice(0, i)
      bestCost = cost
      return
    }
    for (let s = 0; s < LABEL_AT.length; s++) {
      if (steps++ > LABEL_SEARCH_LIMIT) return
      const c = added(i, s)
      picked[i] = s
      search(i + 1, sum(cost, c))
    }
  }
  search(0, [0, 0, 0])

  const out: (EdgeLabel | undefined)[] = shape.edges.map(() => undefined)
  wants.forEach((w, i) => {
    out[w.index] = { text: w.text, at: LABEL_AT[best[i] ?? 0] }
  })
  return out
}
