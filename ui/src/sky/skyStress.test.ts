// 規模試験（契約 pr2-contract.md §F）。種つきの作り物 S/M/L/XL で配置を回し、
//   (1) すべての点が枠内  (2) 星・丸の名前（11px × 文字数で近似）どうしが重ならない
//   (3) 帯の札が重ならない  (4) L・XL は丸モードに切り替わる
// を固定する。「重なる場合は名前を出さない」判定そのものは skyDraw.test.ts。
import { describe, expect, it } from 'vitest'
import { pointLabelBox, visiblePointLabels } from './skyDraw'
import { makeSky, SCALES } from './skyFixture'
import { layoutSky, type KindNode, type SkyLayout, type SkyNode } from './skyLayout'

const WIDTHS = [720, 1000, 1440]
const sizes = ['S', 'M', 'L', 'XL'] as const

interface Rect {
  id: string
  x0: number
  y0: number
  x1: number
  y1: number
}
const rectOf = (n: SkyNode): Rect | null => {
  if (n.type === 'term') return { id: n.id, x0: n.x - n.w / 2, x1: n.x + n.w / 2, y0: n.y - n.h / 2, y1: n.y + n.h / 2 }
  if (n.type === 'std') return { id: n.id, x0: n.x - n.w / 2, x1: n.x + n.w / 2, y0: n.y - n.h / 2, y1: n.y + n.h / 2 }
  if (n.type === 'hub') return { id: n.id, x0: n.x - n.w / 2, x1: n.x + n.w / 2, y0: n.y - 11, y1: n.y + 26 }
  return null
}
const hit = (a: Rect, b: Rect): boolean => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0

function cardsOf(l: SkyLayout, type: 'term' | 'std' | 'hub'): Rect[] {
  return [...l.nodes.values()].filter((n) => n.type === type).map((n) => rectOf(n)!)
}

describe.each(sizes)('規模 %s', (size) => {
  const { datasets, kinds } = SCALES[size]
  const sky = makeSky(datasets, kinds)

  it.each(WIDTHS)(`すべての点が枠内（幅 %i）`, (w) => {
    const l = layoutSky(sky, { width: w })
    expect(l.height).toBeGreaterThanOrEqual(620)
    for (const n of l.nodes.values()) {
      const r = n.type === 'kind' || n.type === 'dataset' ? n.r : 0
      expect(n.x - r).toBeGreaterThanOrEqual(0)
      expect(n.x + r).toBeLessThanOrEqual(l.width)
      expect(n.y - r).toBeGreaterThanOrEqual(0)
      expect(n.y + r).toBeLessThanOrEqual(l.height)
      const box = rectOf(n)
      if (box) {
        expect(box.x0).toBeGreaterThanOrEqual(0)
        expect(box.x1).toBeLessThanOrEqual(l.width)
        expect(box.y0).toBeGreaterThanOrEqual(0)
        expect(box.y1).toBeLessThanOrEqual(l.height)
      }
    }
    // 星は自分の星座の箱の中（箱は枠の中）
    for (const c of l.clusters) {
      expect(c.cx - c.w / 2).toBeGreaterThanOrEqual(0)
      expect(c.cx + c.w / 2).toBeLessThanOrEqual(l.width)
      for (const id of c.nodeIds) {
        const n = l.nodes.get(id)!
        expect(Math.abs(n.x - c.cx)).toBeLessThanOrEqual(c.w / 2 + 1e-6)
        expect(Math.abs(n.y - c.cy)).toBeLessThanOrEqual(c.h / 2 + 1e-6)
      }
    }
  })

  it.each(WIDTHS)(`帯の札（用語・標準用語・ハブ）が重ならない（幅 %i）`, (w) => {
    const l = layoutSky(sky, { width: w })
    for (const type of ['term', 'std', 'hub'] as const) {
      const cards = cardsOf(l, type)
      for (let i = 0; i < cards.length; i++) {
        for (let j = i + 1; j < cards.length; j++) {
          expect(hit(cards[i], cards[j]), `${type} ${cards[i].id} と ${cards[j].id}`).toBe(false)
        }
      }
    }
    // 帯どうし（用語 ↔ 星座 ↔ ハブ ↔ 標準用語）も縦に重ならない
    const terms = cardsOf(l, 'term')
    for (const t of terms) expect(t.y1).toBeLessThanOrEqual(l.bands.midTop)
    for (const s of cardsOf(l, 'std')) expect(s.y0).toBeGreaterThanOrEqual(l.bands.midBot)
  })

  it.each(WIDTHS)(`星・丸の名前が重ならない（幅 %i）`, (w) => {
    const l = layoutSky(sky, { width: w })
    const shown = [...visiblePointLabels(l)].map((id) => {
      const n = l.nodes.get(id) as KindNode
      return { id, ...pointLabelBox(n) }
    })
    for (let i = 0; i < shown.length; i++) {
      for (let j = i + 1; j < shown.length; j++) {
        expect(hit(shown[i], shown[j]), `${shown[i].id} と ${shown[j].id}`).toBe(false)
      }
    }
    // 出ない名前ばかりにはならない（最も大きい点の名前は必ず出る）
    expect(shown.length).toBeGreaterThan(0)
    const points = [...l.nodes.values()].filter((n): n is KindNode => n.type === 'kind' || (n.type as string) === 'dataset')
    const biggest = points.sort((a, b) => b.count - a.count || (a.id < b.id ? -1 : 1))[0]
    expect(visiblePointLabels(l).has(biggest.id)).toBe(true)
  })

  // 重ならないだけなら、名前を全部隠しても通る。出る割合の下限を固定する（実測は S 100%・M 85%）。
  // L・XL は丸モード（1 丸 1 名前）なので全部出る。
  const FLOOR: Record<(typeof sizes)[number], number> = { S: 0.8, M: 0.7, L: 0.9, XL: 0.9 }
  it.each(WIDTHS)(`名前が出る割合が下限を割らない（幅 %i）`, (w) => {
    const l = layoutSky(sky, { width: w })
    const points = [...l.nodes.values()].filter((n) => n.type === 'kind' || n.type === 'dataset')
    const ratio = visiblePointLabels(l).size / points.length
    expect(ratio).toBeGreaterThanOrEqual(FLOOR[size])
  })

  it('同じデータ・同じ幅は同じ絵', () => {
    const a = layoutSky(sky, { width: 1000 })
    const b = layoutSky(makeSky(datasets, kinds), { width: 1000 })
    expect([...a.nodes.values()].map((n) => `${n.id}@${n.x},${n.y}`)).toEqual(
      [...b.nodes.values()].map((n) => `${n.id}@${n.x},${n.y}`),
    )
  })
})

describe('段の選び方（規模で星／丸）', () => {
  it('S・M は星のまま、L・XL は丸', () => {
    expect(layoutSky(makeSky(SCALES.S.datasets, SCALES.S.kinds), { width: 1000 }).mode).toBe('stars')
    expect(layoutSky(makeSky(SCALES.M.datasets, SCALES.M.kinds), { width: 1000 }).mode).toBe('stars')
    expect(layoutSky(makeSky(SCALES.L.datasets, SCALES.L.kinds), { width: 1000 }).mode).toBe('circles')
    expect(layoutSky(makeSky(SCALES.XL.datasets, SCALES.XL.kinds), { width: 1000 }).mode).toBe('circles')
  })
  it('星のとき星はすべて違う場所にある（S・M）', () => {
    for (const s of ['S', 'M'] as const) {
      const l = layoutSky(makeSky(SCALES[s].datasets, SCALES[s].kinds), { width: 1000 })
      const seen = new Set<string>()
      for (const n of l.nodes.values()) {
        if (n.type !== 'kind') continue
        const k = `${Math.round(n.x)},${Math.round(n.y)}`
        expect(seen.has(k)).toBe(false)
        seen.add(k)
      }
    }
  })
})

describe('重い入力でも時間内に終わる', () => {
  it('XL（50 データセット・250 種類）の配置は 1 秒以内', () => {
    const sky = makeSky(SCALES.XL.datasets, SCALES.XL.kinds)
    const t0 = performance.now()
    layoutSky(sky, { width: 1440 })
    expect(performance.now() - t0).toBeLessThan(1000)
  }, 10_000)
  it('星のまま最大（8 データセット・60 種類）でも 1 秒以内・星は枠内', () => {
    const sky = makeSky(8, 60)
    const t0 = performance.now()
    const l = layoutSky(sky, { width: 1000 })
    expect(performance.now() - t0).toBeLessThan(1000)
    expect(l.mode).toBe('stars')
    for (const n of l.nodes.values()) {
      if (n.type !== 'kind') continue
      expect(n.x).toBeGreaterThanOrEqual(0)
      expect(n.x).toBeLessThanOrEqual(l.width)
    }
  }, 10_000)
  it('1 つのデータセットに種類が 60（星の上限）でも星どうしは離れている', () => {
    const l = layoutSky(makeSky(1, 60), { width: 1000 })
    expect(l.mode).toBe('stars')
    const ks = [...l.nodes.values()].filter((n): n is KindNode => n.type === 'kind')
    let min = Infinity
    for (let i = 0; i < ks.length; i++) {
      for (let j = i + 1; j < ks.length; j++) min = Math.min(min, Math.hypot(ks[i].x - ks[j].x, ks[i].y - ks[j].y))
    }
    expect(min).toBeGreaterThan(28)
  })
})
