import { describe, expect, it } from 'vitest'
import type { UpperItem } from '../api'
import { bindingsForColumn, columnBindings, columnOfRef, countFitPairs, sameFound } from './chatBindings'
import { hasChatProvenance, picksToUpper, upperToPicks } from './kindFit'
import type { MappingSkeleton } from '../api'

const T = 'https://sv#grain_size'
const item = (subject: string, term: string, relation: UpperItem['relation'], extra: object = {}): UpperItem =>
  ({ subject, term, relation, ...extra }) as UpperItem

describe('会話で受けた列の結びつき', () => {
  it('property:<map>/<列> から列の見出しを取り出す（形が違えば null・見出しに / があっても残す）', () => {
    expect(columnOfRef('property:SampleMap/粒径')).toBe('粒径')
    expect(columnOfRef('property:SampleMap/a/b')).toBe('a/b')
    expect(columnOfRef('property:nomap')).toBeNull()
    expect(columnOfRef('https://ex/onto#price')).toBeNull()
  })

  it('項目の当てはめだけを列ごとに読む（種類の線は除く・同じものは 1 つ・来歴の有無を持つ）', () => {
    const rows = columnBindings([
      item('property:M/粒径', T, 'equivalentProperty', { drafted_by: 'ai', batch: 'b', reason: '一言' }),
      item('property:M/粒径', T, 'equivalentProperty'),
      item('property:M/重さ', T, 'subPropertyOf'),
      item('https://ex/onto#Sample', 'https://sv#specimen', 'subClassOf'),
    ])
    expect(rows).toEqual([
      { subjects: ['property:M/粒径'], column: '粒径', term: T, relation: 'equivalentProperty', fromChat: true, applied: false, skipped: null },
      { subjects: ['property:M/重さ'], column: '重さ', term: T, relation: 'subPropertyOf', fromChat: false, applied: false, skipped: null },
    ])
  })

  it('公開で線になった・見送りになった・まだ、を持つ（外すに送る subject は同じ見出しの全 map）', () => {
    const rows = columnBindings([
      item('property:M/粒径', T, 'equivalentProperty', { drafted_by: 'ai', applied_at: '2026-10-20T00:00:00Z' }),
      item('property:M/重さ', T, 'subPropertyOf', { drafted_by: 'ai', skipped: 'subject missing' }),
      item('property:M/重さ', T, 'subPropertyOf', { applied_at: '2026-10-20T00:00:00Z', skipped: 'x' }),
      item('property:N/重さ', T, 'subPropertyOf', { drafted_by: 'ai' }),
    ])
    expect(rows.map((r) => [r.column, r.applied, r.skipped])).toEqual([
      ['粒径', true, null],
      ['重さ', false, 'subject missing'],
    ])
    expect(rows[1].subjects).toEqual(['property:M/重さ', 'property:N/重さ'])
  })

  it('最初のカードの N は（列, 用語）の組で重複を除く', () => {
    const rows = [
      { column: '食材', terms: ['t1', 't2'] },
      { column: '食材', terms: ['t1'] }, // 別のファイルの同じ見出し — 同じ組は 1 件
      { column: '価格', terms: ['t1'] },
    ]
    expect(countFitPairs(rows)).toBe(3)
    expect(countFitPairs([])).toBe(0)
    expect(sameFound(undefined, [])).toBe(true)
    expect(sameFound({ column: 'a', terms: ['x'] }, ['x'])).toBe(true)
    expect(sameFound({ column: 'a', terms: ['x'] }, ['x', 'y'])).toBe(false)
  })

  it('行の印は、その列の分だけ・表で受けた fit と同じ語は出さない', () => {
    const all = columnBindings([item('property:M/粒径', T, 'equivalentProperty'), item('property:M/重さ', T, 'subPropertyOf')])
    expect(bindingsForColumn(all, '粒径')).toHaveLength(1)
    expect(bindingsForColumn(all, '粒径', T)).toEqual([])
    expect(bindingsForColumn(all, 'なし')).toEqual([])
  })
})

describe('作り直し（materialize）の upper に、会話で受けた項目の線を残す', () => {
  const sk: MappingSkeleton = { version: 1, prefixes: {}, maps: [] }
  it('来歴つきの property: の項目は、③の fit に無くても carried として残る。来歴の無いものは従来どおり落ちる', () => {
    const chat = item('property:M/粒径', T, 'equivalentProperty', { drafted_by: 'ai', batch: 'b1', reason: '一言' })
    const table = item('property:M/重さ', T, 'subPropertyOf')
    expect(hasChatProvenance(chat)).toBe(true)
    expect(hasChatProvenance(table)).toBe(false)
    const out = picksToUpper({}, sk, [chat, table], []) // itemFits = [] — 表の fit は 1 つも無い
    expect(out).toEqual([chat])
  })

  it('保管済みの読み戻し（upperToPicks）は来歴を落とさずに持ち越す', () => {
    const stored = [item('property:M/粒径', T, 'equivalentProperty', { drafted_by: 'ai', batch: 'b1', reason: '一言', applied_at: 'x' })]
    const { other } = upperToPicks(stored, sk, (i) => i)
    expect(other).toEqual([{ subject: 'property:M/粒径', term: T, relation: 'equivalentProperty', drafted_by: 'ai', batch: 'b1', reason: '一言' }])
  })
})
