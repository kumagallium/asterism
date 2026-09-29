import { describe, expect, it } from 'vitest'
import { fieldDisplay, perspectiveDisplayName } from './crosswalkLabels'

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

// 契約メモ contract_b2_hub_names.md B2-2: display_name（R1、読むたびに解決した
// 表示名）が dataset.name より優先する。
describe('perspectiveDisplayName', () => {
  it('prefers display_name over the stored dataset name', () => {
    expect(
      perspectiveDisplayName({ display_name: '共有たな', dataset: { name: 'crosswalk-bridge' } }),
    ).toBe('共有たな')
  })

  it('falls back to the stored dataset name when display_name is absent (older server)', () => {
    expect(perspectiveDisplayName({ dataset: { name: '共有たな' } })).toBe('共有たな')
  })

  it('treats the server "no name" constant in display_name as no name', () => {
    expect(
      perspectiveDisplayName({ display_name: '名前のないつながり', dataset: { name: 'ignored' } }),
    ).toBeUndefined()
  })

  it('treats an old implementation-minted name in display_name as no name', () => {
    expect(
      perspectiveDisplayName({ display_name: 'crosswalk: shelf-view', dataset: { name: 'x' } }),
    ).toBeUndefined()
  })

  it('is undefined when neither display_name nor the dataset name is a real name', () => {
    expect(perspectiveDisplayName({})).toBeUndefined()
    expect(perspectiveDisplayName({ display_name: '', dataset: { name: '' } })).toBeUndefined()
  })
})
