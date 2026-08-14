import { Fragment, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type {
  MappingSkeleton,
  SkeletonAnnotations,
  SkeletonMap,
  SkeletonMapAnnotation,
} from './api'
import {
  compactClass,
  compactTemplate,
  detectDatasetNamespace,
  expandClass,
  expandTemplate,
  renameDatasetNamespace,
} from './datasetNamespace'
import { Mermaid } from './Mermaid'
import { skeletonMermaid } from './skeletonDiagram'

// (Moved verbatim from WorkbenchView.tsx so the kantan wizard shares the gate.
//  Only addition: the title/hint/continue labels are overridable via *Key props
//  so the kantan tier can show plain-language copy — defaults keep the exact
//  workbench strings, so the detail tier is byte-identical.)

// Human-readable reasons a map's key could not be checked (kept in sync with
// skeleton_annotate's machine-readable `reason` values).
function evidenceReasonKey(reason: string | undefined): string {
  if (!reason) return 'workbench:skeleton.evidence.notChecked'
  if (reason === 'constant') return 'workbench:skeleton.evidence.constant'
  if (reason === 'missing-columns') return 'workbench:skeleton.evidence.missingColumns'
  if (reason === 'source-not-found') return 'workbench:skeleton.evidence.sourceNotFound'
  if (reason === 'no-template') return 'workbench:skeleton.evidence.noTemplate'
  if (reason.startsWith('unsupported-source-kind')) return 'workbench:skeleton.evidence.unsupported'
  return 'workbench:skeleton.evidence.notChecked'
}

// The per-map evidence block: is the key REALLY unique, shown with the data
// (real example IDs, concrete colliding rows, proven fix candidates) — so a
// domain expert can judge the skeleton without knowing what an IRI is.
function SkeletonEvidence({
  ann,
  onApplyCandidate,
  onAddRowKind,
  canRevalidate = true,
  displayClass,
}: {
  ann: SkeletonMapAnnotation
  onApplyCandidate: (columns: string[]) => void
  /** Add the row-level map this source is missing (one click, server-suggested). */
  onAddRowKind?: () => void
  /** False when the sources are gone (restored session / reload): the edit
   *  cannot be re-checked, so the one-click add would land unverified. */
  canRevalidate?: boolean
  /** Kantan tier: fold the minted prefix out of class names in evidence copy
   *  (the annotation carries full CURIEs). Absent on the detail tier. */
  displayClass?: (value: string) => string
}) {
  const { t } = useTranslation()

  const prefixWarning = ann.undeclared_prefixes.length > 0 && (
    <p className="skeleton-evidence-line skeleton-evidence-warn">
      {t('workbench:skeleton.evidence.undeclaredPrefixes', {
        prefixes: ann.undeclared_prefixes.join(', '),
      })}
    </p>
  )

  if (!ann.checkable) {
    return (
      <div className="skeleton-evidence">
        <p className="skeleton-evidence-line skeleton-evidence-muted">
          {t(evidenceReasonKey(ann.reason), {
            columns: (ann.missing_columns ?? []).join(', '),
          })}
        </p>
        {ann.reason === 'constant' && ann.expanded_template && (
          <p className="skeleton-evidence-line skeleton-evidence-muted">
            <code className="skeleton-evidence-id">{ann.expanded_template}</code>
          </p>
        )}
        {prefixWarning}
      </div>
    )
  }

  // A key that merges ALL rows is the file-scoped metadata pattern (one
  // reference card, one run header) — merging is the point, so it renders as
  // the green "everything gathers on one card" state, never as the collision
  // accident. Only a PARTIAL collapse is the accident. Older servers don't
  // send collapse_kind; fall back to the raw is_unique reading.
  const singleton = ann.collapse_kind === 'singleton'
  const collides = ann.collapse_kind
    ? ann.collapse_kind === 'partial'
    : ann.is_unique === false
  // K7: a key that is unique TODAY but built only from measurement values gets
  // an amber caution under the green band, and the proven candidates still show
  // (the green band alone let a semantically wrong ID through in real dogfood).
  const caution = ann.is_unique === true && ann.key_measurement_caution === true
  // Citation-consequence risks (server-detected, machine-readable): what this
  // ID recipe DOES to references later — shown instead of the abstract K7 copy
  // when the server is new enough to send them.
  const risks = ann.reference_risks ?? []
  const measurementRisk = risks.find((r) => r.kind === 'measurement-id')
  const scopeRisk = risks.find((r) => r.kind === 'scope-missing')
  const card = ann.entity_preview
  // Columns another map owns (ADR column-ownership-and-growth), and — on a
  // file-scoped map — what appending the next file does to this design.
  const borrowed = ann.borrowed_columns ?? []
  const growth = ann.growth_preview
  const gap = ann.missing_row_kind
  const displayCls = (value: string) => (displayClass ? displayClass(value) : value)
  const cardCls = (ann.expanded_classes[0]?.curie && displayCls(ann.expanded_classes[0].curie)) || ''
  const candidateChips = (ann.key_candidates?.length ?? 0) > 0 && (
    <>
      {ann.key_candidates!.map((c) => (
        <button
          key={c.columns.join(' ')}
          type="button"
          className={
            c.scoped ? 'skeleton-candidate-chip skeleton-candidate-chip--scoped' : 'skeleton-candidate-chip'
          }
          title={
            c.scoped
              ? t('workbench:skeleton.evidence.scopedCandidate')
              : c.measurement_only
                ? t('workbench:skeleton.evidence.measurementOnly')
                : undefined
          }
          onClick={() => onApplyCandidate(c.columns)}
        >
          {c.columns.map((col) => `{${col}}`).join(' + ')}
          {c.measurement_only && ' ⚠'}
        </button>
      ))}
    </>
  )
  // Non-singleton: chips show whenever something is wrong (collision, caution,
  // risk). Singleton: merging is normal, so the chips fold behind the explicit
  // "should this be one record per row?" question instead.
  const showCandidates = !singleton && (collides || caution || risks.length > 0) && candidateChips
  return (
    <div className="skeleton-evidence">
      {singleton ? (
        <p className="skeleton-evidence-line skeleton-evidence-ok">
          ✓ {t('workbench:skeleton.evidence.singleton', { total: ann.total_rows })}
        </p>
      ) : ann.is_unique ? (
        <p className="skeleton-evidence-line skeleton-evidence-ok">
          ✓ {t('workbench:skeleton.evidence.unique', { rows: ann.total_rows })}
        </p>
      ) : (
        <p className="skeleton-evidence-line skeleton-evidence-bad">
          ⚠ {t('workbench:skeleton.evidence.collides', {
            total: ann.total_rows,
            colliding: ann.colliding_rows,
          })}
        </p>
      )}
      {collides &&
        (ann.collision_examples ?? []).map((ex, i) => (
          <p key={i} className="skeleton-evidence-line skeleton-evidence-muted">
            {t('workbench:skeleton.evidence.collisionExample', {
              lines: ex.line_numbers.join(', '),
              values: Object.entries(ex.key_values)
                .map(([k, v]) => `${k} = ${v}`)
                .join(', '),
              count: ex.row_count,
            })}
          </p>
        ))}
      {caution && !measurementRisk && (
        <p className="skeleton-evidence-line skeleton-evidence-caution">
          ⚠ {t('workbench:skeleton.evidence.measurementKeyCaution')}
        </p>
      )}
      {measurementRisk && (
        <p className="skeleton-evidence-line skeleton-evidence-caution">
          ⚠ {t('workbench:skeleton.evidence.riskMeasurementId', {
            columns: (measurementRisk.columns ?? []).join(', '),
          })}
        </p>
      )}
      {scopeRisk && (
        <p className="skeleton-evidence-line skeleton-evidence-caution">
          ⚠ {t('workbench:skeleton.evidence.riskScopeMissing', {
            parent: scopeRisk.parent_classes?.[0]
              ? displayCls(scopeRisk.parent_classes[0])
              : (scopeRisk.parent_map ?? ''),
            columns: (scopeRisk.parent_columns ?? []).join(', '),
          })}
        </p>
      )}
      {/* ZEM naming trap: the row class named after a measured key column
          ("Temperature" over key {Measurement temp.(C)}) — the row identity
          mislabeled as one of its measurements. */}
      {(ann.class_numeric_key_caution?.length ?? 0) > 0 && (
        <p className="skeleton-evidence-line skeleton-evidence-caution">
          ⚠{' '}
          {t('workbench:skeleton.evidence.classNumericKeyCaution', {
            cls: ann
              .class_numeric_key_caution!.map((c) => (displayClass ? displayClass(c.class) : c.class))
              .join(', '),
            column: ann.class_numeric_key_caution!.map((c) => c.column).join(', '),
          })}
        </p>
      )}
      {card ? (
        /* The consequence, not the syntax: ONE representative entity rendered
           as the card the mapping would build — real values, so "47 rows merge
           into this" reads as a fact, not a warning. Replaces the raw ID list
           (the ID is the card's title line). */
        <div className="skeleton-entity-card">
          <span className="skeleton-evidence-label">
            {cardCls
              ? t('workbench:skeleton.evidence.cardCount', {
                  count: card.entity_count,
                  cls: cardCls,
                })
              : t('workbench:skeleton.evidence.cardCountPlain', { count: card.entity_count })}
          </span>
          <div className="skeleton-entity-card-box">
            <div className="skeleton-entity-card-head">
              <code className="skeleton-evidence-id">{card.id}</code>
              {cardCls && <span className="skeleton-entity-class">{cardCls}</span>}
            </div>
            <table className="skeleton-entity-props">
              <tbody>
                {card.properties.map((p) =>
                  p.conflict ? (
                    <tr key={p.column} className="skeleton-entity-conflict">
                      <th scope="row">{p.column}</th>
                      <td>
                        <span className="skeleton-entity-conflict-note">
                          ⚠ {t('workbench:skeleton.evidence.cardConflict')}
                        </span>
                        {(p.values ?? []).map((v) => (
                          <div key={v.line} className="skeleton-entity-conflict-value">
                            {t('workbench:skeleton.evidence.cardConflictLine', {
                              value: v.value,
                              line: v.line,
                            })}
                          </div>
                        ))}
                        {(p.more_values ?? 0) > 0 && (
                          <div className="skeleton-entity-conflict-value">
                            {t('workbench:skeleton.evidence.cardConflictMore', {
                              count: p.more_values,
                            })}
                          </div>
                        )}
                      </td>
                    </tr>
                  ) : (
                    /* A value another map owns is drawn dimmed with its origin
                       (ADR column-ownership-and-growth G2). Without this the
                       child card silently repeats the parent's columns and
                       reads as "correct" — the singleton card explains its
                       missing per-row columns, so this side must explain its
                       extra ones or the asymmetry misleads. */
                    <tr key={p.column} className={p.owner_map ? 'skeleton-entity-borrowed' : undefined}>
                      <th scope="row">{p.column}</th>
                      <td>
                        {p.value}
                        {p.owner_map && (
                          <span className="skeleton-entity-owner">
                            {t('workbench:skeleton.evidence.cardFromParent', { map: p.owner_map })}
                          </span>
                        )}
                      </td>
                    </tr>
                  ),
                )}
                {card.omitted_columns > 0 && (
                  <tr>
                    <td colSpan={2} className="skeleton-entity-muted">
                      {t('workbench:skeleton.evidence.cardOmitted', {
                        count: card.omitted_columns,
                      })}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {singleton && (
            <p className="skeleton-evidence-line skeleton-evidence-muted">
              {t('workbench:skeleton.evidence.cardMerges', { rows: card.row_count })}
            </p>
          )}
          {!singleton && !collides && card.entity_count > 1 && (
            <p className="skeleton-evidence-line skeleton-evidence-muted">
              {t('workbench:skeleton.evidence.cardMore', { count: card.entity_count - 1 })}
            </p>
          )}
          {singleton && card.varying_columns.length > 0 && (
            <p className="skeleton-evidence-line skeleton-evidence-muted">
              {t('workbench:skeleton.evidence.cardVarying', {
                count: card.varying_columns.length,
                columns:
                  card.varying_columns.slice(0, 5).join(', ') +
                  (card.varying_columns.length > 5 ? ' …' : ''),
              })}
            </p>
          )}
          {/* The mirror of cardVarying: this card carries columns that are
              decided elsewhere. One line states it; the reasoning folds away —
              a wall of prose at the gate does not get read (real feedback). */}
          {borrowed.length > 0 && (
            <details className="skeleton-fold">
              <summary>
                {t('workbench:skeleton.evidence.cardBorrowed', {
                  count: borrowed.length,
                  map: borrowed[0].owner_map,
                })}
              </summary>
              <p className="skeleton-evidence-line skeleton-evidence-muted">
                {t('workbench:skeleton.evidence.cardBorrowedWhy', {
                  map: borrowed[0].owner_map,
                })}
              </p>
              <p className="skeleton-evidence-line skeleton-evidence-muted">
                {borrowed.map((b) => b.column).join(', ')}
              </p>
            </details>
          )}
        </div>
      ) : (
        (ann.id_previews?.length ?? 0) > 0 && (
          <div className="skeleton-evidence-previews">
            <span className="skeleton-evidence-label">
              {t('workbench:skeleton.evidence.previewHead', { n: ann.id_previews!.length })}
            </span>
            {ann.id_previews!.map((id, i) => (
              <code key={i} className="skeleton-evidence-id">
                {id}
              </code>
            ))}
          </div>
        )
      )}
      {/* What the NEXT file does to this design (ADR column-ownership-and-growth
          G3/G4). A file-scoped entity mints one per file, so "should this be
          split?" is answerable BEFORE the second file arrives — and once a
          sibling file exists, the overlap is measured instead of forecast. */}
      {/* Missing row-level kind: the card says its per-row columns "belong to
          the row-level kind" — when that kind does not exist, those values are
          dropped. State it once, in the kantan vocabulary, with the fix as a
          button (reading a paragraph and inferring an action is what people
          skip). */}
      {gap && (
        <div className="skeleton-gap">
          <p className="skeleton-evidence-line skeleton-evidence-bad">
            ⚠ {t('workbench:skeleton.evidence.gapHead', {
              columns: gap.columns.join(', '),
            })}
          </p>
          {onAddRowKind && (
            <button
              type="button"
              className="skeleton-gap-add"
              disabled={!canRevalidate}
              onClick={onAddRowKind}
            >
              {t('workbench:skeleton.evidence.gapAdd', {
                count: gap.entity_count,
                key: gap.suggested_key.map((c) => `{${c}}`).join(' + '),
              })}
            </button>
          )}
          {/* A button that adds something nothing can check is worse than no
              button: say WHY it is off instead of letting the click do half. */}
          {onAddRowKind && !canRevalidate && (
            <p className="skeleton-evidence-line skeleton-evidence-muted">
              {t('workbench:skeleton.evidence.gapNeedsFiles')}
            </p>
          )}
        </div>
      )}
      {/* One line on what the next file does; the reasoning folds away. Measured
          overlap (a sibling file already repeats a value) is the only part that
          earns the amber line — a forecast alone does not. */}
      {growth && growth.described_columns.length > 0 && (
        <details className="skeleton-fold skeleton-growth">
          <summary>
            {(growth.shared_values?.length ?? 0) > 0 ? (
              <span className="skeleton-evidence-warn">
                {t('workbench:skeleton.evidence.growthSharedHead', {
                  count: growth.shared_values!.length,
                })}
              </span>
            ) : (
              t('workbench:skeleton.evidence.growthHead')
            )}
          </summary>
          <p className="skeleton-evidence-line skeleton-evidence-muted">
            {t('workbench:skeleton.evidence.growthPerFile', { count: growth.source_count })}
          </p>
          <p className="skeleton-evidence-line skeleton-evidence-muted">
            {t('workbench:skeleton.evidence.growthDescribed', {
              count: growth.described_columns.length,
            })}
          </p>
          {(growth.shared_values?.length ?? 0) > 0 && (
            <p className="skeleton-evidence-line skeleton-evidence-muted">
              {growth
                .shared_values!.slice(0, 3)
                .map((s) =>
                  t('workbench:skeleton.evidence.growthSharedExample', {
                    column: s.column,
                    value: s.value,
                    files: s.files,
                  }),
                )
                .join(' / ')}
            </p>
          )}
        </details>
      )}
      {showCandidates && (
        <div className="skeleton-evidence-candidates">
          <span className="skeleton-evidence-label">
            {t('workbench:skeleton.evidence.candidatesHead')}
          </span>
          {candidateChips}
        </div>
      )}
      {singleton && candidateChips && (
        /* On a singleton the merge is (usually) the point — but when it ISN'T,
           the fix must stay one click away. Folded behind the explicit
           question so the normal case stays green and quiet. */
        <details className="skeleton-evidence-candidates skeleton-evidence-candidates--fold">
          <summary className="skeleton-evidence-label">
            {t('workbench:skeleton.evidence.candidatesSingletonHead')}
          </summary>
          {candidateChips}
        </details>
      )}
      {prefixWarning}
    </div>
  )
}

// Phase 2b human gate: the editable skeleton table. The user confirms/corrects
// the subject KEY (the single costliest error — a non-unique key collapses rows)
// and the CLASSES per map, then continues. Everything else (properties, prose) is
// generated only after this. Editing stays at the dict level; the confirmed dict
// is posted verbatim to /api/propose/continue. Each row carries deterministic
// EVIDENCE (server-computed, LLM-free) so the human judges data, not syntax.
export function SkeletonGate({
  skeleton,
  annotations,
  annotationsBusy,
  canRevalidate,
  busy,
  plain = false,
  onChange,
  onContinue,
  onDiscard,
  onRethink,
  titleKey = 'workbench:skeleton.gateTitle',
  hintKey = 'workbench:skeleton.gateHint',
  continueKey = 'workbench:skeleton.continue',
  continuingKey = 'workbench:skeleton.continuing',
  discardKey = 'workbench:skeleton.discard',
  discardConfirmKey = 'workbench:skeleton.discardConfirm',
}: {
  skeleton: MappingSkeleton
  annotations: SkeletonAnnotations | null
  annotationsBusy: boolean
  canRevalidate: boolean
  busy: boolean
  /** Kantan tier (ADR K4/K13): hide the raw prefix/namespace table — the
   *  namespace card (dataset name + issuer) is the whole story there. */
  plain?: boolean
  onChange: (s: MappingSkeleton) => void
  onContinue: () => void
  onDiscard: () => void
  /** When set, the gate offers "AI にもう一度考えさせる" with a free-text note
   *  (e.g. 「試料と測定値を別の種類に分けて」) that the caller feeds back into
   *  the skeleton generation — the AI-redo exit for a structurally wrong
   *  skeleton, next to the human-edit exit the table already is. */
  onRethink?: (note: string) => void
  /** i18n key overrides so the kantan tier can swap in plain-language copy.
   *  Defaults are the existing workbench strings (behavior unchanged). */
  titleKey?: string
  hintKey?: string
  continueKey?: string
  continuingKey?: string
  discardKey?: string
  discardConfirmKey?: string
}) {
  const { t } = useTranslation()
  // The optional rethink note (only rendered when onRethink is provided).
  const [rethinkNote, setRethinkNote] = useState('')
  // Two-step removal, and the just-added map (scroll target + brief highlight).
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null)
  const [addedMap, setAddedMap] = useState<string | null>(null)

  useEffect(() => {
    if (!addedMap) return
    document
      .querySelector(`[data-map="${CSS.escape(addedMap)}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const timer = window.setTimeout(() => setAddedMap(null), 2200)
    return () => window.clearTimeout(timer)
  }, [addedMap])

  function updateSubject(idx: number, patch: Partial<SkeletonMap['subject']>) {
    const maps = skeleton.maps.map((m, i) =>
      i === idx ? { ...m, subject: { ...m.subject, ...patch } } : m,
    )
    onChange({ ...skeleton, maps })
  }

  // Apply a proven-unique column combination: keep the template's fixed head
  // (up to the first placeholder), swap the key part. The re-check runs after,
  // so the human immediately sees the ✓ this candidate was promised to earn.
  function applyCandidate(idx: number, columns: string[]) {
    const current = skeleton.maps[idx]?.subject.template ?? ''
    const head = current.includes('{') ? current.slice(0, current.indexOf('{')) : `${current}/`
    updateSubject(idx, {
      template: head + columns.map((c) => `{${c}}`).join('/'),
    })
  }

  /** Add the row-level map the source is missing, right after its parent. The
   *  server suggested every field (name / template / key), so this is a pure
   *  state edit — the re-check then shows the new map's own evidence. */
  function addRowKind(idx: number) {
    const parent = skeleton.maps[idx]
    const gap = annotations?.maps?.[parent?.name]?.missing_row_kind
    if (!parent || !gap) return
    const added: SkeletonMap = {
      name: gap.suggested_name,
      source: parent.source,
      subject: { template: gap.suggested_template, classes: gap.suggested_classes ?? [] },
    }
    onChange({
      ...skeleton,
      maps: [...skeleton.maps.slice(0, idx + 1), added, ...skeleton.maps.slice(idx + 1)],
    })
    // Where the click landed: the new row appears BELOW, so scroll to it and
    // hold a highlight for a beat. An edit you cannot see reads as "nothing
    // happened" — the add button alone was not enough (real feedback).
    setAddedMap(added.name)
  }

  /** Remove a map. Being able to add but not remove made the gate a one-way
   *  door: a wrong split — the AI's or the one-click one — could not be taken
   *  back. Two-step like the other destructive controls, and never the last map
   *  (a skeleton with no maps cannot continue). */
  function removeMap(idx: number) {
    onChange({ ...skeleton, maps: skeleton.maps.filter((_, i) => i !== idx) })
    setConfirmRemove(null)
  }

  function updatePrefix(name: string, iri: string) {
    onChange({ ...skeleton, prefixes: { ...skeleton.prefixes, [name]: iri } })
  }

  // Namespaces minted on a placeholder domain (example.org & co) can never be
  // published — the server evidence names them; editing the IRI re-checks like
  // any key edit (ADR instance-iri-base.md).
  const placeholderPrefixes = annotations?.placeholder_prefixes ?? []
  const placeholderSet = new Set(placeholderPrefixes.map((p) => p.prefix))

  // The dataset's minted namespace pair (ADR K13). Detected straight from the
  // skeleton so the card reflects a rename instantly; whether the BASE is
  // operator-configured is the server annotation's call (Settings knowledge).
  const nsDetected = detectDatasetNamespace(skeleton)
  const baseUnconfigured = annotations?.dataset_namespace?.base_configured === false

  // The one naming judgment that persists: the dataset name inside the minted
  // IRI. Renaming cascades deterministically (IRI pair, derived prefix pair,
  // every CURIE in the maps) and re-checks like any other edit.
  function commitDatasetName(raw: string) {
    if (!nsDetected) return
    const next = renameDatasetNamespace(skeleton, nsDetected, raw)
    if (next) onChange(next)
  }

  // The raw prefix table (IRI editing per prefix) — the detail tier's escape
  // hatch, and the whole section when no minted pair is recognizable.
  const prefixRows = (
    <div className="skeleton-ns-rows">
      {Object.entries(skeleton.prefixes ?? {}).map(([name, iri]) => (
        <div key={name} className="skeleton-ns-row">
          <code className="skeleton-ns-prefix">{name}:</code>
          <input
            type="text"
            className="skeleton-gate-input"
            value={iri}
            disabled={busy}
            onChange={(e) => updatePrefix(name, e.target.value)}
          />
          {placeholderSet.has(name) && (
            <p className="skeleton-evidence-line skeleton-evidence-warn">
              {t('workbench:skeleton.ns.placeholderWarn')}
            </p>
          )}
        </div>
      ))}
    </div>
  )

  // Warn before continuing when the evidence says a key still collapses rows —
  // soft gate: the human can proceed (small collision counts can be legitimate,
  // e.g. deliberate dedup), but never unknowingly. A SINGLETON map (all rows →
  // one file-scoped entity) is the normal metadata pattern, not the accident —
  // it must not trip this confirm (older servers: fall back to is_unique).
  const collapsing = skeleton.maps.filter((m) => {
    const a = annotations?.maps?.[m.name]
    if (!a) return false
    return a.collapse_kind ? a.collapse_kind === 'partial' : a.is_unique === false
  })
  function onContinueGuarded() {
    if (placeholderPrefixes.length > 0) {
      const ok = window.confirm(
        t('workbench:skeleton.ns.confirmPlaceholder', {
          prefixes: placeholderPrefixes.map((p) => p.prefix).join(', '),
        }),
      )
      if (!ok) return
    }
    if (collapsing.length > 0) {
      const ok = window.confirm(
        t('workbench:skeleton.confirmCollides', {
          maps: collapsing.map((m) => m.name).join(', '),
        }),
      )
      if (!ok) return
    }
    onContinue()
  }

  return (
    <section className="skeleton-gate">
      <h4>{t(titleKey)}</h4>
      <p className="skeleton-gate-hint">{t(hintKey)}</p>
      {/* The skeleton at a glance: how many kinds, linked how. A one-box
          skeleton that should be two is visible here before any table reading. */}
      <div className="skeleton-diagram">
        <Mermaid chart={skeletonMermaid(skeleton, t('workbench:skeleton.diagram.edge'))} />
        <p className="skeleton-diagram-note">{t('workbench:skeleton.diagram.note')}</p>
      </div>
      {annotationsBusy && (
        <p className="skeleton-gate-revalidating" role="status">
          <span className="spinner" />
          {t('workbench:skeleton.evidence.revalidating')}
        </p>
      )}
      {!canRevalidate && (
        <p className="skeleton-gate-revalidating">{t('workbench:skeleton.evidence.reattach')}</p>
      )}
      {nsDetected ? (
        /* Namespace card (ADR K13): the ONE naming judgment — the dataset name
           inside the permanent ID — is the editable thing; the prefix pair and
           both IRIs derive from it mechanically. Base fixes route to Settings,
           never to a raw-IRI textbox. */
        <section className="skeleton-ns-card">
          <label className="skeleton-ns-name-label" htmlFor="skeleton-ns-name">
            {t('workbench:skeleton.ns.nameLabel')}
          </label>
          <div className="skeleton-ns-name-row">
            <input
              id="skeleton-ns-name"
              key={nsDetected.slug}
              type="text"
              className="skeleton-gate-input skeleton-ns-name-input"
              defaultValue={nsDetected.slug}
              disabled={busy}
              onBlur={(e) => commitDatasetName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
              }}
            />
            <code className="skeleton-ns-preview">
              {nsDetected.base}/datasets/{nsDetected.slug}/…
            </code>
          </div>
          <p className="skeleton-gate-hint">{t('workbench:skeleton.ns.nameHint')}</p>
          {baseUnconfigured && (
            <p className="skeleton-evidence-line skeleton-evidence-warn">
              {t('workbench:skeleton.ns.baseUnconfigured', { base: nsDetected.base })}
            </p>
          )}
          {!plain && (
            <details className="skeleton-ns" open={placeholderPrefixes.length > 0}>
              <summary>
                {t('workbench:skeleton.ns.advancedTitle')}
                {placeholderPrefixes.length > 0 && (
                  <span className="skeleton-ns-flag">
                    {t('workbench:skeleton.ns.flag', { count: placeholderPrefixes.length })}
                  </span>
                )}
              </summary>
              <p className="skeleton-gate-hint">{t('workbench:skeleton.ns.advancedNote')}</p>
              {prefixRows}
            </details>
          )}
        </section>
      ) : (
        /* Fallback (no recognizable minted pair, e.g. a restored legacy
           skeleton): the raw prefix table stays the escape hatch — even on the
           kantan tier, because a placeholder mint MUST stay visible/fixable. */
        <details className="skeleton-ns" open={placeholderPrefixes.length > 0}>
          <summary>
            {t('workbench:skeleton.ns.title')}
            {placeholderPrefixes.length > 0 && (
              <span className="skeleton-ns-flag">
                {t('workbench:skeleton.ns.flag', { count: placeholderPrefixes.length })}
              </span>
            )}
          </summary>
          <p className="skeleton-gate-hint">{t('workbench:skeleton.ns.hint')}</p>
          {prefixRows}
        </details>
      )}
      <div className="skeleton-gate-table-wrap">
        <table className="skeleton-gate-table">
          <thead>
            <tr>
              <th>{t('workbench:skeleton.colClass')}</th>
              <th>{t('workbench:skeleton.colSource')}</th>
              <th>{t('workbench:skeleton.colKey')}</th>
              <th>{t('workbench:skeleton.colClasses')}</th>
            </tr>
          </thead>
          <tbody>
            {skeleton.maps.map((m, idx) => {
              const usesConstant =
                m.subject.template === undefined && m.subject.constant !== undefined
              const keyValue = m.subject.template ?? m.subject.constant ?? ''
              // Kantan tier (K4/K13): the minted shorthand folds away at the
              // DISPLAY boundary only — `zemr:measurement/{…}` shows (and is
              // edited) as `measurement/{…}`, bare class names get the minted
              // prefix back on the way in. The skeleton state keeps full
              // CURIEs, so evidence/continue see detail-tier values.
              const displayKey = plain ? compactTemplate(keyValue, nsDetected) : keyValue
              const ann = annotations?.maps?.[m.name]
              return (
                <Fragment key={m.name}>
                  <tr
                    data-map={m.name}
                    className={[
                      ann ? 'skeleton-gate-row' : '',
                      addedMap === m.name ? 'skeleton-gate-row--added' : '',
                    ]
                      .filter(Boolean)
                      .join(' ') || undefined}
                  >
                    <td className="skeleton-gate-name">
                      {m.name}
                      {/* Removal is the other half of "add": without it the gate
                          is a one-way door. Two-step, and never the last map. */}
                      {skeleton.maps.length > 1 &&
                        (confirmRemove === m.name ? (
                          <span className="skeleton-remove-confirm">
                            <button
                              type="button"
                              className="skeleton-remove skeleton-remove--yes"
                              disabled={busy}
                              onClick={() => removeMap(idx)}
                            >
                              {t('workbench:skeleton.removeConfirm')}
                            </button>
                            <button
                              type="button"
                              className="skeleton-remove"
                              onClick={() => setConfirmRemove(null)}
                            >
                              {t('workbench:skeleton.removeCancel')}
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            className="skeleton-remove"
                            disabled={busy}
                            title={t('workbench:skeleton.remove')}
                            onClick={() => setConfirmRemove(m.name)}
                          >
                            {t('workbench:skeleton.remove')}
                          </button>
                        ))}
                    </td>
                    <td className="skeleton-gate-source">{m.source}</td>
                    <td>
                      {/* A full IRI template rarely fits one line — wrap it
                          (rows grow with content) so the tail is never cut off. */}
                      <textarea
                        className="skeleton-gate-input skeleton-gate-key"
                        value={displayKey}
                        rows={Math.max(1, Math.ceil(displayKey.length / 48))}
                        disabled={busy}
                        title={m.note ?? undefined}
                        onChange={(e) => {
                          const raw = e.target.value.replace(/\n/g, '')
                          const value = plain ? expandTemplate(raw, nsDetected) : raw
                          updateSubject(
                            idx,
                            usesConstant ? { constant: value } : { template: value },
                          )
                        }}
                      />
                      {m.note && <div className="skeleton-gate-note">{m.note}</div>}
                    </td>
                    <td>
                      <input
                        type="text"
                        className="skeleton-gate-input"
                        value={(m.subject.classes ?? [])
                          .map((c) => (plain ? compactClass(c, nsDetected) : c))
                          .join(', ')}
                        disabled={busy}
                        onChange={(e) =>
                          updateSubject(idx, {
                            classes: e.target.value
                              .split(',')
                              .map((s) => s.trim())
                              .filter(Boolean)
                              .map((c) => (plain ? expandClass(c, nsDetected) : c)),
                          })
                        }
                      />
                    </td>
                  </tr>
                  {ann && (
                    <tr className="skeleton-evidence-row">
                      <td colSpan={4}>
                        <SkeletonEvidence
                          ann={ann}
                          onApplyCandidate={(cols) => applyCandidate(idx, cols)}
                          onAddRowKind={() => addRowKind(idx)}
                          canRevalidate={canRevalidate}
                          displayClass={
                            plain ? (c) => compactClass(c, nsDetected) : undefined
                          }
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      {/* AI-redo exit: when the skeleton is STRUCTURALLY wrong (wrong split
          into kinds, wrong key idea), editing cells is the wrong tool — hand
          a plain-language note back to the generation instead. */}
      {/* When the sources are gone the AI cannot be re-run, so the caller stops
          passing onRethink and this whole exit used to VANISH silently — the
          same "it disappeared and nobody said why" that misleads elsewhere in
          this gate. Keep the block, disabled, with the reason. */}
      {!onRethink && !canRevalidate && (
        <div className="skeleton-rethink">
          <p className="skeleton-gate-hint">{t('workbench:skeleton.rethink.needsFiles')}</p>
        </div>
      )}
      {onRethink && (
        <div className="skeleton-rethink">
          <label className="skeleton-gate-hint" htmlFor="skeleton-rethink-note">
            {t('workbench:skeleton.rethink.label')}
          </label>
          <textarea
            id="skeleton-rethink-note"
            className="skeleton-rethink-note"
            rows={2}
            placeholder={t('workbench:skeleton.rethink.placeholder')}
            value={rethinkNote}
            disabled={busy}
            onChange={(e) => setRethinkNote(e.target.value)}
          />
          <div className="skeleton-gate-actions">
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => onRethink(rethinkNote.trim())}
              disabled={busy}
            >
              {t('workbench:skeleton.rethink.button')}
            </button>
          </div>
        </div>
      )}
      <div className="skeleton-gate-actions">
        <button onClick={onContinueGuarded} disabled={busy}>
          {busy ? (
            <>
              <span className="spinner" />
              {t(continuingKey)}
            </>
          ) : (
            t(continueKey)
          )}
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => {
            if (window.confirm(t(discardConfirmKey))) onDiscard()
          }}
          disabled={busy}
        >
          {t(discardKey)}
        </button>
      </div>
    </section>
  )
}
