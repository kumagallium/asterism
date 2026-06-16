import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type Alignment, align, getAlignments, unalign } from './crosswalkApi'
import type { CatalogDataset } from './galleryApi'
import { GroundingPicker } from './GroundingPicker'
import { type GroundCandidate, groundTerms } from './groundingApi'
import { CheckIcon, ConnectIcon, GlobeIcon, LinkIcon, SearchIcon, SparkIcon } from './icons'
import { knownVocabForIri, localName } from './vocab'

/**
 * 外部の標準に合わせる (ground to a standard) — design_handoff v2 ScreenGround. Map this
 * dataset's OWN minted classes/predicates to a famous external standard term (CMSO / QUDT /
 * schema.org …) so the data REUSES a recognized vocabulary instead of staying private
 * (external-standard-alignment.md §8). Plain language, three states only:
 *   合わせ済み (done) — a human-vetted, reversible alignment exists (citable fact).
 *   確認待ち (suggest) — the closed-set search has a top candidate the human can confirm.
 *   未対応 (none) — no candidate yet; search the catalog.
 * Candidates are ALWAYS from the curated closed set (never fabricated); a human confirms.
 */

type SourceTerm = { iri: string; kind: 'class' | 'property'; name: string }
const RELATION_FOR: Record<'class' | 'property', string> = {
  class: 'equivalentClass',
  property: 'equivalentProperty',
}

/** The dataset's own minted terms, split into classes (もの) and fields (項目). Terms
 * already under a known external namespace are reused already, so they are skipped. */
function ownTerms(dataset: CatalogDataset): { classes: SourceTerm[]; fields: SourceTerm[] } {
  const classes = dataset.classIris
    .filter((iri) => !knownVocabForIri(iri))
    .map((iri) => ({ iri, kind: 'class' as const, name: localName(iri) }))
  const fields = dataset.predicates
    .filter((iri) => !knownVocabForIri(iri))
    .map((iri) => ({ iri, kind: 'property' as const, name: localName(iri) }))
  return { classes, fields }
}

/** A standard term shown as plain gloss + standard name + the mono CURIE token. */
function StdToken({
  gloss,
  std,
  token,
  dashed,
}: {
  gloss: string
  std?: string
  token: string
  dashed?: boolean
}) {
  return (
    <span className={`std-token${dashed ? ' std-token--dashed' : ''}`}>
      <span className="std-token-gloss">
        {gloss}
        {std && <span className="std-token-std">{std}</span>}
      </span>
      <code className="std-token-tok">{token}</code>
    </span>
  )
}

export function DatasetGrounding({ dataset }: { dataset: CatalogDataset }) {
  const { t } = useTranslation()
  const [alignments, setAlignments] = useState<Alignment[] | null>(null)
  // Top closed-set candidate per term (undefined = not fetched, null = none found).
  const [cands, setCands] = useState<Record<string, GroundCandidate | null>>({})
  const [activeIri, setActiveIri] = useState('')
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState('')
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const { classes, fields } = ownTerms(dataset)
  const allTerms = [...classes, ...fields]
  const termIris = new Set(allTerms.map((tm) => tm.iri))

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

  // Eager top-candidate per term (closed-set) → drives the 確認待ち (AI候補) state. Runs
  // once per dataset; the human still confirms before anything is asserted.
  useEffect(() => {
    let off = false
    for (const tm of allTerms) {
      groundTerms(tm.name, { kind: tm.kind, limit: 1 })
        .then((r) => !off && setCands((prev) => ({ ...prev, [tm.iri]: r[0] ?? null })))
        .catch(() => !off && setCands((prev) => ({ ...prev, [tm.iri]: null })))
    }
    return () => {
      off = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset.id])

  // This dataset's EXTERNAL groundings: alignments whose source is one of its terms and
  // whose target is a recognized standard term. Keyed by source IRI (one shown per term).
  const groundedBy = new Map<string, Alignment>()
  for (const a of alignments ?? []) {
    if (termIris.has(a.source) && knownVocabForIri(a.target)) groundedBy.set(a.source, a)
  }
  const doneCount = allTerms.filter((tm) => groundedBy.has(tm.iri)).length
  const pct = allTerms.length ? Math.round((doneCount / allTerms.length) * 100) : 0

  async function confirm(term: SourceTerm, c: GroundCandidate) {
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

  function row(term: SourceTerm) {
    const grounded = groundedBy.get(term.iri)
    const cand = cands[term.iri]
    const picking = activeIri === term.iri
    return (
      <div className="ground-row" key={term.iri}>
        <div className="ground-own">
          <div className="ground-own-name">
            {term.name}
            <span className="ground-own-en">{term.kind}</span>
          </div>
        </div>
        <span className="ground-arrow">→</span>
        <div className="ground-target">
          {grounded ? (
            (() => {
              const vocab = knownVocabForIri(grounded.target)
              const curie = vocab
                ? `${vocab.prefix}${localName(grounded.target)}`
                : localName(grounded.target)
              return (
                <>
                  <StdToken gloss={localName(grounded.target)} token={curie} />
                  <span className="ground-state ground-state--done">
                    <CheckIcon size={13} /> {t('grounding:state.done')}
                  </span>
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
                </>
              )
            })()
          ) : picking ? (
            <GroundingPicker
              seed={term.name}
              kind={term.kind}
              onPick={(c) => confirm(term, c)}
              onCancel={() => setActiveIri('')}
            />
          ) : cand ? (
            <>
              <StdToken gloss={cand.label} std={cand.vocab_title} token={cand.curie} dashed />
              <span className="ground-suggest">
                <span className="ground-state ground-state--suggest">
                  <SparkIcon size={12} /> {t('grounding:state.suggest')}
                </span>
                <span className="ground-suggest-actions">
                  <button
                    type="button"
                    className="btn btn--accent btn--sm"
                    disabled={busy}
                    onClick={() => confirm(term, cand)}
                  >
                    <CheckIcon size={13} /> {t('grounding:confirm')}
                  </button>
                  <button
                    type="button"
                    className="ground-link-btn"
                    onClick={() => setActiveIri(term.iri)}
                  >
                    {t('grounding:search')}
                  </button>
                </span>
              </span>
            </>
          ) : (
            <div className="ground-none">
              <span className="ground-none-label">{t('grounding:state.none')}</span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setActiveIri(term.iri)}
              >
                <SearchIcon size={13} /> {t('grounding:search')}
              </button>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="ds-grounding">
      <div className="ground-banner">
        <span className="ground-banner-icon">
          <GlobeIcon size={20} />
        </span>
        <div>
          <div className="ground-banner-title">{t('grounding:banner.title')}</div>
          <p className="ground-banner-sub">{t('grounding:banner.sub')}</p>
        </div>
      </div>

      {err && <p className="promote-err">{t('grounding:adopt.err', { detail: err })}</p>}
      {note && <p className="lifecycle-ok">{note}</p>}

      {allTerms.length === 0 ? (
        <p className="ds-empty-note">{t('grounding:adopt.noTerms')}</p>
      ) : (
        <div className="ground-grid">
          <div className="ground-main card">
            <div className="ground-progress">
              <span className="ground-progress-label">
                {t('grounding:progress', { done: doneCount, total: allTerms.length })}
              </span>
              <span className="ground-progress-bar">
                <span style={{ width: `${pct}%` }} />
              </span>
            </div>
            {classes.length > 0 && (
              <>
                <div className="ground-group-head">
                  {t('grounding:group.classes')}{' '}
                  <span className="ground-group-en">{t('grounding:group.classesEn')}</span>
                </div>
                {classes.map(row)}
              </>
            )}
            {fields.length > 0 && (
              <>
                <div className="ground-group-head">
                  {t('grounding:group.fields')}{' '}
                  <span className="ground-group-en">{t('grounding:group.fieldsEn')}</span>
                </div>
                {fields.map(row)}
              </>
            )}
          </div>

          <div className="ground-aside">
            <div className="card ground-why">
              <h4 className="ground-why-head">{t('grounding:why.head')}</h4>
              <div className="ground-why-item">
                <span className="ground-why-icon">
                  <ConnectIcon size={15} />
                </span>
                {t('grounding:why.p1')}
              </div>
              <div className="ground-why-item">
                <span className="ground-why-icon">
                  <LinkIcon size={15} />
                </span>
                {t('grounding:why.p2')}
              </div>
              <div className="ground-why-item">
                <span className="ground-why-icon">
                  <SearchIcon size={15} />
                </span>
                {t('grounding:why.p3')}
              </div>
            </div>

            {dataset.reuses.length > 0 && (
              <div className="card ground-detected">
                <div className="ground-detected-head">
                  <h4>{t('grounding:detected.head')}</h4>
                  <span className="ground-detected-badge">
                    <CheckIcon size={12} /> {t('grounding:detected.badge')}
                  </span>
                </div>
                <p className="ground-detected-sub">{t('grounding:detected.sub')}</p>
                {dataset.reuses.map((r) => (
                  <div className="ground-detected-row" key={r.prefix}>
                    <code className="ground-detected-prefix">{r.prefix}</code>
                    <span className="ground-detected-what">{t(r.what)}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="ground-caution">
              <span className="ground-caution-icon">!</span>
              <span>{t('grounding:caution')}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
