import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import {
  buildPerspective,
  deletePerspective,
  type CrosswalkPerspective,
  type DiscoverCandidate,
  getCrosswalks,
} from './crosswalkApi'
import { CrosswalkBuilder, type CrosswalkSeed } from './CrosswalkBuilder'
import { CrosswalkCreate } from './CrosswalkCreate'
import {
  conceptName,
  conceptSentenceLabel,
  crosswalkError,
  fieldDisplay,
  perspectiveDisplayName,
  sameAsKey,
} from './crosswalkLabels'
import { getCatalogDatasets } from './galleryApi'
import { ArrowIcon, ConnectIcon, LinkIcon } from './icons'
import { OntologyMapView } from './OntologyMapView'
import { ToolsPanel } from './ToolsPanel'
import { localName } from './vocab'
import { vocabScopeHash } from './vocabQuestion'
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
  const [deleting, setDeleting] = useState(false)
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

  /** つながりの削除（利用者要望 2026-09-02）。retract の流用は誤りだった
   *  （registry id と hub グラフがずれ、消えたふりだけになる — 旧コメント参照）。
   *  DELETE /api/crosswalk/{id} は hub グラフと登録を本当に消す。元のデータ
   *  セットには触れず、同じ設定でいつでも作り直せる可逆な操作。 */
  async function onDelete() {
    if (!selected) return
    if (!window.confirm(t('crosswalk:view.deleteConfirm', { name: pname(selected) }))) return
    setDeleting(true)
    setRebuildErr('')
    setNote('')
    setSkippedNote('')
    try {
      await deletePerspective(selected.perspective_id)
      setSelectedId(null)
      setNote(t('crosswalk:view.deleted'))
      load()
    } catch (e) {
      setRebuildErr(e instanceof Error ? e.message : String(e))
    } finally {
      setDeleting(false)
    }
  }

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
                <span className="xw-persp-name">
                  {pname(p)}
                  {p.dataset?.auto_linked && (
                    <span className="xw-auto-linked-mark" title={t('crosswalk:auto_linked')}>
                      {' '}
                      ☑
                    </span>
                  )}
                </span>
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
                        field: p.predicate_label
                          ? fieldDisplay({
                              predicate: preds[0] ?? '',
                              predicate_label: p.predicate_label,
                              subject_class_label: p.subject_class_label,
                            })
                          : undefined,
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
                <button
                  type="button"
                  className="btn btn--ghost btn--sm xw-delete-btn"
                  disabled={rebuilding || deleting}
                  onClick={onDelete}
                >
                  {deleting ? t('crosswalk:view.deleting') : t('crosswalk:view.delete')}
                </button>
                {selected.dataset?.crosswalk_built_at && (
                  <span className="xw-built-at">
                    {t('crosswalk:view.builtAt', {
                      at: selected.dataset.crosswalk_built_at.slice(0, 19).replace('T', ' '),
                    })}
                  </span>
                )}
                {selected.dataset?.auto_linked && (
                  <span className="xw-auto-linked-mark">☑ {t('crosswalk:auto_linked')}</span>
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
          {/* つながりどうしの対応づけ（旧 PerspectiveAlignment）は「ことば」画面の「線」へ移った。 */}
          <p className="xw-manual-note">
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                window.location.hash = vocabScopeHash('perspective')
              }}
            >
              {t('crosswalk:align.toVocab')}
            </button>
          </p>
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
    subjectClass: Object.fromEntries(
      c.participants.filter((p) => p.subject_class).map((p) => [p.dataset_id, p.subject_class!]),
    ),
    candidates: Object.fromEntries(
      c.participants.map((p) => [
        p.dataset_id,
        [
          {
            iri: p.predicate,
            sample: c.samples[0]?.raw[p.dataset_id] ?? '',
            subject_class: p.subject_class ?? null,
            subject_class_label: p.subject_class_label ?? null,
            label: p.predicate_label,
          },
        ],
      ]),
    ),
    concept: c.concept,
    normalizer: c.normalizer,
    perspectiveName: c.name,
  }
}
