import { describe, expect, it } from 'vitest'
import { fieldDisplay } from './crosswalkLabels'

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
