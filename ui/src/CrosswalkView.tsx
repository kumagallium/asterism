import { useEffect, useMemo, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import {
  align,
  type Alignment,
  type AlignmentsResult,
  buildPerspective,
  type CrosswalkPerspective,
  type DiscoverCandidate,
  getAlignments,
  getCrosswalks,
  unalign,
} from './crosswalkApi'
import { CrosswalkBuilder, type CrosswalkSeed } from './CrosswalkBuilder'
import { CrosswalkCreate } from './CrosswalkCreate'
import { conceptLabel, sameAsKey } from './crosswalkLabels'
import { ArrowIcon, ConnectIcon, LayersIcon, LinkIcon } from './icons'
import { ToolsPanel } from './ToolsPanel'
import { knownVocabForIri, localName } from './vocab'

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
  createMode = false,
  onCreateMode,
  onAddData,
  onOpenAsk,
}: {
  onBack?: () => void
  onOpenMap?: () => void
  /** Route-driven: `#/crosswalk/new` opens straight into the guided flow. */
  createMode?: boolean
  onCreateMode?: (on: boolean) => void
  onAddData?: () => void
  onOpenAsk?: (question: string) => void
}) {
  const { t } = useTranslation()
  // The detail tier's full form, opened on demand. Mounted lazily: it fetches the
  // catalog and persists to sessionStorage on mount, which should not happen every
  // time someone merely looks at this screen.
  const [manualOpen, setManualOpen] = useState(false)
  const [seed, setSeed] = useState<CrosswalkSeed | undefined>()
  const [seedKey, setSeedKey] = useState(0)
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

  /** Open the detail form, optionally seeded from a candidate the guided flow found.
   * `seedKey` forces a remount because the builder restores its state once, on mount. */
  function openManual(candidate?: DiscoverCandidate) {
    setSeed(candidate ? seedFromCandidate(candidate) : undefined)
    setSeedKey((k) => k + 1)
    setManualOpen(true)
    onCreateMode?.(false)
  }

  if (createMode) {
    return (
      <div className="crosswalk-view">
        <button type="button" className="vocab-back" onClick={() => onCreateMode?.(false)}>
          <ArrowIcon size={14} className="vocab-back-arrow" /> {t('crosswalk:view.back')}
        </button>
        <CrosswalkCreate
          perspectives={list}
          onCancel={() => onCreateMode?.(false)}
          onBuilt={load}
          onOpenManual={openManual}
          onAddData={onAddData}
          onOpenAsk={onOpenAsk}
        />
      </div>
    )
  }

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

      {/* Making a connection lives HERE, and does NOT depend on how many already
          exist — the old empty-state-only wording vanished the moment the first one
          was built, leaving no way to make a second (crosswalk-hub.md ⑤ revised). */}
      {perspectives && (
        <div className={`xw-create-band${list.length === 0 ? ' xw-create-band--hero' : ''}`}>
          <span className="xw-create-band-icon">
            <ConnectIcon size={list.length === 0 ? 22 : 16} />
          </span>
          <div className="xw-create-band-text">
            <p className="xw-create-band-title">
              {list.length === 0
                ? t('crosswalk:view.empty.title')
                : t('crosswalk:create.bandTitle')}
            </p>
            <p className="xw-create-band-sub">
              {list.length === 0 ? t('crosswalk:view.empty.sub') : t('crosswalk:create.bandSub')}
            </p>
          </div>
          <div className="xw-create-band-actions">
            <button type="button" onClick={() => onCreateMode?.(true)}>
              {t('crosswalk:view.empty.btn')}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => openManual()}
            >
              {t('crosswalk:view.empty.manual')}
            </button>
          </div>
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
                {t('crosswalk:view.summary.note', {
                  concept: concepts[0] ? conceptLabel(concepts[0].name) : '—',
                })}
              </p>

              {concepts.map((c) => (
                <div className="xw-concept" key={c.name}>
                  <div className="ds-subhead">
                    {t('crosswalk:view.conceptHead', { name: conceptLabel(c.name) })}
                    {/* Say what counts as the same value in a sentence — a raw
                        normalizer id ("nfkc") means nothing outside the codebase. */}
                    <span className="xw-hint-inline">
                      {c.key_parts && c.key_parts.length > 0
                        ? t('crosswalk:view.compoundKeyHint', {
                            parts: c.key_parts.map((kp) => conceptLabel(kp.name)).join(' × '),
                          })
                        : t(sameAsKey(c.normalizer ?? 'identity'))}
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

      {/* The detail tier, folded away by default: every control the full authoring
          form has is still here (deletion-free — ADR K1), it just no longer competes
          with the one decision most people came to make. */}
      {perspectives && (
        <details
          className="xw-manual"
          open={manualOpen}
          onToggle={(e) => setManualOpen((e.currentTarget as HTMLDetailsElement).open)}
        >
          <summary className="xw-manual-summary">{t('crosswalk:create.manual.summary')}</summary>
          <p className="xw-manual-note">{t('crosswalk:create.manual.hint')}</p>
          {/* Mounted only once opened: it fetches the catalog and writes
              sessionStorage on mount, which must not run on every visit. */}
          {manualOpen && (
            <>
              {seed && <p className="xw-note">{t('crosswalk:create.manual.seeded')}</p>}
              <CrosswalkBuilder key={seedKey} seed={seed} />
            </>
          )}
          <PerspectiveAlignment perspectives={list} />
        </details>
      )}
    </div>
  )
}

/** A discovered candidate as a starting point for the full form — same predicates,
 * same join key, all still editable. The server already minted this candidate's
 * words, so nothing is re-derived here. */
function seedFromCandidate(c: DiscoverCandidate): CrosswalkSeed {
  return {
    selected: c.participants.map((p) => p.dataset_id),
    predicate: Object.fromEntries(c.participants.map((p) => [p.dataset_id, p.predicate])),
    candidates: Object.fromEntries(
      c.participants.map((p) => [
        p.dataset_id,
        [{ iri: p.predicate, sample: c.samples[0]?.raw[p.dataset_id] ?? '' }],
      ]),
    ),
    concept: c.concept,
    normalizer: c.normalizer,
    perspectiveName: c.name,
  }
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

/** Every term THIS surface can author on: each perspective's concept classes + link
 * predicates. An alignment belongs here only when BOTH of its ends are in this set. */
function alignableIris(perspectives: CrosswalkPerspective[]): Set<string> {
  return new Set(perspectives.flatMap((p) => perspectiveTerms(p)).map((term) => term.iri))
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
  // Two perspectives built on the SAME concept key share one hub term (xw:Composition),
  // so there is nothing to align — say that instead of "pick two different concepts".
  const sameTerm = Boolean(srcTerm && tgtTerm && srcTerm.iri === tgtTerm.iri)

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

  // `GET /api/crosswalk/alignments` is a GLOBAL list: データセット詳細の「外部の標準に
  // 合わせる」(DatasetGrounding) writes through the very same `align()`. Show here only
  // what this surface can author — an alignment whose BOTH ends are perspective terms.
  // The positive test is fail-safe: `knownVocabForIri` alone would leak any grounding to
  // a standard missing from the KNOWN_VOCABS mirror, so it is used for LABELLING only.
  const alignable = useMemo(() => alignableIris(perspectives), [perspectives])
  const all = data?.alignments ?? []
  const alignments = all.filter((a) => alignable.has(a.source) && alignable.has(a.target))
  // Never swallowed: a perspective whose config failed to load has unknown terms, so its
  // alignments land here too — they stay listed (and withdrawable) under a disclosure.
  const others = all.filter((a) => !(alignable.has(a.source) && alignable.has(a.target)))
  const groundedCount = others.filter((a) => knownVocabForIri(a.target)).length
  const strayCount = others.length - groundedCount

  // Nothing to author and nothing asserted: an empty form reads as "pick your datasets
  // here" and is where 初見 gets stuck. Say nothing rather than show empty selects.
  if (perspectives.length === 0 && all.length === 0) return null

  return (
    <div className="xw-align">
      <div className="ds-subhead xw-tools-head">
        {t('crosswalk:align.head')}
        <span className="xw-hint-inline">{t('crosswalk:align.hint')}</span>
      </div>

      {loadErr && <pre className="error">{loadErr}</pre>}

      {/* Aligning needs two crosswalks to align BETWEEN — until then the form would be
          a row of empty selects, which reads as "choose your datasets here". */}
      {perspectives.length < 2 ? (
        <p className="xw-align-gate">
          {t('crosswalk:align.needTwo', { count: perspectives.length })}
        </p>
      ) : (
        <>
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

      {!canAssert && (
        <p className="xw-align-empty-hint">
          {srcTerms.length === 0
            ? t('crosswalk:align.noSrcTerms')
            : tgtTerms.length === 0
              ? t('crosswalk:align.noTgtTerms')
              : sameTerm
                ? t('crosswalk:align.sameTerm')
                : t('crosswalk:align.pickDistinct')}
        </p>
      )}
        </>
      )}
      {note && <p className="lifecycle-ok">{note}</p>}
      {actErr && <p className="promote-err">{t('crosswalk:align.actErr', { detail: actErr })}</p>}

      {/* The asserted alignments (each withdrawable). */}
      {alignments.length > 0 ? (
        <div className="xw-align-list">
          {alignments.map((a) => (
            <AlignmentRow
              key={a.alignment_iri}
              a={a}
              relationLabel={relationLabel}
              removing={removing === a.alignment_iri}
              onRemove={onRemove}
            />
          ))}
        </div>
      ) : (
        data && <p className="xw-align-none">{t('crosswalk:align.none')}</p>
      )}

      {/* Alignments this surface cannot author — almost always the ones made in
          データセット詳細 →「外部の標準に合わせる」. Disclosed rather than hidden, so a
          withdrawal path always exists (a perspective whose config failed to load
          also lands here). */}
      {others.length > 0 && (
        <details className="xw-align-others">
          <summary>{t('crosswalk:align.othersHead', { n: others.length })}</summary>
          {groundedCount > 0 && (
            <p className="xw-hint-inline">
              {t('crosswalk:align.groundingCount', { n: groundedCount })}
            </p>
          )}
          {strayCount > 0 && (
            <p className="xw-hint-inline">{t('crosswalk:align.strayCount', { n: strayCount })}</p>
          )}
          <div className="xw-align-list">
            {others.map((a) => (
              <AlignmentRow
                key={a.alignment_iri}
                a={a}
                relationLabel={relationLabel}
                removing={removing === a.alignment_iri}
                onRemove={onRemove}
              />
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

/** One asserted alignment: the claim, where it came from, and its withdrawal. */
function AlignmentRow({
  a,
  relationLabel,
  removing,
  onRemove,
}: {
  a: Alignment
  relationLabel: (rel: string) => string
  removing: boolean
  onRemove: (a: Alignment) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="xw-align-row">
      <div className="xw-align-claim">
        <code className="xw-align-term" title={a.source}>
          {localName(a.source)}
        </code>
        <span className="xw-align-relchip" title={a.relation}>
          {relationLabel(a.relation)}
        </span>
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
        disabled={removing}
        onClick={() => onRemove(a)}
      >
        {removing ? t('crosswalk:align.removing') : t('crosswalk:align.remove')}
      </button>
    </div>
  )
}
