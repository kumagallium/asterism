import { describe, expect, it } from 'vitest'
import type { UpperItem } from '../api'
import { bindingsForColumn, columnBindings, columnOfRef } from './chatBindings'
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
      { column: '粒径', term: T, relation: 'equivalentProperty', fromChat: true },
      { column: '重さ', term: T, relation: 'subPropertyOf', fromChat: false },
    ])
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
