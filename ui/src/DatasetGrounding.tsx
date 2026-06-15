import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type Alignment, align, getAlignments, unalign } from './crosswalkApi'
import type { CatalogDataset } from './galleryApi'
import { GroundingPicker } from './GroundingPicker'
import type { GroundCandidate } from './groundingApi'
import { CheckIcon, LinkIcon } from './icons'
import { knownVocabForIri, localName } from './vocab'

/**
 * 接地 (Ground to a standard): let a human map this dataset's OWN minted classes /
 * predicates to a famous external standard term (CMSO / QUDT / schema.org …), so the
 * data REUSES / ALIGNS to a recognized vocabulary instead of staying private
 * (external-standard-alignment.md §8). The candidate term comes from the closed-set
 * grounding search (never fabricated); adopting it asserts a human-vetted, citable,
 * reversible alignment via the existing `/api/crosswalk/align` (a promoted, FROM-merged
 * fact). Once asserted it lights up as an 整合 edge on the ontology map.
 */

type SourceTerm = { iri: string; kind: 'class' | 'property'; name: string }
const RELATION_FOR: Record<'class' | 'property', string> = {
  class: 'equivalentClass',
  property: 'equivalentProperty',
}

/** The dataset's own minted terms (its private vocabulary) — the ones worth grounding.
 * Terms already under a known external namespace are reused already, so they are skipped. */
function ownTerms(dataset: CatalogDataset): SourceTerm[] {
  const terms: SourceTerm[] = [
    ...dataset.classIris.map((iri) => ({ iri, kind: 'class' as const, name: localName(iri) })),
    ...dataset.predicates.map((iri) => ({ iri, kind: 'property' as const, name: localName(iri) })),
  ]
  return terms.filter((t) => !knownVocabForIri(t.iri))
}

export function DatasetGrounding({ dataset }: { dataset: CatalogDataset }) {
  const { t } = useTranslation()
  const [alignments, setAlignments] = useState<Alignment[] | null>(null)
  const [activeIri, setActiveIri] = useState('')
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState('')
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const terms = ownTerms(dataset)
  const termIris = new Set(terms.map((tm) => tm.iri))

  function load() {
    getAlignments()
      .then((d) => setAlignments(d.alignments))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    let off = false
    getAlignments()
      .then((d) => !off && setAlignments(d.alignments))
      .catch((e) => !off && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
  }, [])

  // This dataset's EXTERNAL groundings: alignments whose source is one of its terms and
  // whose target is a recognized standard term. Keyed by source IRI (one shown per term).
  const groundedBy = new Map<string, Alignment>()
  for (const a of alignments ?? []) {
    if (termIris.has(a.source) && knownVocabForIri(a.target)) groundedBy.set(a.source, a)
  }

  async function onPick(term: SourceTerm, c: GroundCandidate) {
    setBusy(true)
    setErr('')
    setNote('')
    try {
      await align(term.iri, c.iri, RELATION_FOR[term.kind], dataset.name, c.vocab_title)
      setNote(t('grounding:adopt.done', { source: term.name, target: c.curie }))
      setActiveIri('')
      load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function onWithdraw(a: Alignment) {
    setRemoving(a.alignment_iri)
    setErr('')
    setNote('')
    try {
      await unalign(a.source, a.target, a.relation)
      load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRemoving('')
    }
  }

  return (
    <div className="ds-grounding">
      <div className="ds-subhead ds-grounding-head">
        <LinkIcon size={14} className="ds-grounding-icon" />
        {t('grounding:adopt.head')}
        <span className="xw-hint-inline">{t('grounding:adopt.hint')}</span>
      </div>

      {err && <p className="promote-err">{t('grounding:adopt.err', { detail: err })}</p>}
      {note && <p className="lifecycle-ok">{note}</p>}

      {terms.length === 0 ? (
        <p className="ds-empty-note">{t('grounding:adopt.noTerms')}</p>
      ) : (
        <ul className="ds-grounding-list">
          {terms.map((term) => {
            const grounded = groundedBy.get(term.iri)
            const vocab = grounded ? knownVocabForIri(grounded.target) : undefined
            return (
              <li className="ds-grounding-row" key={term.iri}>
                <div className="ds-grounding-term">
                  <code className="ds-grounding-name" title={term.iri}>
                    {term.name}
                  </code>
                  <span className="ds-grounding-kind">
                    {term.kind === 'class' ? t('grounding:kind.class') : t('grounding:kind.property')}
                  </span>
                </div>

                {grounded ? (
                  <div className="ds-grounding-linked">
                    <CheckIcon size={13} className="ds-grounding-check" />
                    <code className="grounding-curie" title={grounded.target}>
                      {vocab ? `${vocab.prefix}${localName(grounded.target)}` : localName(grounded.target)}
                    </code>
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      disabled={removing === grounded.alignment_iri}
                      onClick={() => onWithdraw(grounded)}
                    >
                      {removing === grounded.alignment_iri
                        ? t('grounding:adopt.withdrawing')
                        : t('grounding:adopt.withdraw')}
                    </button>
                  </div>
                ) : activeIri === term.iri ? (
                  <GroundingPicker
                    seed={term.name}
                    kind={term.kind}
                    domain={undefined}
                    onPick={(c) => onPick(term, c)}
                    onCancel={() => setActiveIri('')}
                  />
                ) : (
                  <button
                    type="button"
                    className="btn btn--accent btn--sm"
                    disabled={busy}
                    onClick={() => setActiveIri(term.iri)}
                  >
                    {t('grounding:adopt.ground')}
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
