import { describe, expect, it } from 'vitest'
import { SAMPLE_SKY } from './sample'
import { makeSky, SCALES } from './skyFixture'
import { layoutSky } from './skyLayout'
import { buildSkyTables } from './skyTable'

describe('静かな表示の表は図と同じ内容', () => {
  const cases: [string, ReturnType<typeof makeSky>][] = [
    ['見本', SAMPLE_SKY],
    ['S', makeSky(SCALES.S.datasets, SCALES.S.kinds)],
    ['M', makeSky(SCALES.M.datasets, SCALES.M.kinds)],
    ['L', makeSky(SCALES.L.datasets, SCALES.L.kinds)],
  ]
  it.each(cases)('%s: 星・用語・標準用語・ハブの数が図の点と同じ', (_name, sky) => {
    const t = buildSkyTables(sky)
    const l = layoutSky(sky, { width: 1000 })
    const nodes = [...l.nodes.values()]
    // 星の行は種類の数（丸モードでは図は丸だが、表は種類まで出す）
    expect(t.stars).toHaveLength(sky.datasets.reduce((s, d) => s + d.kinds.length, 0))
    if (l.mode === 'stars') expect(t.stars).toHaveLength(nodes.filter((n) => n.type === 'kind').length)
    else expect(new Set(t.stars.map((r) => r.dataset)).size).toBe(nodes.filter((n) => n.type === 'dataset').length)
    expect(t.terms).toHaveLength(nodes.filter((n) => n.type === 'term').length)
    expect(t.standards).toHaveLength(nodes.filter((n) => n.type === 'std').length)
    expect(t.hubs).toHaveLength(nodes.filter((n) => n.type === 'hub').length)
  })

  it('図の線と同じつながりが表の列に出る（用語 ↔ 種類・標準用語）', () => {
    const t = buildSkyTables(SAMPLE_SKY)
    const card = t.terms.find((r) => r.label === 'カード')!
    expect(card.kinds).toEqual(['Card（XRD 実験）', 'Card（XRD 参照カード）'])
    expect(t.terms.find((r) => r.label === '試料')!.standard).toBe('cmso:Material')
    expect(t.stars.find((r) => r.kind === '試料')!.terms).toEqual(['試料', '組成'])
    expect(t.stars.find((r) => r.kind === '物性')!.standard).toBe('qudt:QuantityKind')
    expect(t.standards.find((r) => r.label === 'qudt:Unit')!.bound).toEqual(['単位（測定曲線）'])
    expect(t.hubs[0].members).toEqual(['試料（試料）', '化学式（XRD 参照カード）'])
  })

  it('言語で用語名が変わる', () => {
    expect(buildSkyTables(SAMPLE_SKY, 'en').terms.map((r) => r.label)).toContain('Sample')
  })

  it('空は空の表', () => {
    const t = buildSkyTables({ ...SAMPLE_SKY, datasets: [], terms: [], standards: [], hubs: [] })
    expect(t).toEqual({ stars: [], terms: [], standards: [], hubs: [] })
  })
})
