import { describe, expect, it } from 'vitest'
import { buildRailTree } from './railTree'
import type { CardsDatasetSummary, ClassEntry, SubjectItem } from './cardsApi'

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

describe('buildRailTree — 種類ごとのグループ化（own/open の区切りは無い）', () => {
  it('class_iri が同じ主語は同じグループの子になる（新しい方が上）', () => {
    const tree = buildRailTree({
      datasets: [],
      subjects: [
        subject({
          subject_key: 'i:loan-1',
          class_iri: 'https://example.org/class/loan',
          class_label: '貸出',
          created_at: '2026-08-01T00:00:00Z',
        }),
        subject({
          subject_key: 'i:loan-2',
          class_iri: 'https://example.org/class/loan',
          class_label: '貸出',
          created_at: '2026-08-02T00:00:00Z',
        }),
      ],
    })
    expect(tree.kinds).toHaveLength(1)
    expect(tree.kinds[0]?.classIri).toBe('https://example.org/class/loan')
    expect(tree.kinds[0]?.label).toBe('貸出')
    expect(tree.kinds[0]?.children.map((c) => c.subjectKey)).toEqual(['i:loan-2', 'i:loan-1'])
  })

  it('グループは class_label の名前順に並ぶ', () => {
    const tree = buildRailTree({
      datasets: [],
      subjects: [
        subject({
          subject_key: 'i:obs-1',
          class_iri: 'https://example.org/class/observation',
          class_label: '気象観測',
          created_at: '2026-08-01T00:00:00Z',
        }),
        subject({
          subject_key: 'i:loan-1',
          class_iri: 'https://example.org/class/loan',
          class_label: '図書館の貸出',
          created_at: '2026-08-01T00:00:00Z',
        }),
      ],
    })
    expect(tree.kinds.map((k) => k.label)).toEqual(['図書館の貸出', '気象観測'])
  })

  it('source（own/open）が違っても同じグループにまとまる', () => {
    const tree = buildRailTree({
      datasets: [],
      subjects: [
        subject({
          subject_key: 'i:loan-1',
          source: 'own',
          class_iri: 'https://example.org/class/loan',
          class_label: '貸出',
          created_at: '2026-08-01T00:00:00Z',
        }),
        subject({
          subject_key: 'i:loan-2',
          source: 'open',
          class_iri: 'https://example.org/class/loan',
          class_label: '貸出',
          created_at: '2026-08-02T00:00:00Z',
        }),
      ],
    })
    expect(tree.kinds).toHaveLength(1)
    expect(tree.kinds[0]?.children).toHaveLength(2)
  })

  it('絞り込み（kind: set）の子は match が常に null', () => {
    const tree = buildRailTree({
      datasets: [],
      subjects: [
        subject({
          subject_key: 's:set-1',
          kind: 'set',
          class_iri: 'https://example.org/class/loan',
          class_label: '貸出',
          match: 'linked',
          created_at: '2026-08-01T00:00:00Z',
        }),
      ],
    })
    expect(tree.kinds[0]?.children[0]?.match).toBeNull()
  })
})

function classEntry(over: Partial<ClassEntry> & { class_iri: string }): ClassEntry {
  return {
    label: over.class_iri,
    count: 0,
    dataset_id: 'ds-x',
    dataset_label: 'ds-x',
    is_demo: false,
    properties: 0,
    with_label: 0,
    with_unit: 0,
    ...over,
  }
}

describe('buildRailTree — ハブの印（PR F16）', () => {
  it('classes に is_hub がある種類だけ isHub が true になる', () => {
    const tree = buildRailTree({
      datasets: [],
      classes: [classEntry({ class_iri: 'https://example.org/class/loan', is_hub: true })],
      subjects: [
        subject({
          subject_key: 'i:loan-1',
          class_iri: 'https://example.org/class/loan',
          class_label: '貸出',
          created_at: '2026-08-01T00:00:00Z',
        }),
        subject({
          subject_key: 'i:obs-1',
          class_iri: 'https://example.org/class/observation',
          class_label: '気象観測',
          created_at: '2026-08-01T00:00:00Z',
        }),
      ],
    })
    expect(tree.kinds.find((k) => k.classIri === 'https://example.org/class/loan')?.isHub).toBe(true)
    expect(tree.kinds.find((k) => k.classIri === 'https://example.org/class/observation')?.isHub).toBe(false)
  })

  it('classes を渡さなければ全て isHub: false', () => {
    const tree = buildRailTree({
      datasets: [],
      subjects: [
        subject({
          subject_key: 'i:loan-1',
          class_iri: 'https://example.org/class/loan',
          class_label: '貸出',
          created_at: '2026-08-01T00:00:00Z',
        }),
      ],
    })
    expect(tree.kinds[0]?.isHub).toBe(false)
  })
})

describe('buildRailTree — その他', () => {
  it('class_iri が無い主語は「その他」節に落ちる', () => {
    const tree = buildRailTree({
      datasets: [],
      subjects: [subject({ subject_key: 'i:loan-1', created_at: '2026-08-01T00:00:00Z' })],
    })
    expect(tree.kinds).toHaveLength(0)
    expect(tree.other.map((c) => c.subjectKey)).toEqual(['i:loan-1'])
  })
})

describe('buildRailTree — 見本の印', () => {
  it('is_demo のデータセット由来の主語にだけ見本の印が付く（グループの見出しには出ない）', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-sample', is_demo: true }), dataset({ id: 'ds-own', is_demo: false })],
      subjects: [
        subject({
          subject_key: 'i:station-1',
          class_iri: 'https://example.org/class/observation',
          class_label: '気象観測',
          dataset_id: 'ds-sample',
          created_at: '2026-08-01T00:00:00Z',
        }),
        subject({
          subject_key: 'i:station-2',
          class_iri: 'https://example.org/class/observation',
          class_label: '気象観測',
          dataset_id: 'ds-own',
          created_at: '2026-08-02T00:00:00Z',
        }),
      ],
    })
    const children = tree.kinds[0]?.children ?? []
    expect(children.find((c) => c.subjectKey === 'i:station-1')?.isSample).toBe(true)
    expect(children.find((c) => c.subjectKey === 'i:station-2')?.isSample).toBe(false)
    // RailKindNode には isSample の概念自体が無い（型で保証）。
    expect(Object.keys(tree.kinds[0] ?? {})).not.toContain('isSample')
  })

  it('dataset_id が無い主語は見本にならない', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-sample', is_demo: true })],
      subjects: [
        subject({
          subject_key: 'i:loan-1',
          class_iri: 'https://example.org/class/loan',
          class_label: '貸出',
          created_at: '2026-08-01T00:00:00Z',
        }),
      ],
    })
    expect(tree.kinds[0]?.children[0]?.isSample).toBe(false)
  })

  it('is_demo が false のデータセット由来なら見本にならない', () => {
    const tree = buildRailTree({
      datasets: [dataset({ id: 'ds-own', is_demo: false })],
      subjects: [
        subject({
          subject_key: 'i:loan-1',
          class_iri: 'https://example.org/class/loan',
          class_label: '貸出',
          dataset_id: 'ds-own',
          created_at: '2026-08-01T00:00:00Z',
        }),
      ],
    })
    expect(tree.kinds[0]?.children[0]?.isSample).toBe(false)
  })
})
