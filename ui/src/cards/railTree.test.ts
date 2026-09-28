import { describe, expect, it } from 'vitest'
import { buildRailTree } from './railTree'
import type { CardsDatasetSummary, SubjectItem } from './cardsApi'

// 分野語ゼロ（契約メモ §0）: テストデータは架空の 2 分野（図書館の貸出・気象観測）。

function dataset(over: Partial<CardsDatasetSummary> & { id: string }): CardsDatasetSummary {
  return {
    name: over.id,
    origin: 'own',
    is_demo: false,
    stage: 'promoted',
    ...over,
  }
}

function subject(over: Partial<SubjectItem> & { subject_key: string; created_at: string }): SubjectItem {
  return {
    kind: 'individual',
    id: over.subject_key,
    label: null,
    class_label: null,
    source: 'own',
    card_count: null,
    match: null,
    ...over,
  }
}

describe('buildRailTree — 振り分け', () => {
  it('origin: own はそのまま own 節、open は open 節', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-a', origin: 'own' }), dataset({ id: 'ds-b', origin: 'open' })],
      subjects: [],
    })
    expect(tree.own.map((d) => d.datasetId)).toEqual(['ds-a'])
    expect(tree.open.map((d) => d.datasetId)).toEqual(['ds-b'])
  })

  it('origin: unknown は own 節に入る（凡例の黄点＝まだ確かめていない）', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-a', origin: 'unknown' })],
      subjects: [],
    })
    expect(tree.own.map((d) => d.datasetId)).toEqual(['ds-a'])
    expect(tree.open).toHaveLength(0)
  })

  it('主語は dataset_id が一致するデータセットの子になる', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-a', origin: 'own' })],
      subjects: [
        subject({ subject_key: 'i:loan-1', dataset_id: 'ds-a', created_at: '2026-08-01T00:00:00Z' }),
      ],
    })
    expect(tree.own[0]?.children.map((c) => c.subjectKey)).toEqual(['i:loan-1'])
  })
})

describe('buildRailTree — その他', () => {
  it('dataset_id が無い主語は「その他」節に落ちる', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-a' })],
      subjects: [subject({ subject_key: 'i:loan-1', created_at: '2026-08-01T00:00:00Z' })],
    })
    expect(tree.own[0]?.children).toHaveLength(0)
    expect(tree.other.map((c) => c.subjectKey)).toEqual(['i:loan-1'])
  })

  it('dataset_id が既知のどのデータセットとも一致しない主語も「その他」節', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-a' })],
      subjects: [
        subject({ subject_key: 'i:loan-1', dataset_id: 'ds-missing', created_at: '2026-08-01T00:00:00Z' }),
      ],
    })
    expect(tree.other.map((c) => c.subjectKey)).toEqual(['i:loan-1'])
  })
})

describe('buildRailTree — 展開の既定', () => {
  it('データセットが 3 つ以下なら全部展開', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-a' }), dataset({ id: 'ds-b' }), dataset({ id: 'ds-c' })],
      subjects: [],
    })
    expect(tree.own.map((d) => d.expanded)).toEqual([true, true, true])
  })

  it('4 つ以上は「いま開いているページのデータセット」だけ展開', () => {
    const tree = buildRailTree({
      datasets: [
        dataset({ id: 'ds-a' }),
        dataset({ id: 'ds-b' }),
        dataset({ id: 'ds-c' }),
        dataset({ id: 'ds-d' }),
      ],
      subjects: [],
      currentDatasetId: 'ds-c',
    })
    expect(tree.own.find((d) => d.datasetId === 'ds-c')?.expanded).toBe(true)
    expect(tree.own.find((d) => d.datasetId === 'ds-a')?.expanded).toBe(false)
  })

  it('localStorage の上書きが既定より優先される', () => {
    const tree = buildRailTree({
      datasets: [
        dataset({ id: 'ds-a' }),
        dataset({ id: 'ds-b' }),
        dataset({ id: 'ds-c' }),
        dataset({ id: 'ds-d' }),
      ],
      subjects: [],
      currentDatasetId: 'ds-c',
      expandedOverrides: { 'ds-a': true, 'ds-c': false },
    })
    expect(tree.own.find((d) => d.datasetId === 'ds-a')?.expanded).toBe(true)
    expect(tree.own.find((d) => d.datasetId === 'ds-c')?.expanded).toBe(false)
  })
})

describe('buildRailTree — 見本の印', () => {
  it('is_demo はデータセット行にだけ出る（子には出さない）', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-a', origin: 'open', is_demo: true })],
      subjects: [
        subject({ subject_key: 'i:station-1', dataset_id: 'ds-a', created_at: '2026-08-01T00:00:00Z' }),
      ],
    })
    expect(tree.open[0]?.isSample).toBe(true)
    // RailChild には isSample の概念自体が無い（型で保証）— ここでは子の中身に
    // 見本フィールドが紛れ込んでいないことだけ確かめる。
    expect(Object.keys(tree.open[0]?.children[0] ?? {})).not.toContain('isSample')
  })

  it('is_demo が false ならデータセット行の印も出ない', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-a', is_demo: false })],
      subjects: [],
    })
    expect(tree.own[0]?.isSample).toBe(false)
  })
})

describe('buildRailTree — 取り込み状況', () => {
  it('design 段階は draft、ingested 段階は ingesting、promoted は状態なし', () => {
    const tree = buildRailTree({
      datasets: [
        dataset({ id: 'ds-a', stage: 'design' }),
        dataset({ id: 'ds-b', stage: 'ingested' }),
        dataset({ id: 'ds-c', stage: 'promoted' }),
      ],
      subjects: [],
    })
    expect(tree.own.find((d) => d.datasetId === 'ds-a')?.state).toBe('draft')
    expect(tree.own.find((d) => d.datasetId === 'ds-b')?.state).toBe('ingesting')
    expect(tree.own.find((d) => d.datasetId === 'ds-c')?.state).toBeNull()
  })
})
