import { Fragment, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type {
  MappingSkeleton,
  SkeletonAnnotations,
  SkeletonMap,
  SkeletonMapAnnotation,
} from './api'
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
}: {
  ann: SkeletonMapAnnotation
  onApplyCandidate: (columns: string[]) => void
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

  const collides = ann.is_unique === false
  // K7: a key that is unique TODAY but built only from measurement values gets
  // an amber caution under the green band, and the proven candidates still show
  // (the green band alone let a semantically wrong ID through in real dogfood).
  const caution = ann.is_unique === true && ann.key_measurement_caution === true
  const showCandidates = (collides || caution) && (ann.key_candidates?.length ?? 0) > 0
  return (
    <div className="skeleton-evidence">
      {ann.is_unique ? (
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
      {caution && (
        <p className="skeleton-evidence-line skeleton-evidence-caution">
          ⚠ {t('workbench:skeleton.evidence.measurementKeyCaution')}
        </p>
      )}
      {/* ZEM naming trap: the row class named after a measured key column
          ("Temperature" over key {Measurement temp.(C)}) — the row identity
          mislabeled as one of its measurements. */}
      {(ann.class_numeric_key_caution?.length ?? 0) > 0 && (
        <p className="skeleton-evidence-line skeleton-evidence-caution">
          ⚠{' '}
          {t('workbench:skeleton.evidence.classNumericKeyCaution', {
            cls: ann.class_numeric_key_caution!.map((c) => c.class).join(', '),
            column: ann.class_numeric_key_caution!.map((c) => c.column).join(', '),
          })}
        </p>
      )}
      {(ann.id_previews?.length ?? 0) > 0 && (
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
      )}
      {showCandidates && (
        <div className="skeleton-evidence-candidates">
          <span className="skeleton-evidence-label">
            {t('workbench:skeleton.evidence.candidatesHead')}
          </span>
          {ann.key_candidates!.map((c) => (
            <button
              key={c.columns.join(' ')}
              type="button"
              className="skeleton-candidate-chip"
              title={
                c.measurement_only
                  ? t('workbench:skeleton.evidence.measurementOnly')
                  : undefined
              }
              onClick={() => onApplyCandidate(c.columns)}
            >
              {c.columns.map((col) => `{${col}}`).join(' + ')}
              {c.measurement_only && ' ⚠'}
            </button>
          ))}
        </div>
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

  function updatePrefix(name: string, iri: string) {
    onChange({ ...skeleton, prefixes: { ...skeleton.prefixes, [name]: iri } })
  }

  // Namespaces minted on a placeholder domain (example.org & co) can never be
  // published — the server evidence names them; editing the IRI re-checks like
  // any key edit (ADR instance-iri-base.md).
  const placeholderPrefixes = annotations?.placeholder_prefixes ?? []
  const placeholderSet = new Set(placeholderPrefixes.map((p) => p.prefix))

  // Warn before continuing when the evidence says a key still collapses rows —
  // soft gate: the human can proceed (small collision counts can be legitimate,
  // e.g. deliberate dedup), but never unknowingly.
  const collapsing = skeleton.maps.filter(
    (m) => annotations?.maps?.[m.name]?.is_unique === false,
  )
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
      </details>
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
              const ann = annotations?.maps?.[m.name]
              return (
                <Fragment key={m.name}>
                  <tr className={ann ? 'skeleton-gate-row' : undefined}>
                    <td className="skeleton-gate-name">{m.name}</td>
                    <td className="skeleton-gate-source">{m.source}</td>
                    <td>
                      {/* A full IRI template rarely fits one line — wrap it
                          (rows grow with content) so the tail is never cut off. */}
                      <textarea
                        className="skeleton-gate-input skeleton-gate-key"
                        value={keyValue}
                        rows={Math.max(1, Math.ceil(keyValue.length / 48))}
                        disabled={busy}
                        title={m.note ?? undefined}
                        onChange={(e) =>
                          updateSubject(
                            idx,
                            usesConstant
                              ? { constant: e.target.value.replace(/\n/g, '') }
                              : { template: e.target.value.replace(/\n/g, '') },
                          )
                        }
                      />
                      {m.note && <div className="skeleton-gate-note">{m.note}</div>}
                    </td>
                    <td>
                      <input
                        type="text"
                        className="skeleton-gate-input"
                        value={(m.subject.classes ?? []).join(', ')}
                        disabled={busy}
                        onChange={(e) =>
                          updateSubject(idx, {
                            classes: e.target.value
                              .split(',')
                              .map((s) => s.trim())
                              .filter(Boolean),
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
