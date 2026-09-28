import { describe, expect, it } from 'vitest'
import {
  addSubject,
  parseStoredSubjects,
  removeSubject,
  serializeSubjects,
  sortSubjects,
} from './subjectStore'
import type { SubjectItem } from './cardsApi'

// 分野語ゼロ（契約メモ §0）: テストデータは架空の 2 分野（図書館の貸出・気象観測）。

function item(over: Partial<SubjectItem> & { id: string; created_at: string }): SubjectItem {
  return {
    kind: 'individual',
    label: null,
    class_label: null,
    source: 'own',
    card_count: null,
    match: null,
    subject_key: `i:https://example.org/${over.id}`,
    ...over,
  }
}

describe('sortSubjects', () => {
  it('own を open より前に置く', () => {
    const open = item({ id: 'station-a', source: 'open', created_at: '2026-09-01T00:00:00Z' })
    const own = item({ id: 'loan-b', source: 'own', created_at: '2026-08-01T00:00:00Z' })
    expect(sortSubjects([open, own])).toEqual([own, open])
  })

  it('各グループの中は created_at 降順（新しい方が上）', () => {
    const older = item({ id: 'loan-a', source: 'own', created_at: '2026-08-01T00:00:00Z' })
    const newer = item({ id: 'loan-b', source: 'own', created_at: '2026-09-01T00:00:00Z' })
    expect(sortSubjects([older, newer])).toEqual([newer, older])
  })

  it('own/open が混在しても、グループごとの降順を保ったまま own が先に来る', () => {
    const ownOld = item({ id: 'loan-a', source: 'own', created_at: '2026-07-01T00:00:00Z' })
    const ownNew = item({ id: 'loan-b', source: 'own', created_at: '2026-09-01T00:00:00Z' })
    const openOld = item({ id: 'station-a', source: 'open', created_at: '2026-06-01T00:00:00Z' })
    const openNew = item({ id: 'station-b', source: 'open', created_at: '2026-08-01T00:00:00Z' })
    expect(sortSubjects([openOld, ownOld, openNew, ownNew])).toEqual([
      ownNew,
      ownOld,
      openNew,
      openOld,
    ])
  })

  it('入力配列を書き換えない（純粋）', () => {
    const a = item({ id: 'loan-a', source: 'own', created_at: '2026-08-01T00:00:00Z' })
    const b = item({ id: 'loan-b', source: 'open', created_at: '2026-09-01T00:00:00Z' })
    const input = [a, b]
    const copy = [...input]
    sortSubjects(input)
    expect(input).toEqual(copy)
  })
})

describe('addSubject', () => {
  it('新しい subject_key はそのまま足す', () => {
    const a = item({ id: 'loan-a', created_at: '2026-08-01T00:00:00Z' })
    const b = item({ id: 'loan-b', created_at: '2026-09-01T00:00:00Z' })
    expect(addSubject([a], b)).toEqual([a, b])
  })

  it('同じ subject_key は重複させず置き換える', () => {
    const original = item({ id: 'loan-a', created_at: '2026-08-01T00:00:00Z', label: '旧' })
    const replacement: SubjectItem = { ...original, label: '新' }
    const result = addSubject([original], replacement)
    expect(result).toHaveLength(1)
    expect(result[0]?.label).toBe('新')
  })

  it('入力配列を書き換えない（純粋）', () => {
    const a = item({ id: 'loan-a', created_at: '2026-08-01T00:00:00Z' })
    const input = [a]
    addSubject(input, item({ id: 'loan-b', created_at: '2026-09-01T00:00:00Z' }))
    expect(input).toHaveLength(1)
  })
})

describe('removeSubject', () => {
  it('id に一致する 1 件だけ取り除く', () => {
    const a = item({ id: 'loan-a', created_at: '2026-08-01T00:00:00Z' })
    const b = item({ id: 'loan-b', created_at: '2026-09-01T00:00:00Z' })
    expect(removeSubject([a, b], 'loan-a')).toEqual([b])
  })

  it('一致が無ければ何も変わらない', () => {
    const a = item({ id: 'loan-a', created_at: '2026-08-01T00:00:00Z' })
    expect(removeSubject([a], 'loan-x')).toEqual([a])
  })
})

describe('localStorage フォールバック（parseStoredSubjects / serializeSubjects）', () => {
  it('無い（null）ときは空配列', () => {
    expect(parseStoredSubjects(null)).toEqual([])
  })

  it('壊れた JSON は空配列に倒す', () => {
    expect(parseStoredSubjects('{not json')).toEqual([])
  })

  it('items が配列でない形は空配列に倒す', () => {
    expect(parseStoredSubjects(JSON.stringify({ v: 1, items: 'oops' }))).toEqual([])
  })

  it('serializeSubjects → parseStoredSubjects が往復する', () => {
    const items = [
      item({ id: 'loan-a', created_at: '2026-08-01T00:00:00Z' }),
      item({ id: 'station-a', source: 'open', created_at: '2026-09-01T00:00:00Z' }),
    ]
    expect(parseStoredSubjects(serializeSubjects(items))).toEqual(items)
  })
})
