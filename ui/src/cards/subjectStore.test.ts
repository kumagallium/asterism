import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  addSubject,
  addSubjectAndPersist,
  backfillDatasetIds,
  getAllSubjects,
  parseStoredSubjects,
  removeSubject,
  removeSubjectAndPersist,
  serializeSubjects,
  sortSubjects,
} from './subjectStore'
import { classSchema, resolveSubject } from './cardsApi'
import type { SubjectItem } from './cardsApi'

// `backfillDatasetIds`（appdata への書き戻しを伴う移行ロジック）のテストのため
// `cardsApi.ts` の呼び出し先だけモックする（他の純関数のテストには影響しない —
// このファイル内でのみ有効）。
vi.mock('./cardsApi', () => ({
  resolveSubject: vi.fn(),
  classSchema: vi.fn(),
  putAppDataSubject: vi.fn(),
  deleteAppDataSubject: vi.fn(),
  fetchAppDataSubjects: vi.fn(),
}))

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

describe('backfillDatasetIds（appdata への書き戻しを伴う移行ロジック・契約メモ §2.1）', () => {
  afterEach(() => {
    // 足した項目を毎回掃除する（localStorage 運用のため互いに影響しないように）。
    for (const i of getAllSubjects()) removeSubjectAndPersist(i.id)
    vi.mocked(resolveSubject).mockReset()
    vi.mocked(classSchema).mockReset()
  })

  it('個体（kind: individual）は dataset_id が無ければ resolveSubject の結果で埋める', async () => {
    vi.mocked(resolveSubject).mockResolvedValue({
      iri: 'loan-c',
      found: true,
      label: null,
      class_iri: null,
      class_label: null,
      dataset_id: 'library',
      dataset_label: '図書館',
      snapshot: null,
    })
    addSubjectAndPersist(item({ id: 'loan-c', created_at: '2026-08-01T00:00:00Z' }))

    await backfillDatasetIds()

    const updated = getAllSubjects().find((i) => i.id === 'loan-c')
    expect(updated?.dataset_id).toBe('library')
    expect(updated?.dataset_label).toBe('図書館')
    expect(resolveSubject).toHaveBeenCalledWith('loan-c')
    expect(classSchema).not.toHaveBeenCalled()
  })

  it('絞り込み（kind: set）は dataset_id が無ければ classSchema(spec.class) の結果で埋める', async () => {
    vi.mocked(classSchema).mockResolvedValue({
      class_iri: 'https://example.org/class/observation',
      label: '観測',
      dataset_id: 'observation',
      snapshot: null,
      properties: [],
      tools: [],
      dataset_label: '気象観測',
    })
    const spec: SubjectItem['spec'] = {
      class: 'https://example.org/class/observation',
      where: [],
      order_by: null,
      limit: 50,
      source_scope: 'all',
    }
    addSubjectAndPersist({
      ...item({ id: 'set-a', created_at: '2026-08-01T00:00:00Z' }),
      kind: 'set',
      spec,
      subject_key: 's:set-a',
    })

    await backfillDatasetIds()

    const updated = getAllSubjects().find((i) => i.id === 'set-a')
    expect(updated?.dataset_id).toBe('observation')
    expect(updated?.dataset_label).toBe('気象観測')
    expect(classSchema).toHaveBeenCalledWith(spec.class)
    expect(resolveSubject).not.toHaveBeenCalled()
  })

  it('解決に失敗しても諦めて据え置く（＝レールの「その他」節に落ちる）— 例外を投げない', async () => {
    vi.mocked(resolveSubject).mockRejectedValue(new Error('network down'))
    addSubjectAndPersist(item({ id: 'loan-d', created_at: '2026-08-01T00:00:00Z' }))

    await expect(backfillDatasetIds()).resolves.toBeUndefined()

    const updated = getAllSubjects().find((i) => i.id === 'loan-d')
    expect(updated?.dataset_id).toBeUndefined()
  })
})
