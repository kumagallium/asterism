import { describe, expect, it } from 'vitest'
import type { KindNode, TermNode } from '../../sky/skyLayout'
import { kindId, termId } from '../../sky/skyLayout'
import { DRAFTS, THERMO, tIri } from './graphFixture'
import { geomPoints, layoutGraph } from './graphLayout'
import { EDGE_HIT, hitEdge, hitNode, hitStdLine } from './hit'

const l = layoutGraph(THERMO, { width: 1280, lang: 'ja', drafts: DRAFTS })
const term = (slug: string): TermNode => l.nodes.get(termId(tIri(slug))) as TermNode

describe('点の当たり判定', () => {
  it('札の中は札・星の上は星・何も無い所は null', () => {
    const t = term('paper')
    expect(hitNode(l, t.x, t.y)?.id).toBe(t.id)
    expect(hitNode(l, t.x + t.w / 2 + 2, t.y)?.id).toBe(t.id)
    const s = l.nodes.get(kindId('sd', 'https://example.org/g#sd/Paper')) as KindNode
    expect(hitNode(l, s.x, s.y)?.id).toBe(s.id)
    expect(hitNode(l, 3, 3)).toBeNull()
  })
  it('札の枠の中は、すぐ下の星より札を優先する', () => {
    const t = term('thermal')
    expect(hitNode(l, t.x, t.y + t.h / 2 - 1)?.id).toBe(t.id)
  })
  it('札の中の標準の語の 1 行（下半分）だけ標準の語。名前の行は札', () => {
    const t = term('thermal')
    expect(hitStdLine(l, t.x, t.y + 10)?.id).toBe(t.id)
    expect(hitStdLine(l, t.x, t.y - 8)).toBeNull()
    const p = term('paper')
    expect(hitStdLine(l, p.x, p.y + 5)).toBeNull() // 標準の語を持たない札
  })
})

describe('線の当たり判定', () => {
  it('線から 7px 以内なら押せる。遠ければ null', () => {
    const e = l.edges.find((x) => x.kind === 'concept' && x.relation === 'isPartOf')!
    const pts = geomPoints(e.geom!)
    const a = pts[0]
    const b = pts[1]
    const mx = (a.x + b.x) / 2
    const my = (a.y + b.y) / 2
    // 道すじに近い点（小さな札から離れた所）
    const px = a.x + (b.x - a.x) * 0.2
    const py = a.y + (b.y - a.y) * 0.2
    expect(hitEdge(l, px + EDGE_HIT - 1, py)).not.toBeNull()
    expect(hitEdge(l, mx + 400, my + 400)).toBeNull()
  })
  it('小さな札の上でも押せる', () => {
    const e = l.edges.find((x) => x.kind === 'draft' && x.pill)!
    expect(hitEdge(l, e.pill!.x, e.pill!.y)).not.toBeNull()
  })
})
