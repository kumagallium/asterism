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

/** ⑤「ためす」の形 — **保存済みの取り込みルール**そのもの。説明ではなく事実。
 *
 *  線を引くのは、ある種類の項目の**行き先が別の種類そのもの**になっていると
 *  き: join（親マップ参照）・同じ ID の作り方を指すテンプレート・同じ定数 IRI
 *  の 3 通り。値（リテラル）を書いている項目は形ではないので描かない。 */
/** 述語の局所名。`#` と `/` と `:` のどれで切れていても最後の一片を取る。 */
const localName = (iri: string): string => iri.split(/[:#/]/).pop() || iri

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
    rules.maps.find(
      (x) =>
        (p.parent_map != null && p.parent_map === x.id) ||
        (!!p.template && p.template === x.subject.template) ||
        (!!p.constant && p.constant_is_iri === true && p.constant === x.subject.constant),
    )
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
      const b = rules.maps.find(
        (x) =>
          x.id !== a.id &&
          ((p.parent_map != null && p.parent_map === x.id) ||
            (!!p.template && p.template === x.subject.template) ||
            (!!p.constant && p.constant_is_iri === true && p.constant === x.subject.constant)),
      )
      if (!b || drawn.has(`${a.id}\u0000${b.id}`)) continue
      drawn.add(`${a.id}\u0000${b.id}`)
      edges.push({
        from: a.id,
        to: b.id,
        label: p.label || localName(p.predicate) || undefined,
      })
    }
  }
  return { nodes, edges }
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

/** 段組みの座標。React Flow は自分で並べてくれないので、ここで決める。
 *  どの箱がどの行かは `rowsOf` が決め、ここは行ごとに中央へ寄せて積むだけ。 */
export function layout(
  shape: Shape,
  opts: {
    /** 線のない箱を 1 行に横並びにする上限。細い列（かんたん層）は 2、幅のある
     *  画面は増やす。線のある箱の段には効かない（折り返さない）。 */
    perRow?: number
    /** 箱の幅。項目を並べる図は広く取る。 */
    nodeWidth?: number
    /** 箱の高さ。項目の数で変わるので呼ぶ側が決める。 */
    heightOf?: (node: ShapeNode) => number
  } = {},
): Map<string, { x: number; y: number }> {
  const nodeW = opts.nodeWidth ?? NODE_W
  const heightOf = opts.heightOf ?? ((n: ShapeNode) => nodeHeight(n))
  const byId = new Map(shape.nodes.map((n) => [n.id, n]))
  const rows = rowsOf(shape, opts.perRow)
  const widest = Math.max(1, ...rows.map((r) => r.length))
  const canvasW = widest * nodeW + (widest - 1) * GAP_X
  const pos = new Map<string, { x: number; y: number }>()
  // 箱の高さがまちまちなので段の間隔は一定にできない。行ごとに積み上げる。
  let top = 0
  for (const row of rows) {
    const rowW = row.length * nodeW + (row.length - 1) * GAP_X
    const left = (canvasW - rowW) / 2
    row.forEach((id, i) => {
      pos.set(id, { x: left + i * (nodeW + GAP_X), y: top })
    })
    const tallest = Math.max(...row.map((id) => heightOf(byId.get(id)!)))
    top += tallest + GAP_Y
  }
  return pos
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

/** 線の上の点。React Flow の bezier（出どころ = 箱の下辺の中央、行き先 = 上辺の
 *  中央）と同じ式 — 名前を置く位置を描画の外で決めるので、線の形を写しておく。 */
export function pointOnEdge(
  from: { x: number; y: number },
  to: { x: number; y: number },
  at: number,
): { x: number; y: number } {
  const c = to.y >= from.y ? 0.5 * (to.y - from.y) : 0.25 * 25 * Math.sqrt(from.y - to.y)
  const p1 = { x: from.x, y: from.y + c }
  const p2 = { x: to.x, y: to.y - c }
  const u = 1 - at
  const b = (a: number, b1: number, c1: number, d: number) =>
    u * u * u * a + 3 * u * u * at * b1 + 3 * u * at * at * c1 + at * at * at * d
  return { x: b(from.x, p1.x, p2.x, to.x), y: b(from.y, p1.y, p2.y, to.y) }
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
export function labelRect(
  from: { x: number; y: number },
  to: { x: number; y: number },
  label: EdgeLabel,
): Rect {
  const c = pointOnEdge(from, to, label.at)
  const size = labelSize(label.text)
  return { x: c.x - size.w / 2, y: c.y - size.h / 2, w: size.w, h: size.h }
}

/** 線の両端（描かれる線と同じ点）。`shape.edges` と同じ並びで返し、端の箱が
 *  無い線は undefined。出どころは箱の下辺の中央、行き先は上辺の中央。 */
export function edgeEnds(
  shape: Shape,
  pos: Map<string, { x: number; y: number }>,
  opts: { nodeWidth?: number; heightOf?: (node: ShapeNode) => number } = {},
): ({ from: { x: number; y: number }; to: { x: number; y: number } } | undefined)[] {
  const nodeW = opts.nodeWidth ?? NODE_W
  const heightOf = opts.heightOf ?? ((n: ShapeNode) => nodeHeight(n))
  const byId = new Map(shape.nodes.map((n) => [n.id, n]))
  return shape.edges.map((e) => {
    const a = byId.get(e.from)
    const pa = pos.get(e.from)
    const pb = pos.get(e.to)
    if (!a || !pa || !pb || !byId.has(e.to)) return undefined
    return {
      from: { x: pa.x + nodeW / 2, y: pa.y + heightOf(a) + HANDLE_R },
      to: { x: pb.x + nodeW / 2, y: pb.y - HANDLE_R },
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
  opts: { nodeWidth?: number; heightOf?: (node: ShapeNode) => number } = {},
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
          pointOnEdge(end.from, end.to, (k + 1) / LINE_STEPS),
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
  const said = new Set<string>()
  shape.edges.forEach((e, index) => {
    const skip = !e.label || e.pending || said.has(e.label)
    if (e.label) said.add(e.label)
    const end = ends[index]
    if (skip || !e.label || !end) return
    const text = e.label
    wants.push({
      index,
      text,
      spots: LABEL_AT.map((at) => {
        const rect = labelRect(end.from, end.to, { text, at })
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
