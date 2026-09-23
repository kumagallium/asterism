import type { GraphEdge, GraphNode, GraphNodeKind, GraphSpec } from './viewSpec'

/** Mermaid flowchart のテキスト ⇄ GraphSpec。
 *
 *  mermaid パッケージは import しない — mermaid 11 の flowchart DB は内部 API で、
 *  vitest の node 環境（jsdom 無し）では DOM 依存で読み込めない（引き継ぎ書
 *  Step 3.3 からの逸脱・本体が把握済み）。ここは自前の決定論パーサで、受け付ける
 *  構文は仕様の部分集合だけ。部分集合の外は行ごと捨てて `dropped` に積む。 */

export interface ParseResult {
  graph: GraphSpec
  dropped: string[]
}

const HEADER_RE = /^(?:graph|flowchart)\s+(LR|TD|RL|BT|LTR|RTL)\s*;?$/i
const SUBGRAPH_RE = /^subgraph\s+(.+)$/i
const END_RE = /^end$/i
const CLASSDEF_RE = /^classDef\s+\S+\s+.+$/i
const CLASS_RE = /^class\s+(\S+)\s+(\S+)$/i

/** 節ひとつ。`id`／`id[label]`／`id(label)`／`id([label])`／`id{label}`／
 *  `id[[label]]`／`id((label))`。ラベルの alt は先に具体的な形（二重括弧・
 *  スタジアム）を試し、あとから単純な形を試す — でないと `[[` が `[` に化ける。
 *  末尾の `:::class` は任意。 */
const NODE_RE =
  /^([A-Za-z0-9_-]+)(?:\[\[([\s\S]*?)\]\]|\(\[([\s\S]*?)\]\)|\(\(([\s\S]*?)\)\)|\[([\s\S]*?)\]|\(([\s\S]*?)\)|\{([\s\S]*?)\})?(?::::([A-Za-z0-9_-]+))?$/

/** 辺の区切り。長い／具体的な形を先に試す（`-->\|text\|` を `-->` より先に）。 */
const ARROW_RE = /(-->\|[^|]*\||--\s+[\s\S]+?\s+-->|-\.->|==>|-->|---)/

function classKind(name: string): GraphNodeKind {
  if (name === 'entity') return 'entity'
  if (name === 'activity') return 'activity'
  return 'other'
}

/** `"..."` の外側の引用符を外し、`toMermaidFlowchart` が書いた `#quot;` を
 *  `"` に戻す（往復のため）。 */
function unquoteLabel(raw: string): string {
  let s = raw.trim()
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) s = s.slice(1, -1)
  return s.replace(/#quot;/g, '"')
}

function pickLabel(m: RegExpMatchArray): string | undefined {
  // 2=subroutine 3=stadium 4=circle 5=rect 6=round 7=rhombus
  const raw = m[2] ?? m[3] ?? m[4] ?? m[5] ?? m[6] ?? m[7]
  return raw === undefined ? undefined : unquoteLabel(raw)
}

interface NodeToken {
  id: string
  label?: string
  className?: string
}

function parseNodeToken(tok: string): NodeToken | null {
  const m = tok.trim().match(NODE_RE)
  if (!m) return null
  return { id: m[1], label: pickLabel(m), className: m[8] }
}

/** 辺の区切り文字列から、あれば付いているラベルを読む。 */
function arrowLabel(tok: string): string | undefined {
  const pipe = tok.match(/^-->\|([^|]*)\|$/)
  if (pipe) return unquoteLabel(pipe[1])
  const inline = tok.match(/^--\s+([\s\S]+?)\s+-->$/)
  if (inline) return unquoteLabel(inline[1])
  return undefined
}

/** `"..."` の中身と `[...]` `(...)` `{...}` `([...])` `[[...]]` `((...))` の
 *  中身を、行を辺の区切りで分割する前にプレースホルダへ退避する。ラベルの中に
 *  `-->` のような矢印そのものの並びがあっても、それは節の一部であって辺の区切り
 *  ではない — 先に隠しておかないと `ARROW_RE` がラベルの中身まで割ってしまう。 */
const SPAN_RE = /"[^"]*"|\[\[[\s\S]*?\]\]|\(\[[\s\S]*?\]\)|\(\([\s\S]*?\)\)|\[[\s\S]*?\]|\([\s\S]*?\)|\{[\s\S]*?\}/g
// プレースホルダの囲みは Unicode 私用領域（制御文字ではない＝lint の
// no-control-regex に触れない）。矢印記号（- > = . |）を含まないので、
// 辺の区切り正規表現には決して一致しない。
const PLACEHOLDER_MARK = ''
const PLACEHOLDER_RE = /(\d+)/g

function protectSpans(line: string): { masked: string; restore: (s: string) => string } {
  const spans: string[] = []
  const masked = line.replace(SPAN_RE, (m) => {
    spans.push(m)
    return `${PLACEHOLDER_MARK}${spans.length - 1}${PLACEHOLDER_MARK}`
  })
  const restore = (s: string): string => s.replace(PLACEHOLDER_RE, (_, i: string) => spans[Number(i)])
  return { masked, restore }
}

/** 1 行を「節だけ」または「A --> B --> C のような鎖」として読む。読めなければ
 *  false を返す（呼び側が行ごと `dropped` へ積む）。部分的な成立は許さない —
 *  1 セグメントでも読めない鎖は行ごと捨てる。 */
function tryParseFlow(
  line: string,
  onNode: (tok: NodeToken) => void,
  onEdge: (from: string, to: string, label: string | undefined) => void,
): boolean {
  const { masked, restore } = protectSpans(line)
  const parts = masked.split(ARROW_RE).map((s) => restore(s.trim()))
  if (parts.length === 1) {
    const tok = parseNodeToken(parts[0])
    if (!tok) return false
    onNode(tok)
    return true
  }
  // 偶数インデックスが節、奇数インデックスが矢印。全セグメントが読めて初めて確定する。
  const nodeToks: NodeToken[] = []
  for (let i = 0; i < parts.length; i += 2) {
    const tok = parseNodeToken(parts[i])
    if (!tok) return false
    nodeToks.push(tok)
  }
  for (const tok of nodeToks) onNode(tok)
  for (let i = 0; i < nodeToks.length - 1; i++) {
    const label = arrowLabel(parts[i * 2 + 1])
    onEdge(nodeToks[i].id, nodeToks[i + 1].id, label)
  }
  return true
}

export function parseMermaidFlowchart(text: string): ParseResult {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')

  let direction: 'LR' | 'TD' = 'LR'
  let sawHeader = false
  const nodeOrder: string[] = []
  const nodeRecs = new Map<string, { label?: string; kind?: GraphNodeKind; group?: string }>()
  const edges: GraphEdge[] = []
  const dropped: string[] = []
  const groupStack: string[] = []

  const ensure = (id: string) => {
    let rec = nodeRecs.get(id)
    if (!rec) {
      rec = {}
      nodeRecs.set(id, rec)
      nodeOrder.push(id)
    }
    return rec
  }

  for (const raw of lines) {
    const line = raw.trim()
    if (line === '' || line.startsWith('%%')) continue

    if (!sawHeader) {
      const header = line.match(HEADER_RE)
      if (header) {
        sawHeader = true
        let d = header[1].toUpperCase()
        if (d === 'RL' || d === 'LTR') d = 'LR'
        if (d === 'BT' || d === 'RTL') d = d === 'BT' ? 'TD' : 'LR'
        direction = (d === 'TD' ? 'TD' : 'LR') as 'LR' | 'TD'
        continue
      }
    }

    const sub = line.match(SUBGRAPH_RE)
    if (sub) {
      groupStack.push(unquoteLabel(sub[1]))
      continue
    }
    if (END_RE.test(line)) {
      if (groupStack.length) groupStack.pop()
      continue
    }
    if (CLASSDEF_RE.test(line)) continue // 色は読んで捨てる
    const cls = line.match(CLASS_RE)
    if (cls) {
      const kind = classKind(cls[2])
      for (const id of cls[1].split(',').map((s) => s.trim()).filter(Boolean)) {
        ensure(id).kind = kind
      }
      continue
    }

    const group = groupStack[groupStack.length - 1]
    const ok = tryParseFlow(
      line,
      (tok) => {
        const rec = ensure(tok.id)
        if (tok.label !== undefined) rec.label = tok.label
        if (tok.className) rec.kind = classKind(tok.className)
        if (group !== undefined) rec.group = group
      },
      (from, to, label) => {
        edges.push({ from, to, label })
      },
    )
    if (!ok) dropped.push(raw)
  }

  const nodes: GraphNode[] = nodeOrder.map((id) => {
    const rec = nodeRecs.get(id)!
    const node: GraphNode = { id, label: rec.label ?? id, kind: rec.kind ?? 'entity' }
    if (rec.group !== undefined) node.props = { group: rec.group }
    return node
  })

  return { graph: { direction, nodes, edges }, dropped }
}

/** GraphSpec → Mermaid flowchart テキスト。往復（`parse(toMermaid(g)).graph` が
 *  `direction`・節の `id`/`label`/`kind`・辺と等しい）だけを保証する — サブグラフ
 *  などの `props` は書き出さない（読み手はそれで十分、契約メモ §3）。 */
export function toMermaidFlowchart(graph: GraphSpec): string {
  const lines: string[] = [`graph ${graph.direction ?? 'LR'}`]
  for (const n of graph.nodes) {
    const label = n.label.replace(/"/g, '#quot;')
    lines.push(`  ${n.id}["${label}"]:::${n.kind}`)
  }
  for (const e of graph.edges) {
    if (e.label) {
      const label = e.label.replace(/"/g, '#quot;')
      lines.push(`  ${e.from} -->|${label}| ${e.to}`)
    } else {
      lines.push(`  ${e.from} --> ${e.to}`)
    }
  }
  return lines.join('\n')
}
