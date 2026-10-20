import { describe, expect, it } from 'vitest'
import { isAnswerable } from './skyApi'
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
    ['XL', makeSky(SCALES.XL.datasets, SCALES.XL.kinds)],
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
    const card = t.terms.find((r) => r.label === 'レシピ')!
    expect(card.kinds).toEqual(['レシピ（献立）', 'レシピ（買い物メモ）'])
    expect(t.terms.find((r) => r.label === '試料')!.standard).toBe('cmso:Material')
    expect(t.stars.find((r) => r.kind === '試料')!.terms).toEqual(['試料', '組成'])
    expect(t.stars.find((r) => r.kind === '物性')!.standard).toBe('qudt:QuantityKind')
    expect(t.standards.find((r) => r.label === 'qudt:Unit')!.bound).toEqual(['単位（測定曲線）'])
    expect(t.hubs[0].members).toEqual(['試料（試料）', '分量（買い物メモ）'])
  })

  it('言語で用語名が変わる', () => {
    expect(buildSkyTables(SAMPLE_SKY, 'en').terms.map((r) => r.label)).toContain('Sample')
  })

  it('空は空の表', () => {
    const t = buildSkyTables({ ...SAMPLE_SKY, datasets: [], terms: [], standards: [], hubs: [] })
    expect(t).toEqual({ stars: [], terms: [], standards: [], hubs: [], lines: [] })
  })
})

describe('知識の問いと知識の線', () => {
  const sky = makeSky(2, 6)
  const [a, b] = sky.terms

  it('知識の問いは「知識 N」つきで用語の行に出る・線は文の素（主語・目的語・関係・理由）で出る', () => {
    const t = buildSkyTables({
      ...sky,
      edges: [{ from: a.iri, to: b.iri, relation: 'lowers', reason: '散乱' }],
    })
    const row = t.terms.find((r) => r.iri === a.iri)!
    expect(row.questions).toEqual([{ title: sky.questions[0].title, mode: 'knowledge', knowledge: 2 }])
    expect(t.lines).toEqual([{ from: a.label, to: b.label, relation: 'lowers', reason: '散乱' }])
  })

  it('線の端の語が無ければ出さない', () => {
    const t = buildSkyTables({ ...sky, edges: [{ from: a.iri, to: 'urn:none', relation: 'affects', reason: null }] })
    expect(t.lines).toEqual([])
  })

  it('答えられる = データ ≥ 1 または 知識 ≥ 1', () => {
    expect(isAnswerable({ answering: 0, knowledge: 2 })).toBe(true)
    expect(isAnswerable({ answering: 1, knowledge: 0 })).toBe(true)
    expect(isAnswerable({ answering: 0, knowledge: 0 })).toBe(false)
    expect(isAnswerable({ answering: 0 })).toBe(false)
  })
})

