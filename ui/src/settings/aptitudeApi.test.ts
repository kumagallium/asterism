import { afterEach, describe, expect, it, vi } from 'vitest'
import { failureParts, fetchAptitudes, limitedHintKey } from './aptitudeApi'

afterEach(() => vi.unstubAllGlobals())

describe('fetchAptitudes', () => {
  it('api の {"aptitude": {...}} の包みを読む', async () => {
    const table = { 'a::m': { verdict: 'good', shape: 1, routing: 1, scope: 1, failed: [], at: 't' } }
    vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({ aptitude: table }) }))
    expect(await fetchAptitudes()).toEqual(table)
  })
})

describe('落ちた問の分け方', () => {
  it('形の修正は 5〜9 を #1〜5 にする', () => {
    expect(failureParts({ kind: 'shape', index: 7, reason: 'diff' })).toMatchObject({ kindKey: 'shapeDiff', no: 3 })
    expect(failureParts({ kind: 'shape', index: 0, reason: 'create' })).toMatchObject({ kindKey: 'shapeCreate', no: 1 })
  })
  it('条件つきの案内は原因で分ける', () => {
    const r = { verdict: 'limited' as const, shape: 0.95, routing: 0.8, scope: 0.8, failed: [], at: '' }
    expect(limitedHintKey(r)).toBe('limitedHintRouting')
    expect(limitedHintKey({ ...r, shape: 0.5 })).toBe('limitedHint')
  })
})
