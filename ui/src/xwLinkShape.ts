/** つながりの関係図の**並べかた**。描画（`XwLinkDiagram.tsx`）から切り離して
 *  あるのは、部品ファイルが部品以外を持てない（react-refresh）のと、並べかたは
 *  絵を出さずに確かめられるから。
 *
 *  つながりは**向きを持たない** — A も B も同じ値を持っている、というだけ。
 *  だから辺に矢じりは付けない。 */

export type XwSide = { key: string; name: string; field?: string; title?: string }

export type XwNode =
  | { id: string; kind: 'side'; side: XwSide; x: number; y: number; hasLeft: boolean; hasRight: boolean }
  | { id: 'hub'; kind: 'hub'; x: number; y: number }

export type XwEdge = { id: string; source: string; target: string }

export const SIDE_W = 208
export const SIDE_H = 74
export const HUB_W = 200
export const HUB_H = 74
const GAP_X = 116
const GAP_Y = 18

/** 2 つなら「A —（共通の値）— B」の横一列。3 つ以上は左に縦積みして、真ん中の
 *  1 つに集める（横一列のままでは画面に収まらず、以前は「ラベルを上に出して
 *  ノードを下に並べる」形に崩していた — 集める形なら同じ絵のまま増やせる）。 */
export function xwLinkLayout(sides: XwSide[]): {
  nodes: XwNode[]
  edges: XwEdge[]
  height: number
} {
  const nodes: XwNode[] = []
  const edges: XwEdge[] = []
  const join = (source: string, target: string) =>
    edges.push({ id: `${source}-${target}`, source, target })

  if (sides.length === 0) return { nodes, edges, height: SIDE_H + 56 }

  if (sides.length === 2) {
    nodes.push({ id: `s:${sides[0].key}`, kind: 'side', side: sides[0], x: 0, y: 0, hasLeft: false, hasRight: true })
    nodes.push({ id: 'hub', kind: 'hub', x: SIDE_W + GAP_X, y: 0 })
    nodes.push({
      id: `s:${sides[1].key}`,
      kind: 'side',
      side: sides[1],
      x: SIDE_W + GAP_X + HUB_W + GAP_X,
      y: 0,
      hasLeft: true,
      hasRight: false,
    })
    join(`s:${sides[0].key}`, 'hub')
    join('hub', `s:${sides[1].key}`)
    return { nodes, edges, height: SIDE_H + 56 }
  }

  sides.forEach((s, i) => {
    nodes.push({
      id: `s:${s.key}`,
      kind: 'side',
      side: s,
      x: 0,
      y: i * (SIDE_H + GAP_Y),
      hasLeft: false,
      hasRight: true,
    })
  })
  const stackH = sides.length * SIDE_H + (sides.length - 1) * GAP_Y
  nodes.push({ id: 'hub', kind: 'hub', x: SIDE_W + GAP_X, y: (stackH - HUB_H) / 2 })
  for (const s of sides) join(`s:${s.key}`, 'hub')
  return { nodes, edges, height: stackH + 56 }
}
