import { describe, expect, it } from 'vitest'
import { answerOf, evidenceList, normalizePoint, undoneOf } from './chatApi'

describe('論文の根拠の文（12c）', () => {
  const e = { iri: 'https://ex.org/s/1', text: '根拠', full: '根拠の全文', section: 'Results', path: '2', dataset_id: 'p1', dataset_label: '論文X' }
  it('evidenceList は形の合うものだけ通す', () => {
    expect(evidenceList([e, { iri: 'x' }, 'str', { ...e, text: '' }])).toEqual([e])
    expect(evidenceList(undefined)).toEqual([])
  })
  it('normalizePoint: 文の evidence を通す（無い文には付けない）・paper の点を受ける', () => {
    const base = { text: 't', relation: 'r', source: 's', target: 'o', drafted_by: 'ai', at: '', batch: '', reason: '', session_id: null, turn_id: null }
    const p = normalizePoint({ iri: 'paper:p1', kind: 'paper', label: '論文X', sentences: [{ ...base, evidence: [e] }, base] })
    expect(p?.kind).toBe('paper')
    expect(p?.sentences[0].evidence).toEqual([e])
    expect(p?.sentences[1].evidence).toBeUndefined()
  })
  it('answerOf: quote のある出典だけ quote・section を持つ', () => {
    const a = answerOf({
      text: 'x',
      citations: [
        { iri: 'https://ex.org/s/1', label: '論文X', dataset_id: 'p1', dataset_label: '論文X', quote: '根拠', section: 'Results' },
        { iri: 'k', label: 'K', dataset_id: '', dataset_label: '' },
      ],
    })
    expect(a?.citations[0]).toMatchObject({ quote: '根拠', section: 'Results' })
    expect(a?.citations[1]).not.toHaveProperty('quote')
  })
})

describe('取り消しの根拠の数（13a）', () => {
  it('undone.evidence は 0 のとき無く、1 以上なら数が入る', () => {
    const base = { lines: 0, terms: 0, questions: 0, kept: [] }
    expect(undoneOf({ ...base, evidence: 2 })?.evidence).toBe(2)
    expect(undoneOf({ ...base, evidence: 0 })).not.toHaveProperty('evidence')
    expect(undoneOf(base)).not.toHaveProperty('evidence')
  })
})
