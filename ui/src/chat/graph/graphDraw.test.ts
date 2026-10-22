import { describe, expect, it } from 'vitest'
import type { SkyLabels } from '../../sky/skyDraw'
import { readSkyPalette } from '../../sky/skyDraw'
import { DRAFTS, THERMO } from './graphFixture'
import { drawGraph, type GraphLabels } from './graphDraw'
import { layoutGraph } from './graphLayout'

const labels: GraphLabels = {
  noName: '用語に結ばれていないデータ',
  names: '',
  mid: '',
  midCircles: '',
  std: '',
  empty: '',
  property: '項目',
  draft: '提案',
  draftRemove: '外す提案',
  partOf: '一部',
  relSub: '一種',
  relQuantity: '量',
  count: (n) => `${n} 件`,
  kinds: (n) => `${n} 種類`,
} satisfies SkyLabels & { noName: string }

/** 呼ばれた描画命令を記録する canvas の替え玉。 */
function recorder(): { ctx: CanvasRenderingContext2D; calls: string[]; texts: string[] } {
  const calls: string[] = []
  const texts: string[] = []
  const state: Record<string, unknown> = { font: '12px sans-serif' }
  const ctx = new Proxy(state, {
    get(t, k: string) {
      if (k in t) return t[k]
      if (k === 'measureText') return (s: string) => ({ width: [...s].length * 5 })
      return (...a: unknown[]) => {
        calls.push(k)
        if (k === 'fillText') texts.push(String(a[0]))
      }
    },
    set(t, k: string, v) {
      t[k] = v
      return true
    },
  }) as unknown as CanvasRenderingContext2D
  return { ctx, calls, texts }
}

describe('drawGraph', () => {
  const l = layoutGraph(THERMO, { width: 1280, lang: 'ja', drafts: DRAFTS })
  const pal = readSkyPalette()
  const st = { focusId: null, near: null, born: new Map<string, number>(), now: 0, reducedMotion: true }

  it('札・札の中の星の行（名前と件数）・最下段の見出し・標準の語の 1 行・提案の印を描く。点線は使わない', () => {
    const r = recorder()
    drawGraph(r.ctx, l, pal, labels, st, new Set())
    expect(r.texts).toEqual(expect.arrayContaining(['材料', '熱伝導率', '≡ qudt:ThermalConductivity', '用語に結ばれていないデータ', 'Starrydata', '結晶構造', '提案', '試料', '46200 件']))
    expect(r.calls).not.toContain('setLineDash')
    // 曲線は使わない
    expect(r.calls).not.toContain('quadraticCurveTo')
    expect(r.calls).not.toContain('bezierCurveTo')
  })
  it('外す提案の札は印と打ち消し線', () => {
    const r = recorder()
    const id = [...l.nodes.values()].find((n) => n.type === 'term' && n.label === '論文')!.id
    drawGraph(r.ctx, l, pal, labels, { ...st, removeNodeIds: new Set([id]) }, new Set())
    expect(r.texts).toContain('外す提案')
  })
  it('濃淡・選択・輪があっても落ちない', () => {
    const r = recorder()
    const id = [...l.nodes.keys()][0]
    drawGraph(r.ctx, l, pal, labels, { focusId: id, near: new Set([id]), born: new Map([[id, 0]]), now: 100, reducedMotion: false, alpha: 0.38 }, new Set([id]))
    expect(r.calls.length).toBeGreaterThan(50)
  })
})
