import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import {
  align,
  type Alignment,
  type AlignmentsResult,
  buildPerspective,
  type CrosswalkPerspective,
  getAlignments,
  getCrosswalks,
  unalign,
} from './crosswalkApi'
import { ArrowIcon, LayersIcon, LinkIcon } from './icons'
import { ToolsPanel } from './ToolsPanel'
import { localName } from './vocab'

/**
 * Catalog → クロスウォーク管理面 (multi-perspective ADR, 管理=カタログ). The upper ontology
 * is PLURAL: a list of independent crosswalk PERSPECTIVES (lenses). Each is its own
 * graph + config; pick one to see its participants, stats, cross-dataset tools, and a
 * manual rebuild. Creation (incl. naming a new perspective) lives in データを追加 →
 * 横断でつなぐ (CrosswalkBuilder).
 */
export function CrosswalkView({
  onBack,
  onOpenMap,
}: {
  onBack?: () => void
  onOpenMap?: () => void
}) {
  const { t } = useTranslation()
  const [perspectives, setPerspectives] = useState<CrosswalkPerspective[] | null>(null)
  const [err, setErr] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildErr, setRebuildErr] = useState('')
  const [note, setNote] = useState('')

  function load() {
    getCrosswalks()
      .then(setPerspectives)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    let off = false
    getCrosswalks()
      .then((ps) => !off && setPerspectives(ps))
      .catch((e) => !off && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
  }, [])

  const list = perspectives ?? []
  const selected = list.find((p) => p.perspective_id === selectedId) ?? list[0] ?? null

  function pname(p: CrosswalkPerspective): string {
    return p.dataset?.name || p.perspective_id
  }

  async function onRebuild() {
    if (!selected) return
    setRebuilding(true)
    setRebuildErr('')
    setNote('')
    try {
      const r = await buildPerspective(selected.perspective_id) // no config → rebuild persisted
      setNote(
        t('crosswalk:view.rebuildNote', {
          shared: r.shared_total,
          count: r.participants_used.length,
        }),
      )
      load()
    } catch (e) {
      setRebuildErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRebuilding(false)
    }
  }

  const concepts = selected?.config?.concepts ?? []
  const participants = concepts.flatMap((c) => c.participants)
  const shared = selected?.dataset?.crosswalk_shared_compositions

  return (
    <div className="crosswalk-view">
      {onBack && (
        <button type="button" className="vocab-back" onClick={onBack}>
          <ArrowIcon size={14} className="vocab-back-arrow" /> {t('crosswalk:view.back')}
        </button>
      )}

      <div className="vocab-banner">
        <span className="vocab-banner-icon">
          <LinkIcon size={22} />
        </span>
        <div>
          <h2 className="vocab-banner-title">{t('crosswalk:view.bannerTitle')}</h2>
          <p className="vocab-banner-sub">
            <Trans i18nKey="crosswalk:view.bannerSub" components={[<strong />, <strong />]} />
          </p>
        </div>
        {onOpenMap && (
          <button type="button" className="btn btn--ghost btn--sm crosswalk-map-btn" onClick={onOpenMap}>
            <LayersIcon size={14} /> {t('crosswalk:view.seeMap')}
          </button>
        )}
      </div>

      {err && <pre className="error">{err}</pre>}
      {!perspectives && !err && (
        <p className="loading-row">
          <span className="spinner" />
          {t('crosswalk:view.loading')}
        </p>
      )}

      {perspectives && list.length === 0 && (
        <div className="state-block">
          <p className="state-title">{t('crosswalk:view.empty.title')}</p>
          <p className="state-sub">{t('crosswalk:view.empty.sub')}</p>
        </div>
      )}

      {list.length > 0 && (
        <>
          <div className="ds-subhead">
            {t('crosswalk:view.perspectiveHead')}
            <span className="xw-hint-inline">
              {t('crosswalk:view.perspectiveHint', { count: list.length })}
            </span>
          </div>
          <div className="xw-persp-tabs">
            {list.map((p) => (
              <button
                key={p.perspective_id}
                type="button"
                className={`xw-persp-tab${p.perspective_id === selected?.perspective_id ? ' active' : ''}`}
                onClick={() => setSelectedId(p.perspective_id)}
              >
                <span className="xw-persp-name">{pname(p)}</span>
                <span className="xw-persp-meta">
                  {t('crosswalk:view.perspMeta', {
                    shared: p.dataset?.crosswalk_shared_compositions ?? '—',
                    count: p.config?.concepts.flatMap((c) => c.participants).length ?? 0,
                  })}
                </span>
              </button>
            ))}
          </div>

          {selected && (
            <>
              <div className="card xw-detail-card">
              <div className="xw-summary">
                <div className="xw-summary-stat">
                  <span className="xw-summary-num">{shared ?? '—'}</span>
                  <span className="xw-summary-label">{t('crosswalk:view.summary.sharedValues')}</span>
                </div>
                <div className="xw-summary-stat">
                  <span className="xw-summary-num">{participants.length}</span>
                  <span className="xw-summary-label">{t('crosswalk:view.summary.participants')}</span>
                </div>
                <div className="xw-summary-stat">
                  <span className="xw-summary-num">{concepts.length}</span>
                  <span className="xw-summary-label">{t('crosswalk:view.summary.concepts')}</span>
                </div>
              </div>
              <p className="xw-summary-note">
                {t('crosswalk:view.summary.note', { concept: concepts[0]?.name ?? '—' })}
              </p>

              {concepts.map((c) => (
                <div className="xw-concept" key={c.name}>
                  <div className="ds-subhead">
                    {t('crosswalk:view.conceptHead', { name: c.name })}
                    <span className="xw-hint-inline">
                      {c.key_parts && c.key_parts.length > 0
                        ? t('crosswalk:view.compoundKeyHint', {
                            parts: c.key_parts.map((kp) => kp.name).join(' × '),
                          })
                        : t('crosswalk:view.normalizerHint', { normalizer: c.normalizer ?? 'identity' })}
                    </span>
                  </div>
                  <div className="xw-participants">
                    {c.participants.map((p) => {
                      // single-part = one predicate; compound = one per key part.
                      const preds = p.predicate
                        ? [p.predicate]
                        : Object.values(p.predicates ?? {})
                      return (
                        <span key={p.dataset_id} className="xw-part-chip" title={preds.join(', ')}>
                          <span className="xw-part-name">{p.label}</span>
                          <code className="xw-part-pred">{preds.map(localName).join(' · ')}</code>
                        </span>
                      )
                    })}
                  </div>
                </div>
              ))}

              <div className="xw-rebuild-row">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={rebuilding}
                  onClick={onRebuild}
                >
                  {rebuilding ? t('crosswalk:view.rebuilding') : t('crosswalk:view.rebuild')}
                </button>
                {selected.dataset?.crosswalk_built_at && (
                  <span className="xw-built-at">
                    {t('crosswalk:view.builtAt', {
                      at: selected.dataset.crosswalk_built_at.slice(0, 19).replace('T', ' '),
                    })}
                  </span>
                )}
              </div>
              {note && <p className="lifecycle-ok">{note}</p>}
              {rebuildErr && (
                <p className="promote-err">
                  {t('crosswalk:view.rebuildErr', { detail: rebuildErr })}
                </p>
              )}
              </div>

              <div className="card xw-tools-card">
                <div className="ds-subhead xw-tools-head">
                  {t('crosswalk:view.toolsHead')}
                  <span className="xw-hint-inline">{t('crosswalk:view.toolsHint')}</span>
                </div>
                {/* The hub-resident cross-dataset tools — keyed by perspective so they
                    reload when you switch lens. */}
                <ToolsPanel
                  key={selected.perspective_id}
                  datasetId={selected.dataset?.id ?? 'crosswalk-bridge'}
                />
              </div>
            </>
          )}
        </>
      )}

      {perspectives && <PerspectiveAlignment perspectives={list} />}
    </div>
  )
}

// --- 視点をつなぐ (multi-perspective ADR §Phase 2) -------------------------------
// Assert a human-vetted, citable, reversible SCHEMA relationship between two
// perspectives' terms (a concept class or its link predicate). Closed relation set;
// stored in a promoted alignment graph the FROM-merge unions. Oxigraph runs no OWL
// reasoner, so this is a fact a tool can FOLLOW — it never rewrites queries.

const RELATION_KEYS = new Set([
  'equivalentClass',
  'subClassOf',
  'equivalentProperty',
  'subPropertyOf',
])
const CLASS_RELATIONS = new Set(['equivalentClass', 'subClassOf'])

interface PerspTerm {
  iri: string
  kind: 'class' | 'property'
  conceptName: string
  name: string
}

function perspName(p: CrosswalkPerspective): string {
  return p.dataset?.name || p.perspective_id
}

/** A perspective's alignable terms: each concept contributes its class + its link
 * predicate. */
function perspectiveTerms(p: CrosswalkPerspective | undefined): PerspTerm[] {
  const out: PerspTerm[] = []
  for (const c of p?.config?.concepts ?? []) {
    if (c.class_iri)
      out.push({ iri: c.class_iri, kind: 'class', conceptName: c.name, name: localName(c.class_iri) })
    if (c.link_predicate)
      out.push({
        iri: c.link_predicate,
        kind: 'property',
        conceptName: c.name,
        name: localName(c.link_predicate),
      })
  }
  return out
}

function PerspectiveAlignment({ perspectives }: { perspectives: CrosswalkPerspective[] }) {
  const { t } = useTranslation()
  const relationLabel = (rel: string): string =>
    RELATION_KEYS.has(rel) ? t(`crosswalk:relation.${rel}`) : rel
  const [data, setData] = useState<AlignmentsResult | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [srcPid, setSrcPid] = useState('')
  const [srcIri, setSrcIri] = useState('')
  const [relation, setRelation] = useState('')
  const [tgtPid, setTgtPid] = useState('')
  const [tgtIri, setTgtIri] = useState('')
  const [busy, setBusy] = useState(false)
  const [actErr, setActErr] = useState('')
  const [note, setNote] = useState('')
  const [removing, setRemoving] = useState('')

  function load() {
    getAlignments()
      .then(setData)
      .catch((e) => setLoadErr(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    let off = false
    getAlignments()
      .then((d) => !off && setData(d))
      .catch((e) => !off && setLoadErr(e instanceof Error ? e.message : String(e)))
    return () => {
      off = true
    }
  }, [])

  // Effective (fallback-resolved) selections, so the controlled selects stay valid as
  // the user narrows source kind / perspectives.
  const srcPersp = perspectives.find((p) => p.perspective_id === srcPid) ?? perspectives[0]
  const tgtPersp =
    perspectives.find((p) => p.perspective_id === tgtPid) ?? perspectives[1] ?? perspectives[0]
  const srcTerms = perspectiveTerms(srcPersp)
  const srcTerm = srcTerms.find((t) => t.iri === srcIri) ?? srcTerms[0]
  const kind = srcTerm?.kind ?? 'class'
  const relOptions = (data?.relations ?? []).filter((r) =>
    kind === 'class' ? CLASS_RELATIONS.has(r) : !CLASS_RELATIONS.has(r),
  )
  const rel = relOptions.includes(relation) ? relation : relOptions[0]
  // Target term must be the same kind as the source (a class aligns to a class).
  const tgtTerms = perspectiveTerms(tgtPersp).filter((t) => t.kind === kind)
  const tgtTerm = tgtTerms.find((t) => t.iri === tgtIri) ?? tgtTerms[0]

  const canAssert = Boolean(srcTerm && tgtTerm && rel && srcTerm.iri !== tgtTerm.iri)

  async function onAssert() {
    if (!canAssert || !srcTerm || !tgtTerm || !srcPersp || !tgtPersp) return
    setBusy(true)
    setActErr('')
    setNote('')
    try {
      await align(srcTerm.iri, tgtTerm.iri, rel, perspName(srcPersp), perspName(tgtPersp))
      setNote(
        t('crosswalk:align.assertNote', {
          source: srcTerm.name,
          relation: relationLabel(rel),
          target: tgtTerm.name,
        }),
      )
      load()
    } catch (e) {
      setActErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function onRemove(a: Alignment) {
    setRemoving(a.alignment_iri)
    setActErr('')
    setNote('')
    try {
      await unalign(a.source, a.target, a.relation)
      load()
    } catch (e) {
      setActErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRemoving('')
    }
  }

  const alignments = data?.alignments ?? []

  return (
    <div className="xw-align">
      <div className="ds-subhead xw-tools-head">
        {t('crosswalk:align.head')}
        <span className="xw-hint-inline">{t('crosswalk:align.hint')}</span>
      </div>

      {loadErr && <pre className="error">{loadErr}</pre>}

      {/* Authoring form: pick two perspectives' terms + a closed-set relation. */}
      <div className="xw-align-form">
        <div className="xw-align-side">
          <span className="xw-align-side-label">{t('crosswalk:align.sourceLabel')}</span>
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.srcPerspective')}
            value={srcPersp?.perspective_id ?? ''}
            onChange={(e) => {
              setSrcPid(e.target.value)
              setSrcIri('')
            }}
            disabled={perspectives.length === 0}
          >
            {perspectives.map((p) => (
              <option key={p.perspective_id} value={p.perspective_id}>
                {perspName(p)}
              </option>
            ))}
          </select>
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.srcTerm')}
            value={srcTerm?.iri ?? ''}
            onChange={(e) => setSrcIri(e.target.value)}
            disabled={srcTerms.length === 0}
          >
            {srcTerms.map((term) => (
              <option key={term.iri} value={term.iri}>
                {t('crosswalk:align.termOption', {
                  kind: term.kind === 'class' ? t('crosswalk:term.class') : t('crosswalk:term.property'),
                  name: term.name,
                })}
              </option>
            ))}
          </select>
        </div>

        <div className="xw-align-rel">
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.relation')}
            value={rel ?? ''}
            onChange={(e) => setRelation(e.target.value)}
            disabled={relOptions.length === 0}
          >
            {relOptions.map((r) => (
              <option key={r} value={r}>
                {relationLabel(r)}
              </option>
            ))}
          </select>
          <ArrowIcon size={16} className="xw-align-arrow" />
        </div>

        <div className="xw-align-side">
          <span className="xw-align-side-label">{t('crosswalk:align.targetLabel')}</span>
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.tgtPerspective')}
            value={tgtPersp?.perspective_id ?? ''}
            onChange={(e) => {
              setTgtPid(e.target.value)
              setTgtIri('')
            }}
            disabled={perspectives.length === 0}
          >
            {perspectives.map((p) => (
              <option key={p.perspective_id} value={p.perspective_id}>
                {perspName(p)}
              </option>
            ))}
          </select>
          <select
            className="xw-map-select"
            aria-label={t('crosswalk:align.a11y.tgtTerm')}
            value={tgtTerm?.iri ?? ''}
            onChange={(e) => setTgtIri(e.target.value)}
            disabled={tgtTerms.length === 0}
          >
            {tgtTerms.map((term) => (
              <option key={term.iri} value={term.iri}>
                {t('crosswalk:align.termOption', {
                  kind: term.kind === 'class' ? t('crosswalk:term.class') : t('crosswalk:term.property'),
                  name: term.name,
                })}
              </option>
            ))}
          </select>
        </div>

        <button
          type="button"
          className="btn btn--accent btn--sm xw-align-btn"
          disabled={!canAssert || busy}
          onClick={onAssert}
        >
          {busy ? t('crosswalk:align.asserting') : t('crosswalk:align.assert')}
        </button>
      </div>

      {!canAssert && perspectives.length > 0 && (
        <p className="xw-align-empty-hint">
          {srcTerms.length === 0
            ? t('crosswalk:align.noSrcTerms')
            : tgtTerms.length === 0
              ? t('crosswalk:align.noTgtTerms')
              : t('crosswalk:align.pickDistinct')}
        </p>
      )}
      {note && <p className="lifecycle-ok">{note}</p>}
      {actErr && <p className="promote-err">{t('crosswalk:align.actErr', { detail: actErr })}</p>}

      {/* The asserted alignments (each withdrawable). */}
      {alignments.length > 0 ? (
        <div className="xw-align-list">
          {alignments.map((a) => (
            <div className="xw-align-row" key={a.alignment_iri}>
              <div className="xw-align-claim">
                <code className="xw-align-term" title={a.source}>
                  {localName(a.source)}
                </code>
                <span className="xw-align-relchip">{relationLabel(a.relation)}</span>
                <code className="xw-align-term" title={a.target}>
                  {localName(a.target)}
                </code>
              </div>
              <div className="xw-align-meta">
                {(a.from_perspective || a.to_perspective) && (
                  <span className="xw-align-persp">
                    {t('crosswalk:align.perspArrow', {
                      from: a.from_perspective || '—',
                      to: a.to_perspective || '—',
                    })}
                  </span>
                )}
                {a.at && <span className="xw-built-at">{a.at.slice(0, 19).replace('T', ' ')}</span>}
              </div>
              <button
                type="button"
                className="btn btn--ghost btn--sm xw-align-remove"
                disabled={removing === a.alignment_iri}
                onClick={() => onRemove(a)}
              >
                {removing === a.alignment_iri
                  ? t('crosswalk:align.removing')
                  : t('crosswalk:align.remove')}
              </button>
            </div>
          ))}
        </div>
      ) : (
        data && <p className="xw-align-none">{t('crosswalk:align.none')}</p>
      )}
    </div>
  )
}
