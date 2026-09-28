import type { GraphSpec } from './viewSpec'

/** GraphSpec の決定論レイアウト。`../shapeGraph.ts` の `layout()` と同じ流儀
 *  （入次数から段を決め、同じ段の中は登場順）だが、`direction: 'LR'` のときは
 *  段を**横**に並べる（x が段、y が段内の順）。`shapeGraph.ts` は Shape 型
 *  （tone 付き）を要求するので直接は呼ばず、ここに段の計算を自前で持つ
 *  （`shapeGraph.ts` は未変更のまま — 既存テストに影響しない）。 */

export interface LayoutOpts {
  nodeWidth?: number
  nodeHeight?: number
  gap?: number
}

export interface LayoutResult {
  positions: Map<string, { x: number; y: number }>
  width: number
  height: number
}

const DEFAULT_NODE_W = 168
const DEFAULT_NODE_H = 46
const DEFAULT_GAP = 48

/** 入次数から段（0 始まり）を決める。循環は**登場順で打ち切る**（それ以上
 *  辿らず深さ 0 扱い）——`shapeGraph.layout` の depth 計算と同じ規則。 */
function computeStages(ids: string[], edges: { from: string; to: string }[]): Map<string, number> {
  const known = new Set(ids)
  const incoming = new Map<string, string[]>()
  for (const id of ids) incoming.set(id, [])
  for (const e of edges) {
    if (known.has(e.to) && known.has(e.from)) incoming.get(e.to)?.push(e.from)
  }
  const stage = new Map<string, number>()
  const visiting = new Set<string>()
  const stageOf = (id: string): number => {
    const cached = stage.get(id)
    if (cached !== undefined) return cached
    if (visiting.has(id)) return 0 // 循環: これ以上たどらない
    visiting.add(id)
    const preds = incoming.get(id) ?? []
    const d = preds.length === 0 ? 0 : Math.max(...preds.map(stageOf)) + 1
    stage.set(id, d)
    return d
  }
  for (const id of ids) stageOf(id)
  return stage
}

export function layoutGraph(graph: GraphSpec, opts: LayoutOpts = {}): LayoutResult {
  const nodeW = opts.nodeWidth ?? DEFAULT_NODE_W
  const nodeH = opts.nodeHeight ?? DEFAULT_NODE_H
  const gap = opts.gap ?? DEFAULT_GAP
  const direction = graph.direction ?? 'LR'

  const ids = graph.nodes.map((n) => n.id)
  const stage = computeStages(ids, graph.edges)

  // 同じ段の中は登場順（決定論 — 同じ入力は毎回同じ絵）。
  const byStage = new Map<number, string[]>()
  for (const id of ids) {
    const s = stage.get(id) ?? 0
    const row = byStage.get(s) ?? []
    row.push(id)
    byStage.set(s, row)
  }
  const stages = [...byStage.keys()].sort((a, b) => a - b)
  const widest = Math.max(1, ...stages.map((s) => (byStage.get(s) ?? []).length))

  const positions = new Map<string, { x: number; y: number }>()

  if (direction === 'LR') {
    // x = 段、y = 段内の順（縦方向は段ごとに中央揃え）。
    const crossSpan = widest * nodeH + (widest - 1) * gap
    let x = 0
    for (const s of stages) {
      const row = byStage.get(s) ?? []
      const rowSpan = row.length * nodeH + (row.length - 1) * gap
      const top = Math.max(0, (crossSpan - rowSpan) / 2)
      row.forEach((id, i) => positions.set(id, { x, y: top + i * (nodeH + gap) }))
      x += nodeW + gap
    }
  } else {
    // TD: y = 段、x = 段内の順（横方向は段ごとに中央揃え）。
    const crossSpan = widest * nodeW + (widest - 1) * gap
    let y = 0
    for (const s of stages) {
      const row = byStage.get(s) ?? []
      const rowSpan = row.length * nodeW + (row.length - 1) * gap
      const left = Math.max(0, (crossSpan - rowSpan) / 2)
      row.forEach((id, i) => positions.set(id, { x: left + i * (nodeW + gap), y }))
      y += nodeH + gap
    }
  }

  const xs = [...positions.values()].map((p) => p.x)
  const ys = [...positions.values()].map((p) => p.y)
  const width = xs.length ? Math.max(...xs) + nodeW : 0
  const height = ys.length ? Math.max(...ys) + nodeH : 0

  return { positions, width, height }
}
