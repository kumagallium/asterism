import { describe, expect, it } from 'vitest'
import type { CrosswalkPerspective, DiscoverCandidate } from './crosswalkApi'
import {
  alreadyLinkedDisplayName,
  existingJoinNames,
  fieldDisplay,
  joinPayloadFor,
  partitionDiscoverCandidates,
} from './crosswalkLabels'

const RDFS_LABEL = 'http://www.w3.org/2000/01/rdf-schema#label'

describe('fieldDisplay', () => {
  it('names a kind-scoped field after its kind and the design\'s word', () => {
    expect(
      fieldDisplay({
        predicate: RDFS_LABEL,
        predicate_label: '試料化学組成',
        subject_class_label: 'Composition',
      }),
    ).toBe('Composition › 試料化学組成')
  })

  it('falls back to the predicate local name, still under its kind', () => {
    // 値のカタログはどれも rdfs:label で値を持つ — 種類が付いて初めて項目になる。
    expect(fieldDisplay({ predicate: RDFS_LABEL, subject_class_label: 'Doi' })).toBe('Doi › label')
  })

  it('shows the word alone for an untyped subject or an old server', () => {
    expect(fieldDisplay({ predicate: 'https://ex.org/o#comp', predicate_label: '組成' })).toBe('組成')
    expect(fieldDisplay({ predicate: 'https://ex.org/o#comp' })).toBe('comp')
  })
})

// ---------------------------------------------------------------------------
// 契約 contract_d_discover_existing.md R7: 候補の仕分けと join の組み立て
// ---------------------------------------------------------------------------

/** 最小限の DiscoverCandidate（テストに要らないフィールドは空/0 埋め）。 */
function candidate(overrides: Partial<DiscoverCandidate> = {}): DiscoverCandidate {
  return {
    id: 'c1',
    concept: 'composition',
    name: 'composition',
    perspective_id: 'composition',
    perspective_exists: false,
    class_iri: 'https://ex.org/x#Composition',
    link_predicate: 'https://ex.org/x#hasComposition',
    normalizer: 'identity',
    normalizer_trials: [],
    matched: 2,
    score: 1,
    participants: [
      { dataset_id: 'ds-a', label: 'ds-a', name: 'Aデータ', predicate: 'p', predicate_label: 'p', distinct_values: 2, matched: 2, coverage: 1, statements: 2, values_truncated: false },
      { dataset_id: 'ds-b', label: 'ds-b', name: 'Bデータ', predicate: 'p', predicate_label: 'p', distinct_values: 2, matched: 2, coverage: 1, statements: 2, values_truncated: false },
    ],
    samples: [],
    flags: [],
    build_config: { min_datasets: 2, concepts: [] },
    ...overrides,
  }
}

describe('partitionDiscoverCandidates', () => {
  it('keeps a candidate with no existing match visible', () => {
    const c = candidate()
    expect(partitionDiscoverCandidates([c])).toEqual({ visible: [c], alreadyLinked: [] })
  })

  it('keeps a candidate visible when there is something left to add', () => {
    const c = candidate({
      existing: {
        perspective_id: 'composition',
        concept: 'composition',
        linked: [{ dataset_id: 'ds-a', predicate: 'p', subject_class: null }],
        new: [{ dataset_id: 'ds-b', predicate: 'p', subject_class: null }],
        already_linked: false,
      },
    })
    expect(partitionDiscoverCandidates([c])).toEqual({ visible: [c], alreadyLinked: [] })
  })

  it('hides an already_linked candidate from the visible list', () => {
    const c = candidate({
      existing: {
        perspective_id: 'composition',
        concept: 'composition',
        linked: [{ dataset_id: 'ds-a', predicate: 'p', subject_class: null }],
        new: [],
        already_linked: true,
      },
    })
    expect(partitionDiscoverCandidates([c])).toEqual({ visible: [], alreadyLinked: [c] })
  })
})

describe('joinPayloadFor', () => {
  it('is empty for a candidate with no existing match', () => {
    expect(joinPayloadFor(candidate())).toEqual([])
  })

  it('builds the join body from only the NEW participants, with the candidate\'s own labels', () => {
    const c = candidate({
      existing: {
        perspective_id: 'composition',
        concept: 'composition',
        linked: [{ dataset_id: 'ds-a', predicate: 'p', subject_class: null }],
        new: [{ dataset_id: 'ds-b', predicate: 'p2', subject_class: 'https://ex.org/x#Kind' }],
        already_linked: false,
      },
    })
    expect(joinPayloadFor(c)).toEqual([
      {
        dataset_id: 'ds-b',
        label: 'Bデータ',
        predicate: 'p2',
        subject_class: 'https://ex.org/x#Kind',
      },
    ])
  })
})

describe('existingJoinNames', () => {
  it('is undefined for a candidate with no existing match', () => {
    expect(existingJoinNames(candidate())).toBeUndefined()
  })

  it('names both sides from the candidate\'s own participant names', () => {
    const c = candidate({
      existing: {
        perspective_id: 'composition',
        concept: 'composition',
        linked: [{ dataset_id: 'ds-a', predicate: 'p', subject_class: null }],
        new: [{ dataset_id: 'ds-b', predicate: 'p', subject_class: null }],
        already_linked: false,
      },
    })
    expect(existingJoinNames(c)).toEqual({ linked: 'Aデータ', added: 'Bデータ' })
  })
})

describe('alreadyLinkedDisplayName', () => {
  const perspectives: Pick<CrosswalkPerspective, 'perspective_id' | 'dataset'>[] = [
    { perspective_id: 'composition', dataset: { name: '組成でつなぐ' } },
  ]

  it('prefers the connection\'s own display name', () => {
    const c = candidate({
      existing: {
        perspective_id: 'composition',
        concept: 'composition',
        linked: [],
        new: [],
        already_linked: true,
      },
    })
    expect(alreadyLinkedDisplayName(c, perspectives, 'fallback')).toBe('組成でつなぐ')
  })

  it('falls back to the concept display, never the raw ascii key', () => {
    const c = candidate({
      concept: 'crystal_system',
      participants: [
        { dataset_id: 'ds-a', label: 'ds-a', name: 'Aデータ', predicate: 'p', predicate_label: '', distinct_values: 2, matched: 2, coverage: 1, statements: 2, values_truncated: false },
        { dataset_id: 'ds-b', label: 'ds-b', name: 'Bデータ', predicate: 'p', predicate_label: '', distinct_values: 2, matched: 2, coverage: 1, statements: 2, values_truncated: false },
      ],
      existing: {
        perspective_id: 'unknown-perspective',
        concept: 'crystal_system',
        linked: [],
        new: [],
        already_linked: true,
      },
    })
    expect(alreadyLinkedDisplayName(c, perspectives, 'fallback')).toBe('crystal system')
  })

  it('falls back to the given placeholder as a last resort', () => {
    const c = candidate({
      concept: 'shared_value_1',
      participants: [
        { dataset_id: 'ds-a', label: 'ds-a', name: 'Aデータ', predicate: 'p', predicate_label: '', distinct_values: 2, matched: 2, coverage: 1, statements: 2, values_truncated: false },
        { dataset_id: 'ds-b', label: 'ds-b', name: 'Bデータ', predicate: 'p', predicate_label: '', distinct_values: 2, matched: 2, coverage: 1, statements: 2, values_truncated: false },
      ],
      existing: {
        perspective_id: 'unknown-perspective',
        concept: 'shared_value_1',
        linked: [],
        new: [],
        already_linked: true,
      },
    })
    expect(alreadyLinkedDisplayName(c, perspectives, 'fallback')).toBe('fallback')
  })
})
