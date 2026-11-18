import { describe, expect, it } from 'vitest'
import { KnowledgeApiError } from './knowledgeRelationsApi'
import { ledger, row, A, B } from './fixtures'
import { loadLedgers, writeFailureOf } from './useLedgers'

const src = (s: string) => ({ source: s, title: `題-${s}`, datasetId: null, copiedAt: null, running: false })

describe('loadLedgers', () => {
  it('取り込み元ごとに読み、失敗した 1 件は飛ばして題を failed に積む', async () => {
    const r = await loadLedgers('ja', {
      imports: async () => [src('a'), src('b')],
      ledger: async (s) => {
        if (s.source === 'b') throw new KnowledgeApiError(409, 'x')
        return ledger('a', [row({ from: A, to: B })])
      },
    })
    expect(r.status).toBe('ok')
    expect(r.ledgers).toHaveLength(1)
    expect(r.failed).toEqual(['題-b'])
  })
  it('取り込み元の一覧が読めないときだけ error', async () => {
    const r = await loadLedgers('ja', { imports: async () => { throw new Error('x') }, ledger: async () => ledger('a', []) })
    expect(r.status).toBe('error')
  })
  it('取り込み元が 0 件なら ok で空', async () => {
    expect(await loadLedgers('ja', { imports: async () => [], ledger: async () => ledger('a', []) })).toEqual({ status: 'ok', ledgers: [], failed: [] })
  })
})

describe('writeFailureOf', () => {
  it('409＝取り込み中・422＝理由つき・それ以外', () => {
    expect(writeFailureOf(new KnowledgeApiError(409, 'busy'))).toEqual({ kind: 'busy' })
    expect(writeFailureOf(new KnowledgeApiError(422, '理由'))).toEqual({ kind: 'rejected', reason: '理由' })
    expect(writeFailureOf(new KnowledgeApiError(500, 'boom'))).toEqual({ kind: 'other', message: 'boom' })
    expect(writeFailureOf(new Error('x'))).toEqual({ kind: 'other', message: 'x' })
  })
})
