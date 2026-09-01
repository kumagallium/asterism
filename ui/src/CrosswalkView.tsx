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
import {
  conceptName,
  conceptSentenceLabel,
  crosswalkError,
  perspectiveDisplayName,
  sameAsKey,
} from './crosswalkLabels'
import { getCatalogDatasets } from './galleryApi'
import { ArrowIcon, ConnectIcon, LinkIcon } from './icons'
import { OntologyMapView } from './OntologyMapView'
import { ToolsPanel } from './ToolsPanel'
import { knownVocabForIri, localName } from './vocab'
import { XwLinkDiagram } from './XwLinkDiagram'

/** The connection the address bar names, when it names one. The overview links to a
 * single connection as `#/crosswalk/<perspective_id>`; the app's router keeps only the
 * tab, so this screen reads the rest of the address itself. `new` is the guided flow,
 * not an id, and an id that no longer exists simply falls back to the first connection
 * (the same thing that happens with no address at all). */
function perspectiveIdFromHash(): string | null {
  const parts = window.location.hash
    .replace(/^#\/?/, '')
    .split('/')
    .filter(Boolean)
  if (parts[0] !== 'crosswalk' || !parts[1] || parts[1] === 'new') return null
  return decodeURIComponent(parts[1])
}

/**
 * Catalog → クロスウォーク管理面 (multi-perspective ADR, 管理=カタログ). The upper ontology
 * is PLURAL: a list of independent crosswalk PERSPECTIVES (lenses). Each is its own
 * graph + config; pick one to see its participants, stats, cross-dataset tools, and a
 * manual rebuild. Creation (incl. naming a new perspective) lives in データを追加 →
 * 横断でつなぐ (CrosswalkBuilder).
 */
export function CrosswalkView({
  onBack,
  createMode = false,
  onCreateMode,
  onAddData,
  onOpenAsk,
}: {
  onBack?: () => void
  // 全体像は本画面の最下部に常時埋め込むようになったため、開くボタンは削除した
  // （呼び出し側との互換のため型には残し、受け取っても使わない）。
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
  // Which connection is open. Seeded from the address so a connection node on the
  // overview lands ON that connection instead of on whichever one happens to be first.
  const [selectedId, setSelectedId] = useState<string | null>(perspectiveIdFromHash)
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildErr, setRebuildErr] = useState('')
  const [note, setNote] = useState('')
  const [skippedNote, setSkippedNote] = useState('')
  // Bumped by "load again": a screen whose first fetch failed must be recoverable
  // without a browser reload.
  const [reloads, setReloads] = useState(0)
  // dataset_id -> the dataset's CURRENT name. The saved config keeps ids + an ascii
  // label; the screen has to show what the dataset is called today.
  const [dsNames, setDsNames] = useState<Record<string, string>>({})
  // How many datasets are published right now, or null while unknown (not fetched
  // yet, or the fetch failed). Connecting needs two, and saying so up front beats
  // letting someone start a scan that can only end in a refusal.
  const [publishedCount, setPublishedCount] = useState<number | null>(null)

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
    // Names only — a failure here degrades the chips to their stored label, so it is
    // deliberately not an error state for the screen.
    getCatalogDatasets()
      .then((all) => {
        if (off) return
        const names: Record<string, string> = {}
        for (const d of all) {
          names[d.id] = d.name
          if (d.live?.meta.id) names[d.live.meta.id] = d.name
        }
        setDsNames(names)
        // Hubs are not candidates for connecting, so they do not count.
        setPublishedCount(all.filter((d) => !d.isCrosswalk && d.statusKind === 'pub').length)
      })
      .catch(() => undefined)
    return () => {
      off = true
    }
  }, [reloads])

  const list = perspectives ?? []
  const selected = list.find((p) => p.perspective_id === selectedId) ?? list[0] ?? null

  function pname(p: CrosswalkPerspective): string {
    return perspectiveDisplayName(p) ?? t('crosswalk:view.unnamed')
  }

  /** A participant's dataset name (today's), falling back to the stored label. */
  function dsName(datasetId: string, label: string): string {
    return dsNames[datasetId] || label || datasetId
  }

  async function onRebuild() {
    if (!selected) return
    setRebuilding(true)
    setRebuildErr('')
    setNote('')
    setSkippedNote('')
    try {
      const r = await buildPerspective(selected.perspective_id) // no config → rebuild persisted
      setNote(
        t('crosswalk:view.rebuildNote', {
          shared: r.shared_total,
          count: r.participants_used.length,
        }),
      )
      // A count that quietly shrank is the one thing people read as breakage. Say
      // which data dropped out, and how it comes back.
      if (r.participants_skipped.length > 0) {
        setSkippedNote(
          t('crosswalk:view.rebuildSkipped', {
            names: r.participants_skipped
              .map((s) => dsName(s.dataset_id, s.label))
              .join(t('crosswalk:create.confirm.join')),
          }),
        )
      }
      load()
    } catch (e) {
      setRebuildErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRebuilding(false)
    }
  }

  // NO "stop this connection" button yet, on purpose. `POST /api/datasets/{id}/retract`
  // looks like the wiring for it, but for a crosswalk it retracts the WRONG graph:
  // the registry id is `crosswalk-bridge` while the data lives in
  // `…/canonical/crosswalk`, so the marker lands on a graph that does not exist —
  // and `list_perspectives` does not filter on status, so the "stopped" connection
  // would still be listed AND still answer questions. A button that promises removal
  // and delivers neither is worse than saying what is true today, so the confirm
  // screen now promises a rebuild instead (see audit/handoff-crosswalk.md).

  const concepts = selected?.config?.concepts ?? []
  const participants = concepts.flatMap((c) => c.participants)
  const shared = selected?.dataset?.crosswalk_shared_compositions
  /** Known to be short of the two published datasets connecting needs. `null` means
   * "not known", which is deliberately NOT "too few" (fail open). */
  const tooFewPublished = publishedCount !== null && publishedCount < 2

  /** Try-it questions for the connection on screen — the "what now?" the just-built
   * screen offers, kept available for a connection someone comes back to. The words
   * are the datasets' current names plus the SERVER-resolved label for what they
   * connect on; without that label the questions ask about "values" instead, so no
   * key ever reaches a question box. */
  const askQuestions: string[] = (() => {
    const c = concepts[0]
    if (!c) return []
    const label = conceptSentenceLabel(c)
    const names = c.participants.map((p) => dsName(p.dataset_id, p.name || p.label))
    const out: string[] = []
    if (names.length >= 2) {
      const values = { a: names[0], b: names[1], label }
      out.push(
        label
          ? t('crosswalk:create.done.askQ2', values)
          : t('crosswalk:create.done.askQ2Plain', values),
      )
    }
    out.push(
      label
        ? t('crosswalk:create.done.askQ3', { label })
        : t('crosswalk:create.done.askQ3Plain'),
    )
    return out
  })()

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
          onBuilt={(id) => {
            // Select what was just made: "see this connection" must not land on
            // whichever one happened to be open before.
            setSelectedId(id)
            load()
          }}
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
      </div>

      {/* A failure on the shared screen says what happened and what to do; the raw
          HTTP/JSON stays reachable, folded, for whoever needs it. */}
      {err && (
        <div className="state-block">
          <p className="state-title">{t(crosswalkError(err).title)}</p>
          <p className="state-sub">{t(crosswalkError(err).body)}</p>
          <div className="kz-actions">
            <button
              type="button"
              onClick={() => {
                setErr('')
                setPerspectives(null)
                setReloads((n) => n + 1)
              }}
            >
              {t('crosswalk:view.retryBtn')}
            </button>
          </div>
          <details className="kz-stop-detail">
            <summary>{t('crosswalk:create.details')}</summary>
            <pre className="error">{err}</pre>
          </details>
        </div>
      )}
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
              {tooFewPublished
                ? t('crosswalk:view.empty.needTwo', { count: publishedCount })
                : list.length === 0
                  ? t('crosswalk:view.empty.sub')
                  : t('crosswalk:create.bandSub')}
            </p>
          </div>
          {/* K23: 作る前の人にとって「つながり」はまだ像を結んでいない。1 枚の絵で
              「データ—共通の値—データ」を言う。実在のデータ名はまだ無いので、
              ここだけは総称で描く（在るように見せない）。 */}
          {list.length === 0 && (
            <XwLinkDiagram
              headline={t('crosswalk:view.diagram.head')}
              note={t('crosswalk:view.diagram.note')}
              sides={[
                { key: 'a', name: t('crosswalk:view.diagram.a') },
                { key: 'b', name: t('crosswalk:view.diagram.b') },
              ]}
            />
          )}
          <div className="xw-create-band-actions">
            {/* With fewer than two published datasets the scan can only end in a
                refusal, so the offer becomes the step that actually helps. Only when
                the count is KNOWN: an unread or failed catalog leaves the search
                button alone rather than blocking on a guess. */}
            {tooFewPublished && onAddData ? (
              <button type="button" onClick={onAddData}>
                {t('crosswalk:view.empty.addBtn')}
              </button>
            ) : (
              <button type="button" onClick={() => onCreateMode?.(true)}>
                {t('crosswalk:view.empty.btn')}
              </button>
            )}
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
              {/* One sentence, no interpolation: what the big number counts. The rest
                  of what the stats mean is the card underneath, not a caption. */}
              <p className="xw-summary-note">{t('crosswalk:view.summary.note')}</p>

              {concepts.map((c) => (
                <div className="xw-concept" key={c.name}>
                  <div className="ds-subhead" title={c.name}>
                    {t('crosswalk:view.conceptHead', {
                      name:
                        conceptName(c.name, c.concept_label) ??
                        t('crosswalk:create.sharedValueLabel'),
                    })}
                  </div>

                  {/* K23: 同じ 3 つの事実（どのデータ同士が／どの値で／何を同じと
                      みなして）を 1 枚の絵にまとめる。チップの行と件数バッジと
                      同一視の 1 文が画面の別々の場所に散っていた。
                      The node says WHICH data and WHICH field, in the words those
                      things have today; the IRIs and their local names stay in the
                      tooltip for whoever needs them. */}
                  <XwLinkDiagram
                    headline={t('crosswalk:view.diagram.head')}
                    /* Say what counts as the same value in a sentence — a raw
                       normalizer id ("nfkc") means nothing outside the codebase.
                       It belongs ON the link, not as a separate note above it:
                       that IS what the link does. */
                    note={
                      c.key_parts && c.key_parts.length > 0
                        ? t('crosswalk:view.compoundKeyHint', {
                            parts: c.key_parts
                              .map(
                                (kp) =>
                                  conceptName(kp.name) ??
                                  t('crosswalk:create.sharedValueLabel'),
                              )
                              .join(' × '),
                          })
                        : t(sameAsKey(c.normalizer ?? 'identity'))
                    }
                    sides={c.participants.map((p) => {
                      // single-part = one predicate; compound = one per key part.
                      const preds = p.predicate
                        ? [p.predicate]
                        : Object.values(p.predicates ?? {})
                      return {
                        key: p.dataset_id,
                        name: dsName(p.dataset_id, p.name || p.label),
                        field: p.predicate_label ?? undefined,
                        title: [...preds, ...preds.map(localName)].join(', '),
                      }
                    })}
                  />
                </div>
              ))}

              {/* The point of having a connection, said where someone lands when they
                  come back to it later — the same offer the just-built screen makes. */}
              {onOpenAsk && askQuestions.length > 0 && (
                <>
                  <p className="kz-note">{t('crosswalk:view.askLead')}</p>
                  <div className="kz-q-options">
                    {askQuestions.map((q) => (
                      <button
                        key={q}
                        type="button"
                        className="kz-pill"
                        onClick={() => onOpenAsk(q)}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                  <p className="kz-note">{t('crosswalk:create.done.askHint')}</p>
                </>
              )}

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
              {skippedNote && <p className="kz-note kz-caution">{skippedNote}</p>}
              {rebuildErr && (
                <div className="state-block">
                  <p className="state-title">{t(crosswalkError(rebuildErr).title)}</p>
                  <p className="state-sub">{t(crosswalkError(rebuildErr).body)}</p>
                  <details className="kz-stop-detail">
                    <summary>{t('crosswalk:create.details')}</summary>
                    <pre className="error">{rebuildErr}</pre>
                  </details>
                </div>
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

      {/* 全体像（データ・つながり・外部標準の 3 レーン図）を常時ここに埋め込む
          （旧「全体像を見る」ボタンは廃止 — 別画面へ移らせず、同じページの
          下にスクロールすれば見える）。データ取得は OntologyMapView 側で
          独自に行う（この段では二重取得を許容 — 別途キャッシュ化を検討）。 */}
      <div className="ds-subhead">{t('crosswalk:view.mapHead')}</div>
      <OntologyMapView
        embedded
        onAddData={onAddData}
        onCreateConnection={() => onCreateMode?.(true)}
        // ハブ節のクリックは別画面へ飛ばさず、同じ画面内の選択を更新して
        // 先頭へ戻す（アドレスバー直書きの既定動作には落とさない）。
        onOpenConnections={(perspectiveId) => {
          setSelectedId(perspectiveId ?? null)
          window.scrollTo({ top: 0, behavior: 'smooth' })
        }}
      />
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

function usePerspName(): (p: CrosswalkPerspective) => string {
  const { t } = useTranslation()
  return (p) => perspectiveDisplayName(p) ?? t('crosswalk:view.unnamed')
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
  const perspName = usePerspName()
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
