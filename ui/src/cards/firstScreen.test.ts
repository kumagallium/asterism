import { describe, expect, it } from 'vitest'
import type { SubjectItem } from './cardsApi'
import { pickSampleSubjects } from './FirstScreen'

// 分野語ゼロ（契約メモ §0）: テストデータは架空の 2 分野（図書館の貸出・気象観測）。
// subjectStore.test.ts と同じ helper 流儀。

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

describe('pickSampleSubjects', () => {
  it('自分のデータ（own）は見本の候補にしない', () => {
    const own = item({ id: 'loan-a', source: 'own', created_at: '2026-01-01T00:00:00Z' })
    expect(pickSampleSubjects([own])).toEqual({ individual: null, set: null })
  })

  it('open の個体・絞り込みが 1 件ずつあれば両方を返す', () => {
    const individual = item({ id: 'station-a', source: 'open', created_at: '2026-01-01T00:00:00Z' })
    const set = item({
      id: 'set-a',
      kind: 'set',
      source: 'open',
      subject_key: 's:set-a',
      created_at: '2026-01-01T00:00:01Z',
    })
    expect(pickSampleSubjects([individual, set])).toEqual({ individual, set })
  })

  it('open の個体が複数あれば、作られた時刻が一番古いもの（boot が仕込んだもの）を選ぶ', () => {
    const seeded = item({ id: 'station-a', source: 'open', created_at: '2026-01-01T00:00:00Z' })
    const searchedLater = item({
      id: 'station-b',
      source: 'open',
      created_at: '2026-02-01T00:00:00Z',
    })
    // 追加された順序に関係なく、created_at で決める（配列順に依存しない）。
    expect(pickSampleSubjects([searchedLater, seeded]).individual).toEqual(seeded)
  })

  it('open の絞り込みが無ければ set は null（individual だけ見本があってもよい）', () => {
    const individual = item({ id: 'station-a', source: 'open', created_at: '2026-01-01T00:00:00Z' })
    expect(pickSampleSubjects([individual])).toEqual({ individual, set: null })
  })

  it('候補が無ければ両方 null', () => {
    expect(pickSampleSubjects([])).toEqual({ individual: null, set: null })
  })
})
