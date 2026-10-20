import { describe, expect, it } from 'vitest'
import { makeSky } from './skyFixture'
import { kindId, layoutSky, termId } from './skyLayout'
import { readSuggestions, suggestionKey, type Suggestion } from './suggestApi'
import { irisOfKeys, overlayEdges, refusedText, suggestBatchId, withoutKeys } from './suggestModel'

const sug = (over: Partial<Suggestion>): Suggestion => ({
  subject: 's',
  subject_label: 's',
  term: 't',
  term_label: 't',
  relation: 'equivalentClass',
  dataset_id: 'd',
  dataset_label: 'd',
  version: 'v1',
  at: '',
  ...over,
})

describe('結べそうな線（純関数）', () => {
  it('星図に重ねるのは、両端が図にある 星 → 用語の札 だけ（同じ組は 1 本）', () => {
    const sky = makeSky(3, 10)
    const layout = layoutSky(sky, { width: 960, lang: 'ja', mode: 'stars' })
    const term = sky.terms[0]
    const kind = sky.datasets[0].kinds[0]
    const rows = [
      sug({ subject: kind.iri, dataset_id: sky.datasets[0].id, term: term.iri }),
      sug({ subject: kind.iri, dataset_id: sky.datasets[0].id, term: term.iri, relation: 'subClassOf' }), // 同じ組
      sug({ subject: kind.iri, dataset_id: sky.datasets[0].id, term: 'https://nowhere/x' }), // 用語が図に無い
      sug({ subject: 'https://nowhere/Item', dataset_id: sky.datasets[0].id, term: term.iri }), // 星が無い（項目）
    ]
    expect(overlayEdges(layout, rows)).toEqual([
      { from: kindId(sky.datasets[0].id, kind.iri), to: termId(term.iri) },
    ])
    // 丸モードでは星が無いので何も重ねない（一覧には残る）
    const circles = layoutSky(sky, { width: 960, lang: 'ja', mode: 'circles' })
    expect(overlayEdges(circles, rows)).toEqual([])
  })

  it('まとめて確定の ID は時刻から作り、1 行・短い', () => {
    const id = suggestBatchId(new Date('2026-10-20T03:04:05Z'))
    expect(id).toBe('suggest-20261020T030405')
    expect(id.length).toBeLessThanOrEqual(100)
  })

  it('返事をした候補を一覧から外し、輪を出す点は重複なく集める', () => {
    const a = sug({ subject: 'a', term: 'x' })
    const b = sug({ subject: 'b', term: 'x' })
    expect(withoutKeys([a, b], [a]).map((s) => s.subject)).toEqual(['b'])
    expect(irisOfKeys([a, b])).toEqual(['a', 'x', 'b'])
    expect(suggestionKey(a)).not.toBe(suggestionKey({ ...a, relation: 'subClassOf' }))
  })

  it('返りの形の悪い行は落とし、欠けた名前は IRI で埋める', () => {
    expect(readSuggestions(null)).toEqual([])
    expect(readSuggestions({ suggestions: [{ subject: 's' }, null, { subject: 's', term: 't', relation: 'equivalentClass' }] })).toEqual([
      sug({ subject_label: 's', term_label: 't', dataset_id: '', dataset_label: '', version: '' }),
    ])
  })

  it('断られた理由は 1 行に数える（もう候補に無いものは断りではない）', () => {
    const tt = (k: string, o?: Record<string, unknown>) => (o ? `${k}:${JSON.stringify(o)}` : k)
    expect(refusedText([{ index: 0, reason: 'not_found' }], tt)).toBe('')
    expect(
      refusedText(
        [
          { index: 0, reason: 'cycle' },
          { index: 1, reason: 'mixed' },
          { index: 2, reason: 'cycle' },
          { index: 3, reason: 'not_found' },
        ],
        tt,
      ),
    ).toBe('sky:suggest.refused:{"n":3,"why":"sky:suggest.refusedWhy.cycle 2 / sky:suggest.refusedWhy.mixed 1"}')
  })
})
