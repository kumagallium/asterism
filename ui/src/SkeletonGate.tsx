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
  resolveUndeclaredPrefixes,
  slugifyDatasetName,
  STANDARD_VOCAB_IRIS,
} from './datasetNamespace'
import { Mermaid } from './Mermaid'
import { skeletonMermaid } from './skeletonDiagram'

// (Moved verbatim from WorkbenchView.tsx so the kantan wizard shares the gate.
//  Only addition: the title/hint/continue labels are overridable via *Key props
//  so the kantan tier can show plain-language copy — defaults keep the exact
//  workbench strings, so the detail tier is byte-identical.)

// Ghost rows on one card, before the rest fold into a count. A card that
// delegates 40 measurement columns should still read as a card.
const _GHOST_ROWS = 6

// Column names spelled out in the "nowhere to put these" warning before the
// rest fold into a count (a 40-column instrument file made that line a wall).
const _GAP_COLUMNS = 5

/** The first few column names plus a count for the rest, as ONE interpolated
 *  value — appending the count AFTER the sentence broke its grammar in
 *  Japanese ("… を入れる種類がありません …ほか 35 列") and, worse, the confirm
 *  block dropped the count entirely: naming 5 of 40 columns understates what
 *  continuing costs, in the one place the human decides to accept that cost.
 *  The full list stays one hover away (the caller puts it in a `title`). */
function columnsSummary(columns: string[], more: (count: number) => string): string {
  const head = columns.slice(0, _GAP_COLUMNS).join(', ')
  const rest = columns.length - _GAP_COLUMNS
  return rest > 0 ? `${head} ${more(rest)}` : head
}

// Consecutive blocks under one boundary line, in first-seen order. A card can
// in principle borrow from two parents — that stays two blocks rather than one
// averaged claim.
function groupByOwner<T>(items: T[], owner: (item: T) => string | undefined) {
  const groups: { owner: string | undefined; items: T[] }[] = []
  for (const item of items) {
    const key = owner(item)
    const existing = groups.find((g) => g.owner === key)
    if (existing) existing.items.push(item)
    else groups.push({ owner: key, items: [item] })
  }
  return groups
}

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
  onSplit,
  canRevalidate = true,
  displayClass,
  plain = false,
  reading,
  displayMap,
  shortId,
  filesGoneText,
  suggestedClass,
  onUseSuggestedClass,
  onFixColumn,
}: {
  ann: SkeletonMapAnnotation
  onApplyCandidate: (columns: string[]) => void
  /** Add the row-level map this source is missing (one click, server-suggested). */
  onAddRowKind?: () => void
  /** Split a shared concept out of this file-scoped map: the checked columns
   *  become their own kind, keyed by `key` (ADR column-ownership G15). */
  onSplit?: (columns: string[], key: string) => void
  /** False when the sources are gone (restored session / reload): the edit
   *  cannot be re-checked, so the one-click add would land unverified. */
  canRevalidate?: boolean
  /** Kantan tier: fold the minted prefix out of class names in evidence copy
   *  (the annotation carries full CURIEs). Absent on the detail tier. */
  displayClass?: (value: string) => string
  /** Kantan tier: plain-language copy, no template syntax. */
  plain?: boolean
  /** "1 row = one …" — the reading of this map in the human's words (K7). */
  reading?: string
  /** Another map's INTERNAL name → what the human sees for it (K4/GATE-05). */
  displayMap?: (name: string) => string
  /** Full minted IRI → its readable tail (the dataset's resource base folds). */
  shortId?: (iri: string) => string
  /** One shared sentence for every "the files are gone" dead end (GATE-26). */
  filesGoneText?: string
  /** A machine-derived row-class name offered when the AI named the row class
   *  after a measured column (the ZEM trap) — one tap, no typing. */
  suggestedClass?: string
  onUseSuggestedClass?: () => void
  /** Replace a column name the AI invented with the closest real one. */
  onFixColumn?: (wrong: string, right: string) => void
}) {
  const { t } = useTranslation()
  // The split control (G15): which described columns name one shared thing,
  // and which of them is its identity. Pre-filled from what sibling files
  // already agree on; with one file nothing is checked — only the offer.
  // (Hooks first — this component returns early for an uncheckable map.)
  const [splitCols, setSplitCols] = useState<string[]>(
    () => ann.growth_preview?.split_default?.columns ?? [],
  )
  const [splitKey, setSplitKey] = useState<string>(
    () => ann.growth_preview?.split_default?.key ?? '',
  )

  const showId = (iri: string) => (shortId ? shortId(iri) : iri)
  const mapName = (name: string | undefined) =>
    name ? (displayMap ? displayMap(name) : name) : ''
  // The one sentence the human came to confirm ("1 行 = 1 つの peak"). It is the
  // heading of the block, not a line inside it: the kind name is what the reader
  // recognises from their own bench, and everything below is evidence FOR it.
  const readingLine = reading && <p className="skeleton-evidence-reading">{reading}</p>
  // The kantan tier states the same thing once, at gate level, in plain words
  // (the deterministic repair usually makes it moot) — the raw prefix list is
  // detail-tier copy for a table the kantan tier does not show.
  const prefixWarning = !plain && ann.undeclared_prefixes.length > 0 && (
    <p className="skeleton-evidence-line skeleton-evidence-warn">
      {t('workbench:skeleton.evidence.undeclaredPrefixes', {
        prefixes: ann.undeclared_prefixes.join(', '),
      })}
    </p>
  )
  // Proven-unique column combinations. Defined before the uncheckable early
  // return: a map whose key names a column that does not exist gets the same
  // one-tap chips (the fix is the point, not the diagnosis).
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
          {/* Braces are template syntax; the kantan tier reads column names. */}
          {c.columns.map((col) => (plain ? col : `{${col}}`)).join(' + ')}
          {c.measurement_only && ' ⚠'}
        </button>
      ))}
    </>
  )

  if (!ann.checkable) {
    // Newer servers name the closest REAL column for each invented one; the
    // field is optional, so read it defensively (older servers send nothing).
    const columnSuggestions =
      (ann as { column_suggestions?: { column: string; suggestions: string[] }[] })
        .column_suggestions ?? []
    return (
      <div className="skeleton-evidence">
        {readingLine}
        <p className="skeleton-evidence-line skeleton-evidence-muted">
          {t(
            plain && ann.reason === 'missing-columns'
              ? 'skeletongate:missingColumns'
              : evidenceReasonKey(ann.reason),
            { columns: (ann.missing_columns ?? []).join(', ') },
          )}
        </p>
        {ann.reason === 'constant' && ann.expanded_template && (
          <p className="skeleton-evidence-line skeleton-evidence-muted">
            <code className="skeleton-evidence-id" title={ann.expanded_template}>
              {showId(ann.expanded_template)}
            </code>
          </p>
        )}
        {ann.reason === 'missing-columns' && onFixColumn && (
          <div className="skeleton-evidence-candidates">
            {columnSuggestions
              .filter((s) => s.suggestions.length > 0)
              .map((s) => (
                <button
                  key={s.column}
                  type="button"
                  className="skeleton-candidate-chip"
                  onClick={() => onFixColumn(s.column, s.suggestions[0])}
                >
                  {t('skeletongate:fixColumn', {
                    wrong: s.column,
                    suggestion: s.suggestions[0],
                  })}
                </button>
              ))}
          </div>
        )}
        {ann.reason === 'missing-columns' && candidateChips && (
          <div className="skeleton-evidence-candidates">
            <span className="skeleton-evidence-label">
              {t('workbench:skeleton.evidence.candidatesHead')}
            </span>
            {candidateChips}
          </div>
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
  // Where each column this card CANNOT carry will live (G12). The card names
  // them either way (`varying_columns`); the destination is what turns "not
  // here" into "over there".
  const delegatedOwner = new Map(
    (ann.delegated_columns ?? []).map((d) => [d.column, d.owner_map] as const),
  )
  const growth = ann.growth_preview
  const gap = ann.missing_row_kind
  const displayCls = (value: string) => (displayClass ? displayClass(value) : value)
  const cardCls = (ann.expanded_classes[0]?.curie && displayCls(ann.expanded_classes[0].curie)) || ''
  // Non-singleton: chips show whenever something is wrong (collision, caution,
  // risk). Singleton: merging is normal, so the chips fold behind the explicit
  // "should this be one record per row?" question instead.
  const showCandidates = !singleton && (collides || caution || risks.length > 0) && candidateChips
  // The card's three ownership blocks (G12): what it carries, what another map
  // owns, and what it cannot carry at all.
  const cardProps = card?.properties ?? []
  const ownProps = cardProps.filter((p) => !p.owner_map)
  const borrowedGroups = groupByOwner(
    cardProps.filter((p) => !!p.owner_map),
    (p) => p.owner_map,
  )
  const ghostCols = (card?.varying_columns ?? []).slice(0, _GHOST_ROWS)
  const ghostMore = (card?.varying_columns.length ?? 0) - ghostCols.length
  const ghostGroups = groupByOwner(ghostCols, (col) => delegatedOwner.get(col))
  const renderProp = (p: (typeof cardProps)[number], rowClass?: string) =>
    p.conflict ? (
      <tr key={p.column} className="skeleton-entity-conflict">
        <th scope="row">{p.column}</th>
        <td>
          <span className="skeleton-entity-conflict-note">
            ⚠ {t('workbench:skeleton.evidence.cardConflict')}
          </span>
          {(p.values ?? []).map((v) => (
            <div key={v.line} className="skeleton-entity-conflict-value">
              {t('workbench:skeleton.evidence.cardConflictLine', { value: v.value, line: v.line })}
            </div>
          ))}
          {(p.more_values ?? 0) > 0 && (
            <div className="skeleton-entity-conflict-value">
              {t('workbench:skeleton.evidence.cardConflictMore', { count: p.more_values })}
            </div>
          )}
        </td>
      </tr>
    ) : (
      <tr key={p.column} className={rowClass}>
        <th scope="row">{p.column}</th>
        <td>{p.value}</td>
      </tr>
    )
  const headLines = (
    <>
      {readingLine}
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
    </>
  )

  const body = (
    <>
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
              : mapName(scopeRisk.parent_map),
            columns: (scopeRisk.parent_columns ?? []).join(', '),
          })}
        </p>
      )}
      {/* ZEM naming trap: the row class named after a measured key column
          ("Temperature" over key {Measurement temp.(C)}) — the row identity
          mislabeled as one of its measurements. */}
      {(ann.class_numeric_key_caution?.length ?? 0) > 0 && (
        <>
          <p className="skeleton-evidence-line skeleton-evidence-caution">
            ⚠{' '}
            {t('workbench:skeleton.evidence.classNumericKeyCaution', {
              cls: ann
                .class_numeric_key_caution!.map((c) => (displayClass ? displayClass(c.class) : c.class))
                .join(', '),
              column: ann.class_numeric_key_caution!.map((c) => c.column).join(', '),
            })}
          </p>
          {/* Naming the row kind is world knowledge, but the machine already
              knows a defensible default (this map's own name) — offer it as
              one tap instead of asking for a typed identifier. */}
          {suggestedClass && onUseSuggestedClass && (
            <div className="skeleton-evidence-candidates">
              <button
                type="button"
                className="skeleton-candidate-chip"
                onClick={onUseSuggestedClass}
              >
                {t('skeletongate:suggestClass', { suggested: suggestedClass })}
              </button>
            </div>
          )}
        </>
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
              {/* The readable tail is the point; the full minted IRI stays one
                  hover away (a URL as a card title buries the ID). */}
              <code className="skeleton-evidence-id" title={card.id}>
                {showId(card.id)}
              </code>
              {cardCls && <span className="skeleton-entity-class">{cardCls}</span>}
            </div>
            {/* Ownership is POSITION + a boundary line, not dimming: the old
                greyed row used the same --faint as the omitted-columns line, so
                "this value is the parent's" read as "skipped". The value itself
                is real — what differs is where it belongs (ADR G12). */}
            <table className="skeleton-entity-props">
              <tbody>
                {ownProps.map((p) => renderProp(p))}
                {borrowedGroups.map((g) => (
                  <Fragment key={`borrowed:${g.owner}`}>
                    <tr className="skeleton-entity-band">
                      <td colSpan={2}>
                        ↓{' '}
                        {t('workbench:skeleton.evidence.cardBorrowedBand', {
                          map: mapName(g.owner),
                          count: card.entity_count,
                        })}
                      </td>
                    </tr>
                    {g.items.map((p) => renderProp(p, 'skeleton-entity-borrowed'))}
                  </Fragment>
                ))}
                {/* Real columns the card cap left out — before the ghost block,
                    since those are values the card DOES carry. */}
                {card.omitted_columns > 0 && (
                  <tr>
                    <td colSpan={2} className="skeleton-entity-muted">
                      {t('workbench:skeleton.evidence.cardOmitted', {
                        count: card.omitted_columns,
                      })}
                    </td>
                  </tr>
                )}
                {/* The mirror block: columns this card cannot carry, drawn as
                    ghost rows so the parent states its absence in the same
                    shape the child states its extras. */}
                {ghostGroups.map((g) => (
                  <Fragment key={`delegated:${g.owner ?? ''}`}>
                    <tr className="skeleton-entity-band">
                      <td colSpan={2}>
                        ↓{' '}
                        {g.owner
                          ? t('workbench:skeleton.evidence.cardDelegatedBand', {
                              map: mapName(g.owner),
                            })
                          : t('workbench:skeleton.evidence.cardDelegatedBandPlain')}
                      </td>
                    </tr>
                    {g.items.map((col) => (
                      <tr key={col} className="skeleton-entity-delegated">
                        <th scope="row">{col}</th>
                        <td className="skeleton-entity-ghost-value">
                          {t('workbench:skeleton.evidence.cardVaries')}
                        </td>
                      </tr>
                    ))}
                  </Fragment>
                ))}
                {ghostMore > 0 && (
                  <tr className="skeleton-entity-delegated">
                    <td colSpan={2} className="skeleton-entity-muted">
                      {t('workbench:skeleton.evidence.cardOmitted', { count: ghostMore })}
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
          {/* The boundary line inside the table already states WHAT; this holds
              only the WHY, folded — a wall of prose at the gate does not get
              read (real feedback). The column list is gone: the rows are it. */}
          {borrowed.length > 0 && (
            <details className="skeleton-fold">
              <summary>
                {t('workbench:skeleton.evidence.cardBorrowedWhyHead', {
                  map: mapName(borrowed[0].owner_map),
                })}
              </summary>
              <p className="skeleton-evidence-line skeleton-evidence-muted">
                {t('workbench:skeleton.evidence.cardBorrowedWhy', {
                  map: mapName(borrowed[0].owner_map),
                })}
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
              <code key={i} className="skeleton-evidence-id" title={id}>
                {showId(id)}
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
          {/* 40 measurement columns in one sentence is a wall the eye skips —
              and the one-tap fix sits right under it. Name a few, count the
              rest, keep the full list one hover away. */}
          <p className="skeleton-evidence-line skeleton-evidence-bad" title={gap.columns.join(', ')}>
            ⚠ {t('workbench:skeleton.evidence.gapHead', {
              columns: columnsSummary(gap.columns, (count) =>
                t('workbench:skeleton.evidence.cardOmitted', { count }),
              ),
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
                key: gap.suggested_key.map((c) => (plain ? c : `{${c}}`)).join(' + '),
              })}
            </button>
          )}
          {/* A button that adds something nothing can check is worse than no
              button: say WHY it is off instead of letting the click do half.
              One shared sentence for every "the files are gone" dead end. */}
          {onAddRowKind && !canRevalidate && (
            <p className="skeleton-evidence-line skeleton-evidence-muted">
              {filesGoneText ?? t('workbench:skeleton.evidence.gapNeedsFiles')}
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
          {/* "These name one thing — make it its own kind." The human ticks
              WHICH columns (world knowledge); the machine pre-ticks the ones
              the files already agree on and picks the identity-like key. The
              measured overlap above shows the evidence; this is the action. */}
          {onSplit && (
            <div className="skeleton-split">
              <p className="skeleton-evidence-line skeleton-evidence-muted">
                {t('workbench:skeleton.evidence.splitLead')}
              </p>
              <div className="skeleton-split-cols">
                {growth.described_columns.map((col) => (
                  <label key={col} className="skeleton-split-col">
                    <input
                      type="checkbox"
                      checked={splitCols.includes(col)}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? [...splitCols, col]
                          : splitCols.filter((c) => c !== col)
                        setSplitCols(next)
                        // The key must be one of the checked columns.
                        if (!next.includes(splitKey)) setSplitKey(next[0] ?? '')
                      }}
                    />
                    <span>{col}</span>
                  </label>
                ))}
              </div>
              <div className="skeleton-split-row">
                <label className="skeleton-split-keylabel">
                  {t('workbench:skeleton.evidence.splitKey')}
                  <select
                    className="skeleton-split-key"
                    value={splitKey}
                    disabled={splitCols.length === 0}
                    onChange={(e) => setSplitKey(e.target.value)}
                  >
                    {splitCols.length === 0 && <option value="">—</option>}
                    {splitCols.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="skeleton-gap-add"
                  disabled={!canRevalidate || splitCols.length === 0 || !splitKey}
                  onClick={() => onSplit(splitCols, splitKey)}
                >
                  {t('workbench:skeleton.evidence.splitAdd', { count: splitCols.length })}
                </button>
              </div>
            </div>
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
      {!singleton && !showCandidates && candidateChips && (
        /* A key can be WRONG without being broken: `{2theta}` and `{(hkl)}` are
           both unique here, so nothing goes amber and the chips used to stay
           hidden — leaving a person who wanted the other one to edit the ID
           template by hand. Same treatment as the singleton case: available,
           folded, so the green path stays quiet (2026-08-19 review). */
        <details className="skeleton-evidence-candidates skeleton-evidence-candidates--fold">
          <summary className="skeleton-evidence-label">
            {t('workbench:skeleton.evidence.candidatesQuietHead')}
          </summary>
          {candidateChips}
        </details>
      )}
      {prefixWarning}
    </>
  )

  // The evidence stays OPEN, including for a map with nothing amber. Folding it
  // was tried and failed on the person it was for: with the card hidden, the row
  // above ("ファイル / ID の決まりかた / 1 行が表すもの") carries too little to
  // judge with, so "確かめる" has nothing to confirm. K7 asks that a green map may
  // fold; the reading line alone turned out not to be enough to act on.
  return (
    <div className="skeleton-evidence">
      {headLines}
      {body}
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
  onOpenSettings,
  titleKey = 'workbench:skeleton.gateTitle',
  hintKey = 'workbench:skeleton.gateHint',
  continueKey = 'workbench:skeleton.continue',
  continuingKey = 'workbench:skeleton.continuing',
  discardKey = 'workbench:skeleton.discard',
  discardConfirmKey = 'workbench:skeleton.discardConfirm',
  filesGoneKey = 'skeletongate:filesGone',
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
  /** When set, the "the ID issuer is still provisional" note gets a way to act
   *  on it. Without it the note used to be a dead end: a warning band with no
   *  button and a tab name only the detail tier knows (GATE-13). Personal
   *  installs can open the setting themselves; on a shared server the note
   *  already says to ask the administrator. */
  onOpenSettings?: () => void
  /** i18n key overrides so the kantan tier can swap in plain-language copy.
   *  Defaults are the existing workbench strings (behavior unchanged). */
  titleKey?: string
  hintKey?: string
  continueKey?: string
  continuingKey?: string
  discardKey?: string
  discardConfirmKey?: string
  /** One sentence for every "the files are gone, so this is off" dead end —
   *  it must name the way back (the discard button), which the three separate
   *  detail-tier sentences never did. Kantan default; the detail tier keeps
   *  its own per-place wording. */
  filesGoneKey?: string
}) {
  const { t } = useTranslation()
  // The optional rethink note (only rendered when onRethink is provided).
  const [rethinkNote, setRethinkNote] = useState('')
  // Two-step removal, and the just-added map (scroll target + brief highlight).
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null)
  const [addedMap, setAddedMap] = useState<string | null>(null)
  // A dataset name that cleans away to nothing (a Japanese one) used to do
  // NOTHING at all — the field kept the typed text and the ID kept the old
  // slug. Say what an ID may contain, right under the field.
  const [nameInvalid, setNameInvalid] = useState(false)
  // Kantan tier: the ID-shape card opens only when the human asks (or when the
  // issuer is still provisional and they must be told).
  const [nsOpen, setNsOpen] = useState(false)
  // "Are you sure?" as an inline block instead of window.confirm (whose Enter
  // key means "proceed with the collision" — the one default K7 forbids).
  const [confirming, setConfirming] = useState(false)
  // What the deterministic vocabulary repair did, kept after the re-check that
  // makes the server warning disappear: a silent meaning change is worse than
  // the warning it replaces.
  const [vocabFix, setVocabFix] = useState<{
    declared: string[]
    declaredOwn: string[]
    renamed: string[]
  } | null>(null)

  useEffect(() => {
    if (!addedMap) return
    const row = document.querySelector(`[data-map="${CSS.escape(addedMap)}"]`)
    row?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    // A machine-added map carries a placeholder class (named after its key
    // column — "Name" is not a kind of thing). Put the caret in that field,
    // text selected: "name this" without a sentence, and typing replaces it.
    // On the kantan tier that silent jump reads as "something broke": the
    // caret lands on an English identifier with no instruction, so the row
    // gets a sentence instead and the field is left alone.
    if (!plain) {
      const cls = row?.querySelector<HTMLInputElement>('td:last-child input')
      if (cls) {
        cls.focus({ preventScroll: true })
        cls.select()
      }
    }
    const timer = window.setTimeout(() => setAddedMap(null), 2200)
    return () => window.clearTimeout(timer)
  }, [addedMap, plain])

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

  /** Swap a column name the AI invented for the closest REAL one (server-
   *  measured suggestion). Placeholder-level replacement: the rest of the ID
   *  recipe — its head, its other columns — is left exactly as it was. */
  function fixColumnName(idx: number, wrong: string, right: string) {
    const current = skeleton.maps[idx]?.subject.template
    if (current === undefined) return
    updateSubject(idx, { template: current.split(`{${wrong}}`).join(`{${right}}`) })
  }

  /** The row-class name a map's OWN name suggests (`sample_detail` →
   *  `SampleDetail`), in the dataset's own vocabulary. Used to offer a one-tap
   *  escape from the ZEM trap (a row class named after a measured column). */
  function suggestedClassFor(idx: number): string | null {
    const m = skeleton.maps[idx]
    if (!m) return null
    const pascal = m.name
      .split(/[^0-9A-Za-z]+/)
      .filter(Boolean)
      .map((w) => w[0].toUpperCase() + w.slice(1))
      .join('')
    if (!pascal || /^[0-9]/.test(pascal)) return null
    const current = m.subject.classes ?? []
    const prefix = current[0]?.includes(':')
      ? current[0].slice(0, current[0].indexOf(':') + 1)
      : nsDetected?.ontology_prefix
        ? `${nsDetected.ontology_prefix}:`
        : ''
    const curie = `${prefix}${pascal}`
    return current.includes(curie) ? null : curie
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

  /** Split a shared concept out of a file-scoped map (ADR G15): a new map on
   *  the same source, keyed by the human's identity column, carrying `owns` =
   *  the columns they ticked. Named after the key so the row reads as what it
   *  is; a starter class in the parent's vocabulary (renamable, like the rest). */
  function splitConcept(idx: number, columns: string[], key: string) {
    const parent = skeleton.maps[idx]
    if (!parent || columns.length === 0 || !key) return
    const template = parent.subject.template ?? ''
    const cut = template.indexOf(parent.name)
    const head = cut >= 0 ? template.slice(0, cut) : template.slice(0, template.indexOf('{'))
    const base = key.toLowerCase().replace(/[^0-9a-z]+/g, '_').replace(/^_+|_+$/g, '') || 'shared'
    let name = base
    for (let i = 2; skeleton.maps.some((m) => m.name === name); i += 1) name = `${base}${i}`
    const pascal = name
      .split('_')
      .filter(Boolean)
      .map((w) => w[0].toUpperCase() + w.slice(1))
      .join('')
    const parentClass = parent.subject.classes?.[0] ?? ''
    const clsPrefix = parentClass.includes(':') ? parentClass.slice(0, parentClass.indexOf(':') + 1) : ''
    const added: SkeletonMap = {
      name,
      source: parent.source,
      subject: { template: `${head}${name}/{${key}}`, classes: clsPrefix ? [`${clsPrefix}${pascal}`] : [] },
      owns: columns,
    }
    onChange({
      ...skeleton,
      maps: [...skeleton.maps.slice(0, idx + 1), added, ...skeleton.maps.slice(idx + 1)],
    })
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
  // Detected straight from the skeleton, with the SERVER's own reading as the
  // fallback: a skeleton whose minted pair is declared in a non-canonical shape
  // (or not at all) detects as nothing here, and the deterministic vocabulary
  // repair below then had no namespace to work from — so the one screen that
  // could fix a missing prefix offered only "AI にもう一度考えさせる" (live
  // 2026-08-19). The server annotation knows the slug and base regardless.
  const nsDetected = detectDatasetNamespace(skeleton) ?? annotations?.dataset_namespace ?? null
  const baseUnconfigured = annotations?.dataset_namespace?.base_configured === false

  // The one naming judgment that persists: the dataset name inside the minted
  // IRI. Renaming cascades deterministically (IRI pair, derived prefix pair,
  // every CURIE in the maps) and re-checks like any other edit.
  function commitDatasetName(input: HTMLInputElement) {
    if (!nsDetected) return
    const raw = input.value
    const next = renameDatasetNamespace(skeleton, nsDetected, raw)
    if (next) {
      setNameInvalid(false)
      onChange(next)
      return
    }
    // Nothing changed: either the same name (fine) or a name that cleans away
    // to nothing — a Japanese one, which is what this field invites. Silence
    // there reads as a broken field, so put the rule under it and restore the
    // ID the dataset actually has.
    const empty = raw.trim() !== '' && slugifyDatasetName(raw) === ''
    setNameInvalid(empty)
    if (empty) input.value = nsDetected.slug
  }

  // Kantan tier (K4/GATE-05): one kind, one name. The internal map name
  // (`sample_detail`) is machine bookkeeping — the human sees the kind, and
  // only falls back to the internal name when the kind cannot name it alone.
  const displayMapName = (name: string): string => {
    if (!plain) return name
    const m = skeleton.maps.find((x) => x.name === name)
    const cls = m?.subject.classes?.[0]
    if (!cls) return name
    const shown = compactClass(cls, nsDetected)
    const twin = skeleton.maps.some(
      (x) => x.name !== name && compactClass(x.subject.classes?.[0] ?? '', nsDetected) === shown,
    )
    return twin || !shown ? name : shown
  }

  // A minted ID reads as its tail; the issuer part is the same on every card.
  const resourceBase = nsDetected ? `${nsDetected.base}/datasets/${nsDetected.slug}/resource/` : ''
  const shortId = (iri: string): string =>
    plain && resourceBase && iri.startsWith(resourceBase) ? iri.slice(resourceBase.length) : iri

  // "1 row = one …" (K7): the reading of a map in the human's words. Without a
  // human-readable label from the AI, the kind's own (folded) name is it — and
  // with no kind at all (the human cleared the field), the map's own name, the
  // same last resort displayMapName uses. K7 wants this line ALWAYS.
  function readingFor(m: SkeletonMap, ann: SkeletonMapAnnotation | undefined): string | undefined {
    if (!plain) return undefined
    const label = compactClass(m.subject.classes?.[0] ?? '', nsDetected) || m.name
    if (!label) return undefined
    const kind = ann?.collapse_kind
    if (kind === 'singleton') return t('skeletongate:reading.singleton', { label })
    if (kind === 'partial') return t('skeletongate:reading.partial', { label })
    if (kind === 'unique') return t('skeletongate:reading.unique', { label })
    return t('skeletongate:reading.plain', { label })
  }

  // One sentence for every dead end the missing sources cause, naming the way
  // back — the three detail-tier sentences each described their own control.
  const filesGoneText = plain ? t(filesGoneKey, { back: t(discardKey) }) : undefined

  // A prefix the design USES but never DECLARES is what a weak model reliably
  // produces (`schema:Dataset`, no declaration) — and the server says plainly
  // that it will fail on save. The kantan tier has no prefix table to fix it
  // in, so the fix is one tap of deterministic machinery: known vocabularies
  // get their real IRI, anything else becomes a word of this dataset. Never a
  // guessed IRI, and never silent — the button says it, the report keeps it.
  const undeclared = [
    ...new Set(Object.values(annotations?.maps ?? {}).flatMap((a) => a.undeclared_prefixes ?? [])),
  ].sort()
  const vocabFixable =
    undeclared.length > 0 &&
    (undeclared.some((p) => p in STANDARD_VOCAB_IRIS) ||
      !!(nsDetected?.ontology_prefix || nsDetected?.resource_prefix) ||
      // Neither half declared yet, but the dataset's namespace is known: the
      // undeclared name may BE one of the two the slug derives, in which case
      // writing it down is the whole repair.
      !!(nsDetected?.slug && nsDetected?.base))
  function repairVocabulary() {
    const fixed = resolveUndeclaredPrefixes(skeleton, undeclared, nsDetected)
    if (!fixed) return
    setVocabFix({
      declared: fixed.declared.filter((n) => !fixed.declaredOwn.includes(n)),
      declaredOwn: fixed.declaredOwn,
      renamed: fixed.renamed,
    })
    onChange(fixed.skeleton)
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
  // Values that would be dropped whole (no kind to put a row's columns in) and
  // ID recipes naming columns the table does not have: heavier losses than a
  // collision, and until now both walked straight past the continue button.
  const gapping = skeleton.maps.filter((m) => !!annotations?.maps?.[m.name]?.missing_row_kind)
  const missingCols = skeleton.maps.filter(
    (m) => annotations?.maps?.[m.name]?.reason === 'missing-columns',
  )
  const blockers =
    placeholderPrefixes.length +
    collapsing.length +
    gapping.length +
    missingCols.length +
    (plain ? undeclared.length : 0)

  /** Does this map's evidence actually carry a one-tap fix? A button that
   *  promises "pick another candidate" must not land on a row that has none. */
  function hasOneTapFix(name: string): boolean {
    const a = annotations?.maps?.[name]
    if (!a) return false
    const suggestions =
      (a as { column_suggestions?: { suggestions: string[] }[] }).column_suggestions ?? []
    return (a.key_candidates?.length ?? 0) > 0 || suggestions.some((s) => s.suggestions.length > 0)
  }

  /** Scroll to a map's row and put the focus on its first one-tap fix — "pick
   *  another candidate" has to LAND somewhere, not just close a dialog. The
   *  chips live in THIS map's evidence row (the immediate sibling): searching
   *  the whole tbody would focus whichever row happens to come first. */
  function focusRow(name: string) {
    setConfirming(false)
    const row = document.querySelector(`[data-map="${CSS.escape(name)}"]`)
    row?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const evidence = row?.nextElementSibling
    const chip = evidence?.classList.contains('skeleton-evidence-row')
      ? evidence.querySelector<HTMLButtonElement>('.skeleton-candidate-chip, .skeleton-gap-add')
      : null
    chip?.focus({ preventScroll: true })
  }

  // The old window.confirm chain made "proceed with the damage" the Enter
  // key's default and offered no way to fix anything (K7). One inline block
  // instead, where every item carries the repair as its main button.
  // The check reads the CURRENT blockers every time: gating on "was the block
  // already open" would wave a NEW blocker (an edit made while the block was
  // up) straight through — the auto-continue K7 forbids.
  function onContinueGuarded() {
    if (blockers > 0) {
      setConfirming(true)
      return
    }
    setConfirming(false)
    onContinue()
  }

  /** The ghost exit of the confirm block: proceed with the damage, knowingly. */
  function continueAnyway() {
    setConfirming(false)
    onContinue()
  }

  // The skeleton at a glance: how many kinds, linked how. A one-box skeleton
  // that should be two is visible here before any table reading — but with a
  // single kind the picture says nothing, and on the kantan tier it pushed the
  // thing the human must actually confirm below the fold.
  const diagramLinked = skeletonMermaid(skeleton, '|').includes('-->')
  const diagram = (
    <div className="skeleton-diagram">
      <Mermaid chart={skeletonMermaid(skeleton, t('workbench:skeleton.diagram.edge'))} />
      <p className="skeleton-diagram-note">{t('workbench:skeleton.diagram.note')}</p>
    </div>
  )
  const diagramBlock = !plain ? (
    diagram
  ) : skeleton.maps.length > 1 ? (
    <details className="skeleton-fold">
      <summary>
        {t('skeletongate:diagram.summary', { count: skeleton.maps.length })}
        {diagramLinked && t('skeletongate:diagram.linked')}
      </summary>
      {diagram}
    </details>
  ) : null

  // The ID-shape card. Once the issuer is configured this is one settled line
  // ("this is what your IDs look like"), not a form to fill in.
  const shortNsPreview = nsDetected ? `${nsDetected.base}/datasets/${nsDetected.slug}/…` : ''
  const nsCompact = plain && !baseUnconfigured && !nsOpen
  const nsCard = nsDetected ? (
    /* Namespace card (ADR K13): the ONE naming judgment — the dataset name
       inside the permanent ID — is the editable thing; the prefix pair and
       both IRIs derive from it mechanically. Base fixes route to Settings,
       never to a raw-IRI textbox. */
    <section className="skeleton-ns-card">
      {nsCompact ? (
        <div className="skeleton-ns-name-row">
          <span className="skeleton-ns-name-label">{t('skeletongate:ns.shape')}</span>
          <code className="skeleton-ns-preview">{shortNsPreview}</code>
          <button type="button" className="btn btn--ghost" onClick={() => setNsOpen(true)}>
            {t('skeletongate:ns.change')}
          </button>
        </div>
      ) : (
        <>
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
              onBlur={(e) => commitDatasetName(e.target)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
              }}
            />
            {/* A bare URL with no label reads as a bug (localhost especially):
                say what it is, and mark it provisional while it is. */}
            <span className="skeleton-ns-name-label">{t('skeletongate:ns.shape')}</span>
            <code className="skeleton-ns-preview">
              {shortNsPreview}
              {baseUnconfigured && ` ${t('skeletongate:ns.provisional')}`}
            </code>
          </div>
          {nameInvalid && (
            <p className="skeleton-evidence-line skeleton-evidence-warn">
              {t('skeletongate:ns.nameInvalid')}
            </p>
          )}
          <p className="skeleton-gate-hint">{t('workbench:skeleton.ns.nameHint')}</p>
          {baseUnconfigured && (
            <>
              {/* Plain tier: NOT a warning band. A provisional issuer is the
                  normal state of a fresh install, and painting the normal path
                  red teaches people to ignore red — one muted sentence about
                  what it means for them instead (GATE-13). */}
              {plain ? (
                <p className="skeleton-gate-hint">
                  {t('skeletongate:ns.baseProvisional', { base: nsDetected.base })}
                </p>
              ) : (
                <p className="skeleton-evidence-line skeleton-evidence-warn">
                  {t('workbench:skeleton.ns.baseUnconfigured', { base: nsDetected.base })}
                </p>
              )}
              {/* Both tiers: a note that names a setting and gives no way to
                  reach it is a dead end (GATE-13 / MISC-13). */}
              {onOpenSettings && (
                <button type="button" className="btn btn--ghost btn--sm" onClick={onOpenSettings}>
                  {t(plain ? 'skeletongate:ns.openSettings' : 'workbench:skeleton.ns.openSettings')}
                </button>
              )}
            </>
          )}
        </>
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
  )

  return (
    <section className="skeleton-gate">
      <h4>{t(titleKey)}</h4>
      <p className="skeleton-gate-hint">{t(hintKey)}</p>
      {/* What the deterministic vocabulary repair did — never silent. */}
      {vocabFix && vocabFix.declared.length > 0 && (
        <p className="skeleton-evidence-line skeleton-evidence-muted">
          {t('skeletongate:vocab.declared', { names: vocabFix.declared.join(', ') })}
        </p>
      )}
      {vocabFix && vocabFix.declaredOwn.length > 0 && (
        <p className="skeleton-evidence-line skeleton-evidence-muted">
          {t('skeletongate:vocab.declaredOwn', { names: vocabFix.declaredOwn.join(', ') })}
        </p>
      )}
      {vocabFix && vocabFix.renamed.length > 0 && (
        <p className="skeleton-evidence-line skeleton-evidence-muted">
          {t('skeletongate:vocab.renamed', { names: vocabFix.renamed.join(', ') })}
        </p>
      )}
      {plain && undeclared.length > 0 && (
        <div className="skeleton-gap">
          <p className="skeleton-evidence-line skeleton-evidence-warn">
            {t('skeletongate:vocab.unresolved', { names: undeclared.join(', ') })}
          </p>
          {vocabFixable && (
            <button type="button" className="skeleton-gap-add" disabled={busy} onClick={repairVocabulary}>
              {t('skeletongate:vocab.fix')}
            </button>
          )}
        </div>
      )}
      {diagramBlock}
      {annotationsBusy && (
        <p className="skeleton-gate-revalidating" role="status">
          <span className="spinner" />
          {t('workbench:skeleton.evidence.revalidating')}
        </p>
      )}
      {/* The files are gone: say it once, in the words the rest of the gate
          uses, and put the way back right next to it. */}
      {!canRevalidate && (
        <p className="skeleton-gate-revalidating">
          {filesGoneText ?? t('workbench:skeleton.evidence.reattach')}
          {plain && (
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => {
                if (window.confirm(t(discardConfirmKey))) onDiscard()
              }}
            >
              {t(discardKey)}
            </button>
          )}
        </p>
      )}
      {!plain && nsCard}
      <div className="skeleton-gate-table-wrap">
        <table className="skeleton-gate-table">
          <thead>
            <tr>
              {/* K4: one kind, one name. The internal map name is machine
                  bookkeeping — showing it beside the kind put two English
                  identifiers on every row and named the same thing twice. */}
              {!plain && <th>{t('workbench:skeleton.colClass')}</th>}
              <th>{plain ? t('skeletongate:col.source') : t('workbench:skeleton.colSource')}</th>
              <th>{plain ? t('skeletongate:col.key') : t('workbench:skeleton.colKey')}</th>
              {/* K4: "クラス" is a fatal-tier word — the kantan header asks the
                  question the column answers instead. */}
              <th>{plain ? t('skeletongate:col.kind') : t('workbench:skeleton.colClasses')}</th>
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
              // K14 in the cell itself: the ID recipe as its CONSEQUENCE (which
              // columns decide the ID), never as template syntax. The raw
              // template stays one fold away for whoever wants it. Read from
              // the LIVE template, not the annotation: the annotation lags a
              // candidate chip by one re-check, and a sentence that describes
              // the previous key is worse than no sentence.
              const templateColumns = [...keyValue.matchAll(/\{([^{}]+)\}/g)].map((x) => x[1])
              const keyColumns =
                templateColumns.length > 0 ? templateColumns : (ann?.key_columns ?? [])
              // A template with no {column} in it mints ONE id for the whole
              // file — the same reading as a constant, not "no recipe yet"
              // (which is only true of an empty cell).
              const keySentence =
                usesConstant || (keyColumns.length === 0 && keyValue.trim() !== '')
                  ? t('skeletongate:key.constant')
                  : keyColumns.length === 0
                    ? t('skeletongate:key.none')
                    : t(
                        keyColumns.length === 1
                          ? 'skeletongate:key.from1'
                          : 'skeletongate:key.fromN',
                        { columns: keyColumns.join(t('skeletongate:key.join')) },
                      )
              const removeControl = skeleton.maps.length > 1 &&
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
                    title={plain ? t('skeletongate:remove') : t('workbench:skeleton.remove')}
                    onClick={() => setConfirmRemove(m.name)}
                  >
                    {plain ? t('skeletongate:remove') : t('workbench:skeleton.remove')}
                  </button>
                ))
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
                    {/* Removal is the other half of "add": without it the gate
                        is a one-way door. Two-step, and never the last map. */}
                    {!plain && (
                      <td className="skeleton-gate-name">
                        {m.name}
                        {removeControl}
                      </td>
                    )}
                    <td className="skeleton-gate-source">{m.source}</td>
                    <td>
                      {plain ? (
                        <>
                          <p className="skeleton-evidence-line">{keySentence}</p>
                          <details className="skeleton-fold">
                            <summary>{t('skeletongate:key.editSummary')}</summary>
                            <textarea
                              className="skeleton-gate-input skeleton-gate-key"
                              value={displayKey}
                              rows={Math.max(1, Math.ceil(displayKey.length / 48))}
                              disabled={busy}
                              onChange={(e) => {
                                const raw = e.target.value.replace(/\n/g, '')
                                updateSubject(
                                  idx,
                                  usesConstant
                                    ? { constant: expandTemplate(raw, nsDetected) }
                                    : { template: expandTemplate(raw, nsDetected) },
                                )
                              }}
                            />
                          </details>
                        </>
                      ) : (
                        /* A full IRI template rarely fits one line — wrap it
                           (rows grow with content) so the tail is never cut off. */
                        <textarea
                          className="skeleton-gate-input skeleton-gate-key"
                          value={displayKey}
                          rows={Math.max(1, Math.ceil(displayKey.length / 48))}
                          disabled={busy}
                          title={m.note ?? undefined}
                          onChange={(e) => {
                            const raw = e.target.value.replace(/\n/g, '')
                            updateSubject(
                              idx,
                              usesConstant ? { constant: raw } : { template: raw },
                            )
                          }}
                        />
                      )}
                      {/* The AI's own note is raw model output (English, jargon
                          on a weak model) — information for whoever asks, not
                          the kantan tier's default reading. */}
                      {m.note &&
                        (plain ? (
                          <details className="skeleton-fold">
                            <summary>{t('skeletongate:noteSummary')}</summary>
                            <p className="skeleton-evidence-line skeleton-evidence-muted">
                              {m.note}
                            </p>
                          </details>
                        ) : (
                          <div className="skeleton-gate-note">{m.note}</div>
                        ))}
                    </td>
                    <td>
                      {/* An empty box under "1 行が表すもの" reads as "nothing to
                          do here", but a map with no kind produces rows with no
                          type at all — nothing can later be counted or asked
                          about by kind. Say it, in the cell where the answer
                          goes. (The reading line above falls back to the map's
                          own name, so the sentence alone cannot reveal this.) */}
                      {plain && (m.subject.classes ?? []).length === 0 && (
                        <p className="skeleton-evidence-line skeleton-evidence-warn">
                          ⚠ {t('skeletongate:kindMissing')}
                        </p>
                      )}
                      <input
                        type="text"
                        className="skeleton-gate-input"
                        placeholder={plain ? t('skeletongate:kindPlaceholder') : undefined}
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
                      {/* A destructive control at the START of the row is the
                          first thing the eye lands on; at the end it reads as
                          what it is (after the row has been described). */}
                      {plain && removeControl}
                      {plain && addedMap === m.name && (
                        <p className="skeleton-evidence-line skeleton-evidence-muted">
                          {t('skeletongate:addedHint')}
                        </p>
                      )}
                    </td>
                  </tr>
                  {ann && (
                    <tr className="skeleton-evidence-row">
                      <td colSpan={plain ? 3 : 4}>
                        <SkeletonEvidence
                          ann={ann}
                          onApplyCandidate={(cols) => applyCandidate(idx, cols)}
                          onAddRowKind={() => addRowKind(idx)}
                          onSplit={(cols, key) => splitConcept(idx, cols, key)}
                          canRevalidate={canRevalidate}
                          displayClass={
                            plain ? (c) => compactClass(c, nsDetected) : undefined
                          }
                          plain={plain}
                          reading={readingFor(m, ann)}
                          displayMap={displayMapName}
                          shortId={shortId}
                          filesGoneText={filesGoneText}
                          suggestedClass={
                            (ann.class_numeric_key_caution?.length ?? 0) > 0
                              ? (compactClass(suggestedClassFor(idx) ?? '', nsDetected) || undefined)
                              : undefined
                          }
                          onUseSuggestedClass={() => {
                            const cls = suggestedClassFor(idx)
                            if (cls) updateSubject(idx, { classes: [cls] })
                          }}
                          onFixColumn={(wrong, right) => fixColumnName(idx, wrong, right)}
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
      {/* The kantan tier reads the table FIRST (that is the one question this
          screen asks) and meets the naming card after — settled, one line, on
          the way out. The detail tier keeps the card above the table. */}
      {plain && nsCard}
      {/* AI-redo exit: when the skeleton is STRUCTURALLY wrong (wrong split
          into kinds, wrong key idea), editing cells is the wrong tool — hand
          a plain-language note back to the generation instead. */}
      {/* When the sources are gone the AI cannot be re-run, so the caller stops
          passing onRethink and this whole exit used to VANISH silently — the
          same "it disappeared and nobody said why" that misleads elsewhere in
          this gate. Keep the block, disabled, with the reason. */}
      {!onRethink && !canRevalidate && (
        <div className="skeleton-rethink">
          <p className="skeleton-gate-hint">
            {filesGoneText ?? t('workbench:skeleton.rethink.needsFiles')}
          </p>
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
      {/* "Are you sure?" where the answer can be "no, fix it": every item names
          what continuing costs and carries the repair as its own button. The
          native confirm this replaces had OK/Cancel only — and its Enter-key
          default was "proceed with the collision" (K7 forbids exactly that). */}
      {/* Fixing every item from inside the block empties it: close it then, so
          the normal continue button comes back instead of an empty alert. */}
      {confirming && blockers > 0 && (
        <div className="wb-fix-box" role="alert">
          <p className="skeleton-evidence-line">
            <strong>{t('skeletongate:confirm.head')}</strong>
          </p>
          {missingCols.map((m) => (
            <div key={`missing:${m.name}`} className="skeleton-gap">
              <p className="skeleton-evidence-line skeleton-evidence-bad">
                ⚠{' '}
                {t('skeletongate:confirm.missing', {
                  map: displayMapName(m.name),
                  columns: (annotations?.maps?.[m.name]?.missing_columns ?? []).join(', '),
                })}
              </p>
              <button
                type="button"
                className="skeleton-gap-add"
                onClick={() => focusRow(m.name)}
              >
                {hasOneTapFix(m.name)
                  ? t('skeletongate:confirm.missingFix')
                  : t('skeletongate:confirm.showRow')}
              </button>
            </div>
          ))}
          {gapping.map((m) => {
            const gapColumns = annotations?.maps?.[m.name]?.missing_row_kind?.columns ?? []
            return (
              <div key={`gap:${m.name}`} className="skeleton-gap">
                {/* Name a few, COUNT the rest: this is where the human accepts
                    the loss, so "5 columns" must not stand in for 40. */}
                <p
                  className="skeleton-evidence-line skeleton-evidence-bad"
                  title={gapColumns.join(', ')}
                >
                  ⚠{' '}
                  {t('skeletongate:confirm.gap', {
                    columns: columnsSummary(gapColumns, (count) =>
                      t('workbench:skeleton.evidence.cardOmitted', { count }),
                    ),
                  })}
                </p>
                <button
                  type="button"
                  className="skeleton-gap-add"
                  disabled={!canRevalidate || busy}
                  onClick={() => {
                    setConfirming(false)
                    addRowKind(skeleton.maps.findIndex((x) => x.name === m.name))
                  }}
                >
                  {t('skeletongate:confirm.gapFix')}
                </button>
                {!canRevalidate && (
                  <p className="skeleton-evidence-line skeleton-evidence-muted">
                    {filesGoneText ?? t('workbench:skeleton.evidence.gapNeedsFiles')}
                  </p>
                )}
              </div>
            )
          })}
          {collapsing.map((m) => (
            <div key={`collides:${m.name}`} className="skeleton-gap">
              <p className="skeleton-evidence-line skeleton-evidence-bad">
                ⚠{' '}
                {plain
                  ? t('skeletongate:confirm.collides', { map: displayMapName(m.name) })
                  : t('workbench:skeleton.confirmCollides', { maps: m.name })}
              </p>
              <button type="button" className="skeleton-gap-add" onClick={() => focusRow(m.name)}>
                {hasOneTapFix(m.name)
                  ? t('skeletongate:confirm.collidesFix')
                  : t('skeletongate:confirm.showRow')}
              </button>
            </div>
          ))}
          {plain && undeclared.length > 0 && (
            <div className="skeleton-gap">
              <p className="skeleton-evidence-line skeleton-evidence-bad">
                ⚠ {t('skeletongate:vocab.unresolved', { names: undeclared.join(', ') })}
              </p>
              {vocabFixable && (
                <button
                  type="button"
                  className="skeleton-gap-add"
                  disabled={busy}
                  onClick={() => {
                    setConfirming(false)
                    repairVocabulary()
                  }}
                >
                  {t('skeletongate:vocab.fix')}
                </button>
              )}
            </div>
          )}
          {placeholderPrefixes.length > 0 && (
            <p className="skeleton-evidence-line skeleton-evidence-warn">
              {/* K13: the machine-derived shorthand is not the human's to know,
                  so the kantan copy names neither the prefixes nor Settings. */}
              {plain
                ? t('skeletongate:confirm.placeholder')
                : t('workbench:skeleton.ns.confirmPlaceholder', {
                    prefixes: placeholderPrefixes.map((p) => p.prefix).join(', '),
                  })}
            </p>
          )}
          <div className="skeleton-gate-actions">
            {onRethink && (
              <button
                type="button"
                className="skeleton-gap-add"
                disabled={busy}
                onClick={() => {
                  setConfirming(false)
                  onRethink(rethinkNote.trim())
                }}
              >
                {t('skeletongate:confirm.rethinkFix')}
              </button>
            )}
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              onClick={() => setConfirming(false)}
            >
              {t('skeletongate:confirm.cancel')}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              onClick={continueAnyway}
            >
              {t('skeletongate:confirm.proceed')}
            </button>
          </div>
        </div>
      )}
      <div className="skeleton-gate-actions">
        <button onClick={onContinueGuarded} disabled={busy || (confirming && blockers > 0)}>
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
