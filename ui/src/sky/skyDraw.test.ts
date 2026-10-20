import { describe, expect, it } from 'vitest'
import { SAMPLE_SKY } from './sample'
import { EMPTY_SKY } from './skyApi'
import {
  BORN_RING_MS,
  bornAlive,
  formatCount,
  hitTest,
  pointLabelBox,
  visiblePointLabels,
  withAlpha,
} from './skyDraw'
import { kindId, layoutSky, type KindNode, type SkyLayout, type SkyNode } from './skyLayout'

const star = (id: string, x: number, y: number, label: string, count: number, r = 8): KindNode => ({
  id,
  type: 'kind',
  x,
  y,
  label,
  iri: id,
  datasetId: 'd',
  count,
  r,
})
const layoutOf = (nodes: SkyNode[], clusters: SkyLayout['clusters'] = []): SkyLayout => ({
  width: 800,
  height: 620,
  mode: 'stars',
  bands: {
    namesLanes: 1,
    namesLabelY: 69,
    midTop: 186,
    midBot: 446,
    hubTop: 482,
    hubLanes: 0,
    stdLanes: 1,
    horizonY: 561,
    stdLabelY: 549,
  },
  nodes: new Map(nodes.map((n) => [n.id, n])),
  clusters,
  edges: [],
})

describe('星の名前を出さない判定（重なるものは出さない）', () => {
  it('名前の幅は 11px × 文字数（件数の行がある星は最低 56px）', () => {
    const b = pointLabelBox(star('a', 100, 100, '試料の種類です', 5))
    expect(b.x1 - b.x0).toBe(11 * 7)
    const short = pointLabelBox(star('b', 100, 100, 'ab', 5))
    expect(short.x1 - short.x0).toBe(56)
    // 件数 0 は件数の行が無い（箱が低い）
    expect(pointLabelBox(star('c', 100, 100, 'ab', 0)).y1 - pointLabelBox(star('c', 100, 100, 'ab', 0)).y0).toBe(14)
  })

  it('近い 2 つは、件数の多い星の名前だけが出る', () => {
    const l = layoutOf([star('big', 100, 100, 'ながい名前の星', 9000), star('small', 140, 100, 'ながい名前の星2', 10)])
    expect([...visiblePointLabels(l)]).toEqual(['big'])
  })

  it('離れていれば両方出る', () => {
    const l = layoutOf([star('a', 100, 100, '星あ', 9000), star('b', 500, 100, '星い', 10)])
    expect([...visiblePointLabels(l)].sort()).toEqual(['a', 'b'])
  })

  it('ほかの星の丸に被る名前も出さない', () => {
    // b の名前の箱が a の丸の上に乗る位置
    const l = layoutOf([star('b', 100, 100, 'bbbbbb', 9000), star('a', 100, 128, 'a', 5, 10)])
    const shown = visiblePointLabels(l)
    expect(shown.has('b')).toBe(false) // b の下に a の丸が来る＝名前を置けない
    expect(shown.has('a')).toBe(true)
  })

  it('星座の名前に被る名前も出さない', () => {
    const cluster = { datasetId: 'd', label: '星座の名前', cx: 150, cy: 150, w: 200, h: 220, labelMaxW: 200, nodeIds: ['a'] }
    // 星座の名前の箱 = x 58〜113・y 31〜44。星 a の名前の箱（丸の下）がそこに入る位置。
    expect(visiblePointLabels(layoutOf([star('a', 80, 15, 'あ', 100)], [cluster])).has('a')).toBe(false)
    // 離れていれば出る
    expect(visiblePointLabels(layoutOf([star('a', 80, 140, 'あ', 100)], [cluster])).has('a')).toBe(true)
  })

  it('決定論（同じ入力は同じ結果・件数が同じなら id 順）', () => {
    const nodes = [star('b', 100, 100, 'あいうえお', 50), star('a', 130, 100, 'あいうえお', 50)]
    const x = [...visiblePointLabels(layoutOf(nodes))]
    const y = [...visiblePointLabels(layoutOf([...nodes].reverse()))]
    expect(x).toEqual(y)
    expect(x).toEqual(['a'])
  })

  it('実際の配置でも、出す名前どうしは重ならない（見本）', () => {
    const l = layoutSky(SAMPLE_SKY, { width: 1000 })
    const shown = [...visiblePointLabels(l)].map((id) => pointLabelBox(l.nodes.get(id) as KindNode))
    for (let i = 0; i < shown.length; i++) {
      for (let j = i + 1; j < shown.length; j++) {
        const a = shown[i]
        const b = shown[j]
        expect(a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0).toBe(false)
      }
    }
    // 最も件数の多い星（測定曲線）の名前は必ず出る
    expect(visiblePointLabels(l).has(kindId('curves', 'sample:curve'))).toBe(true)
  })
})

describe('hitTest', () => {
  const l = layoutSky(SAMPLE_SKY, { width: 1000 })
  it('星の上（半径 + 8）で当たる・遠いと外れる', () => {
    const s = l.nodes.get(kindId('samples', 'sample:sample')) as KindNode
    expect(hitTest(l, s.x + s.r + 4, s.y)?.id).toBe(s.id)
    expect(hitTest(l, l.width + 100, l.height + 100)).toBeNull()
  })
  it('用語の札は矩形で当たる', () => {
    const t = [...l.nodes.values()].find((n) => n.type === 'term')!
    expect(hitTest(l, t.x, t.y)?.id).toBe(t.id)
  })
  it('標準用語・ハブにも当たる', () => {
    const s = [...l.nodes.values()].find((n) => n.type === 'std')!
    expect(hitTest(l, s.x, s.y)?.id).toBe(s.id)
    const h = [...l.nodes.values()].find((n) => n.type === 'hub')!
    expect(hitTest(l, h.x + 3, h.y + 3)?.id).toBe(h.id)
  })
  it('下書きの札も矩形で当たる', () => {
    const d = layoutSky(EMPTY_SKY, {
      width: 800,
      drafts: [
        {
          draft_id: 'x',
          theme_label: 'T',
          concepts: [{ key: 'c1', label: '熱伝導率', kind: 'property', use: 'new' }],
          relations: [],
          questions: [],
        },
      ],
    })
    const n = [...d.nodes.values()].find((x) => x.type === 'draft')!
    expect(hitTest(d, n.x, n.y)?.id).toBe(n.id)
  })
  it('何も無い所は null', () => {
    expect(hitTest(l, 1, 1)).toBeNull()
  })
})

describe('小道具', () => {
  it('件数の短い表記（日本語は万・英語は k/M）', () => {
    expect(formatCount(46200, 'ja')).toBe('4.6万')
    expect(formatCount(238000, 'ja')).toBe('24万')
    expect(formatCount(9999, 'ja')).toBe('9,999')
    expect(formatCount(46200, 'en')).toBe('46.2k')
    expect(formatCount(2_500_000, 'en')).toBe('2.5M')
    expect(formatCount(12, 'en')).toBe('12')
  })
  it('withAlpha は #rrggbb・#rgb を rgba にする（他は素通し）', () => {
    expect(withAlpha('#3a7a4c', 0.5)).toBe('rgba(58,122,76,0.5)')
    expect(withAlpha('#fff', 0.2)).toBe('rgba(255,255,255,0.2)')
    expect(withAlpha('rgb(1,2,3)', 0.2)).toBe('rgb(1,2,3)')
  })
  it('生まれた輪は 1.4 秒＋少しで終わる（1 度だけ）', () => {
    const born = new Map([['a', 1000]])
    expect(bornAlive(born, 1000)).toBe(true)
    expect(bornAlive(born, 1000 + BORN_RING_MS + 200)).toBe(false)
    expect(bornAlive(new Map(), 0)).toBe(false)
  })
})
