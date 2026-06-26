import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from 'react'
import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import {
  ingestDataset,
  IngestValidationError,
  type IngestProgress,
  type IngestResult,
} from './api'
import { type CrosswalkPerspective, getCrosswalks } from './crosswalkApi'
import { DatasetGrounding } from './DatasetGrounding'
import {
  type AlignmentReport,
  appendDocument,
  appendToDataset,
  type AppendResult,
  type CatalogDataset,
  type CatalogStatusKind,
  datasetStage,
  deleteDataset,
  type DocumentAppendResult,
  getAlignment,
  getCatalogDatasets,
  type LiveDataset,
  promoteDataset,
  reinstateDataset,
  renameDataset,
  retractDataset,
} from './galleryApi'
import { ArrowIcon, ConnectIcon, DataIcon, FileIcon, LayersIcon, SearchIcon } from './icons'
import { IngestProgressView } from './IngestProgressView'
import { Mermaid } from './Mermaid'
import { ToolsPanel } from './ToolsPanel'
import { localName } from './vocab'

type DetailTab = 'structure' | 'tools' | 'files' | 'connect' | 'design'

/** Display label for a dataset's source kind (used as the small mono type tag). */
function sourceTag(meta?: LiveDataset['meta']): string {
  const k = meta?.source_kind
  return k === 'json' ? 'JSON' : k === 'xml' ? 'DOC' : 'CSV'
}

/** How many crosswalk perspectives this dataset participates in (for the card meta). */
function connectionCount(d: CatalogDataset, perspectives: CrosswalkPerspective[]): number {
  const ids = new Set([d.id, d.live?.meta.id].filter(Boolean) as string[])
  return perspectives.filter((p) =>
    (p.config?.concepts ?? []).some((c) => c.participants.some((part) => ids.has(part.dataset_id))),
  ).length
}

function statusLabel(t: (k: string) => string, kind: CatalogStatusKind): string {
  return t(`gallery:status.${kind}`)
}

/**
 * Catalog — datasets are the entry point (design_handoff_asterism_ux #5). Each
 * dataset HAS a 設計図 (vocabulary) and 取り込みルール (mapping), shown as two tabs
 * inside the dataset; the SHARED vocabulary is the gateway band at the bottom.
 *
 * All datasets are REAL and LIVE: the workbench-materialized drafts persisted to
 * /api/datasets (getCatalogDatasets), each with its designed classes (model.yaml),
 * class diagram (diagram.md), and the external vocabularies it actually reuses
 * (derived from real term IRIs). No fixtures, no demo placeholders.
 */
export function GalleryView({
  focusClass,
  onOpenCrosswalk,
  onOpenMap,
  onAddData,
}: {
  focusClass?: string | null
  onOpenCrosswalk?: () => void
  onOpenMap?: () => void
  onAddData?: () => void
}) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  const [error, setError] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [seenFocus, setSeenFocus] = useState<string | null | undefined>(focusClass)
  const [tab, setTab] = useState<DetailTab>('structure')
  // Crosswalk perspectives — used by each dataset's つながり (connections) tab and
  // for the per-card connection count.
  const [perspectives, setPerspectives] = useState<CrosswalkPerspective[]>([])
  // Client-side dataset search (by name) over the full-width grid.
  const [query, setQuery] = useState('')

  function reload() {
    getCatalogDatasets()
      .then((d) => setDatasets(d))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    let cancelled = false
    getCatalogDatasets()
      .then((d) => {
        if (!cancelled) setDatasets(d)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    getCrosswalks()
      .then((r) => !cancelled && setPerspectives(r))
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // Reset the explicit pick when arriving with a new Ask focus (setState during
  // render — the "adjust state on prop change" pattern; avoids effect cascades).
  const list = (datasets ?? []).filter((d) => !d.isCrosswalk)
  // Arriving with a new Ask focus opens that dataset's detail directly.
  if (focusClass !== seenFocus) {
    setSeenFocus(focusClass)
    const f = focusClass ? list.find((d) => d.classes.includes(focusClass)) : undefined
    setPicked(f ? f.id : null)
  }
  // Default view is the full-width grid; a dataset is opened on demand (v2 #5).
  const selected = picked ? (list.find((d) => d.id === picked) ?? null) : null
  const filtered = query.trim()
    ? list.filter((d) => d.name.toLowerCase().includes(query.trim().toLowerCase()))
    : list

  return (
    <div className="catalog">
      {error && <pre className="error">{error}</pre>}

      {!datasets && !error && (
        <p className="loading-row">
          <span className="spinner" />
          {t('gallery:loading')}
        </p>
      )}

      {datasets && list.length === 0 && (
        <div className="state-block">
          <span className="state-icon state-icon--primary">
            <SearchIcon size={26} />
          </span>
          <p className="state-title">{t('gallery:empty.title')}</p>
          <p className="state-sub">{t('gallery:empty.sub')}</p>
          {onAddData && (
            <button type="button" className="promote-btn" onClick={onAddData}>
              {t('gallery:grid.addTitle')}
            </button>
          )}
        </div>
      )}

      {/* Detail (full width). Back returns to the grid; keyed by id so each
          dataset's controls remount fresh (no state leak across datasets). */}
      {datasets && selected && (
        <DatasetDetail
          key={selected.id}
          dataset={selected}
          perspectives={perspectives}
          tab={tab}
          onTab={setTab}
          highlight={focusClass}
          onChanged={reload}
          onBack={() => setPicked(null)}
          onOpenCrosswalk={onOpenCrosswalk}
          onOpenMap={onOpenMap}
        />
      )}

      {/* Full-width 3-column grid (v2 ScreenDatasets) + add tile. */}
      {datasets && !selected && list.length > 0 && (
        <>
          <div className="catalog-toolbar">
            <p className="catalog-intro">
              <Trans i18nKey="gallery:intro">
                作った<strong>データセット</strong>が主役です。各データセットは「<strong>設計図（語彙）</strong>」と
                「<strong>取り込みルール</strong>」を持ちます。共通で使う語彙は下にまとめています。
              </Trans>
            </p>
            <label className="catalog-search">
              <SearchIcon size={15} />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t('gallery:grid.search')}
              />
            </label>
          </div>

          {focusClass && (
            <div className="vocab-focus-banner">
              {t('gallery:focusBanner.label')}
              <strong>{focusClass}</strong>
              <span className="vocab-focus-sub">{t('gallery:focusBanner.sub')}</span>
            </div>
          )}

          <div className="ds-grid">
            {filtered.map((d) => (
              <DatasetGridCard
                key={d.id}
                dataset={d}
                connections={connectionCount(d, perspectives)}
                onSelect={(t) => {
                  setTab(t ?? 'structure')
                  setPicked(d.id)
                }}
                onChanged={reload}
              />
            ))}
            {onAddData && (
              <button type="button" className="ds-add-tile" onClick={onAddData}>
                <span className="ds-add-plus">+</span>
                <span className="ds-add-title">{t('gallery:grid.addTitle')}</span>
                <span className="ds-add-sub">{t('gallery:grid.addSub')}</span>
              </button>
            )}
          </div>
        </>
      )}

    </div>
  )
}

function DatasetGridCard({
  dataset,
  connections,
  onSelect,
  onChanged,
}: {
  dataset: CatalogDataset
  connections: number
  onSelect: (tab?: DetailTab) => void
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const meta = dataset.live?.meta
  const files = meta?.source_files?.length ?? 0
  const updated = meta?.created_at?.slice(0, 10) ?? ''
  // The card is the click target to open the detail; the action footer holds
  // real <button>s, so the card itself is a clickable div (not a <button>, which
  // cannot legally nest interactive children).
  function open(tab?: DetailTab) {
    onSelect(tab)
  }
  return (
    <div
      className="ds-grid-card"
      role="button"
      tabIndex={0}
      onClick={() => open()}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          open()
        }
      }}
    >
      <div className="ds-grid-card-head">
        <span className="ds-grid-card-icon">
          <DataIcon size={18} />
        </span>
        <span className="ds-grid-card-titles">
          <span className="ds-grid-card-name">{dataset.name}</span>
          <span className="ds-grid-card-type">{sourceTag(meta)}</span>
        </span>
        <span className={`status-pill status-pill--${dataset.statusKind}`}>
          {statusLabel(t, dataset.statusKind)}
        </span>
      </div>
      <div className="ds-grid-card-counts">
        {dataset.counts.map((c) => (
          <span className="ds-row-count" key={c.label}>
            <span className="ds-row-count-val">{c.value}</span> {c.label}
          </span>
        ))}
      </div>
      <div className="ds-grid-card-meta">
        <span className="ds-grid-card-metaitem">
          <FileIcon size={13} /> {t('gallery:grid.metaFiles')} <b>{files}</b>
        </span>
        <span className="ds-grid-card-metaitem">
          <ConnectIcon size={13} /> {t('gallery:grid.metaLinks')} <b>{connections}</b>
        </span>
        {updated && <span className="ds-grid-card-updated">{updated}</span>}
      </div>
      {meta && <CardActions meta={meta} onChanged={onChanged} onOpen={open} />}
    </div>
  )
}

/**
 * Dataset-level state actions on the catalog card (moved here from the detail's
 * always-visible band — that band showed on every tab and felt awkward). State is
 * derived from `meta` with the SAME logic the detail uses (datasetStage / status /
 * version) and the SAME galleryApi calls (promote / retract / reinstate / delete),
 * so card and detail never disagree.
 *
 *   - ingested (draft) → 「検索対象として公開」(promote) primary CTA. A re-stage of an
 *     already-published version (version ≥ 1) is a re-promote whose alignment preview
 *     lives in the detail, so that case opens the detail instead of one-clicking.
 *   - promoted, active → 「撤回」(retract, confirm).
 *   - promoted, retracted → 「復帰」(reinstate).
 *   - always → 「削除」(delete) tucked behind a compact ⋯ menu (window.confirm gated).
 *
 * The richer flows (first ingest needing a CSV, promote preview/options, append,
 * re-ingest) stay in the detail; the card only carries quick one-click actions.
 */
function CardActions({
  meta,
  onChanged,
  onOpen,
}: {
  meta: LiveDataset['meta']
  onChanged: () => void
  onOpen: (tab?: DetailTab) => void
}) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [menu, setMenu] = useState(false)
  const stage = datasetStage(meta)
  const retracted = meta.status === 'retracted'
  const version = meta.version ?? 0

  // Stop card-open when interacting with the action footer.
  function stop(e: ReactMouseEvent | ReactKeyboardEvent) {
    e.stopPropagation()
  }

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label)
    setErr('')
    try {
      await fn()
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
      setMenu(false)
    }
  }

  // A staged draft of an already-published dataset (version ≥ 1) is a re-promote;
  // its alignment preview / version bump belongs in the detail (Files tab), so the
  // card "publish" routes there rather than one-clicking.
  const isRepromote = stage === 'ingested' && version >= 1

  return (
    <div className="ds-card-actions" onClick={stop} onKeyDown={stop} role="presentation">
      {stage === 'ingested' &&
        (isRepromote ? (
          <button
            type="button"
            className="btn btn--soft btn--sm ds-card-cta"
            onClick={() => onOpen('files')}
          >
            {t('gallery:card.publish')}
          </button>
        ) : (
          <button
            type="button"
            className="btn btn--soft btn--sm ds-card-cta"
            disabled={!!busy}
            onClick={() =>
              run('promote', async () => {
                await promoteDataset(meta.id)
              })
            }
          >
            {busy === 'promote' ? t('gallery:promote.promoting') : t('gallery:card.publish')}
          </button>
        ))}

      {stage === 'promoted' && !retracted && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={!!busy}
          onClick={() =>
            window.confirm(t('gallery:lifecycle.retractConfirm')) &&
            run('retract', async () => {
              await retractDataset(meta.id)
            })
          }
        >
          {busy === 'retract' ? t('gallery:lifecycle.retracting') : t('gallery:card.retract')}
        </button>
      )}

      {retracted && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={!!busy}
          onClick={() =>
            run('reinstate', async () => {
              await reinstateDataset(meta.id)
            })
          }
        >
          {busy === 'reinstate' ? t('gallery:lifecycle.reinstating') : t('gallery:card.reinstate')}
        </button>
      )}

      {/* Compact ⋯ menu keeps the destructive action out of the way. */}
      <div className="ds-card-menu-wrap">
        <button
          type="button"
          className="ds-card-menu-btn"
          aria-label={t('gallery:card.more')}
          aria-haspopup="menu"
          aria-expanded={menu}
          disabled={!!busy}
          onClick={() => setMenu((m) => !m)}
        >
          ⋯
        </button>
        {menu && (
          <div className="ds-card-menu" role="menu">
            <button
              type="button"
              role="menuitem"
              className="ds-card-menu-item ds-card-menu-item--danger"
              disabled={!!busy}
              onClick={() => {
                const promoted = stage === 'promoted'
                const ok = window.confirm(
                  promoted
                    ? t('gallery:lifecycle.deleteConfirmPromoted')
                    : t('gallery:lifecycle.deleteConfirm'),
                )
                if (ok)
                  run('delete', async () => {
                    await deleteDataset(meta.id, promoted)
                  })
              }}
            >
              {busy === 'delete' ? t('gallery:lifecycle.deleting') : t('gallery:lifecycle.delete')}
            </button>
          </div>
        )}
      </div>

      {err && <p className="ds-card-err">{t('gallery:lifecycle.error', { message: err })}</p>}
    </div>
  )
}

function DatasetDetail({
  dataset,
  perspectives,
  tab,
  onTab,
  highlight,
  onChanged,
  onBack,
  onOpenCrosswalk,
  onOpenMap,
}: {
  dataset: CatalogDataset
  perspectives: CrosswalkPerspective[]
  tab: DetailTab
  onTab: (t: DetailTab) => void
  highlight?: string | null
  onChanged: () => void
  onBack?: () => void
  onOpenCrosswalk?: () => void
  onOpenMap?: () => void
}) {
  const { t } = useTranslation()
  const meta = dataset.live?.meta
  // 取り込んだファイル: the design-time source + every appended batch (no dedupe
  // detection yet — that needs a backend content hash; surfaced as a note).
  const fileRows: { name: string; type: string; when: string; tag: string }[] = []
  if (meta) {
    const typeLabel = sourceTag(meta)
    for (const f of meta.source_files ?? [])
      fileRows.push({ name: f, type: typeLabel, when: meta.created_at.slice(0, 10), tag: t('gallery:files.source') })
    for (const a of meta.appends ?? [])
      for (const bf of a.batch_files)
        fileRows.push({
          name: bf,
          type: typeLabel,
          when: a.appended_at.slice(0, 10),
          tag: t('gallery:files.batch', { seq: a.seq }),
        })
  }
  // つながり: the crosswalk perspectives this dataset participates in.
  const myIds = new Set([dataset.id, meta?.id].filter(Boolean) as string[])
  const myPersp = perspectives.filter((p) =>
    (p.config?.concepts ?? []).some((c) => c.participants.some((part) => myIds.has(part.dataset_id))),
  )
  const tabs: [DetailTab, string][] = [
    ['structure', t('gallery:tab.structure')],
    ['tools', t('gallery:tab.tools')],
    ['files', t('gallery:tab.files')],
    ['connect', t('gallery:tab.connect')],
    ['design', t('gallery:tab.design')],
  ]
  // dataset rename (kept from #231) — inline edit in the detail header.
  const [editingName, setEditingName] = useState(false)
  const [draftName, setDraftName] = useState(dataset.name)
  const [renaming, setRenaming] = useState(false)
  const [renameErr, setRenameErr] = useState('')

  async function saveRename() {
    const n = draftName.trim()
    if (!n || n === dataset.name) {
      setEditingName(false)
      return
    }
    setRenaming(true)
    setRenameErr('')
    try {
      // CatalogDataset.id is the synthetic catalog id (`live-<id>`); the registry id is
      // dataset.live.meta.id (what every other control uses). Strip the prefix as a fallback.
      const realId = dataset.live?.meta.id ?? dataset.id.replace(/^live-/, '')
      await renameDataset(realId, n)
      setEditingName(false)
      onChanged() // name changed — refresh the catalog list + detail
    } catch (e) {
      setRenameErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRenaming(false)
    }
  }

  return (
    <div className="ds-detail-wrap">
      {onBack && (
        <button type="button" className="vocab-back ds-detail-back" onClick={onBack}>
          <ArrowIcon size={14} className="vocab-back-arrow" /> {t('gallery:detail.back')}
        </button>
      )}
      <div className="ds-detail card">
      <div className="ds-detail-head">
        {editingName ? (
          <span className="ds-rename">
            <input
              className="ds-rename-input"
              type="text"
              value={draftName}
              autoFocus
              disabled={renaming}
              placeholder={t('gallery:rename.placeholder')}
              onChange={(e) => setDraftName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') saveRename()
                if (e.key === 'Escape') setEditingName(false)
              }}
            />
            <button type="button" className="ds-rename-save" onClick={saveRename} disabled={renaming}>
              {t('gallery:rename.save')}
            </button>
            <button
              type="button"
              className="ds-rename-cancel"
              onClick={() => {
                setEditingName(false)
                setDraftName(dataset.name)
                setRenameErr('')
              }}
              disabled={renaming}
            >
              {t('gallery:rename.cancel')}
            </button>
          </span>
        ) : (
          <h2 className="ds-detail-name">
            {dataset.name}
            <button
              type="button"
              className="ds-rename-edit"
              title={t('gallery:rename.edit')}
              aria-label={t('gallery:rename.edit')}
              onClick={() => {
                setDraftName(dataset.name)
                setEditingName(true)
              }}
            >
              ✎
            </button>
          </h2>
        )}
        <span className={`status-pill status-pill--${dataset.statusKind}`}>
          {statusLabel(t, dataset.statusKind)}
        </span>
        {renameErr && <pre className="error">{renameErr}</pre>}
        <div className="ds-tabs">
          {tabs.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`ds-tab${tab === id ? ' active' : ''}`}
              onClick={() => onTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {dataset.purposes.length > 0 && (
        <div className="ds-purposes">
          <div className="ds-purposes-label">
            <SearchIcon size={13} /> {t('gallery:purposes.label')}
          </div>
          <div className="ds-purpose-tags">
            {dataset.purposes.map((p) => (
              <span key={p.tag} className="purpose-pill" title={p.detail}>
                {p.tag}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Dataset-level state actions (retract / reinstate / delete / quick publish)
          now live on the catalog cards (CardActions), not in an always-visible band
          here — that band showed on every tab and felt awkward. The detail keeps the
          richer flows (first ingest, promote preview, append, re-ingest) in the
          Files tab below. */}

      {/* 設計図 (schema): the dataset's structure — classes, predicates, and the
          class diagram (always shown, the centerpiece of this page). */}
      {tab === 'structure' && (
        <div className="ds-tab-body">
          <div className="ds-section-head">
            <span className="ds-section-title">{t('gallery:design.title')}</span>
            <span className="ds-section-note">{t('gallery:design.classCount', { n: dataset.classes.length })}</span>
          </div>
          {dataset.classes.length > 0 ? (
            <div className="ds-classes">
              {dataset.classes.map((c) => (
                <span key={c} className={`class-chip${c === highlight ? ' onto-class-chip--focus' : ''}`}>
                  <code className="class-chip-en">{c}</code>
                </span>
              ))}
            </div>
          ) : (
            <p className="ds-empty-note">{t('gallery:design.noClasses')}</p>
          )}

          {dataset.predicates.length > 0 && (
            <>
              <div className="ds-subhead">{t('gallery:design.predicatesHead')}</div>
              <div className="ds-classes">
                {dataset.predicates.map((p) => (
                  <span key={p} className="class-chip" title={p}>
                    <code className="class-chip-en">{localName(p)}</code>
                  </span>
                ))}
              </div>
            </>
          )}

          {dataset.mermaid && (
            <div className="ds-diagram-block">
              <div className="ds-subhead">{t('gallery:design.diagramSummary')}</div>
              <div className="onto-diagram">
                <Mermaid chart={dataset.mermaid} />
              </div>
            </div>
          )}
        </div>
      )}

      {/* ツール (tools): the typed tools this dataset can answer questions with. */}
      {tab === 'tools' && (
        <div className="ds-tab-body">
          <div className="ds-section-head">
            <span className="ds-section-title">{t('gallery:detail.tools')}</span>
          </div>
          {dataset.live ? (
            <ToolsPanel datasetId={dataset.live.meta.id} />
          ) : (
            <p className="ds-empty-note">{t('gallery:tools.none')}</p>
          )}
        </div>
      )}

      {/* 取り込んだファイル (files): every file that makes up this dataset. */}
      {tab === 'files' && (
        <div className="ds-tab-body">
          <p className="ds-dedupe-note">
            <span className="ds-dedupe-check">✓</span> {t('gallery:files.dedupe')}
          </p>
          {fileRows.length > 0 ? (
            <div className="ds-files-table">
              <div className="ds-files-head">
                <span>{t('gallery:files.colName')}</span>
                <span>{t('gallery:files.colType')}</span>
                <span>{t('gallery:files.colWhen')}</span>
                <span>{t('gallery:files.colStatus')}</span>
              </div>
              {fileRows.map((f, i) => (
                <div className="ds-files-row" key={`${f.name}-${i}`}>
                  <span className="ds-file-name">
                    <FileIcon size={14} /> <code>{f.name}</code>
                  </span>
                  <span>{f.type}</span>
                  <span className="ds-file-when">
                    {f.when} <span className="ds-file-tag">{f.tag}</span>
                  </span>
                  <span className="ds-file-status">✓ {t('gallery:files.statusIngested')}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="ds-empty-note">{t('gallery:files.none')}</p>
          )}
          <p className="ds-files-future">{t('gallery:files.futureDedup')}</p>

          {/* Operations on this dataset's ingested data live here (the natural home
              for ingest / append / re-ingest / promote / lifecycle). State-gated, so
              usually only one or two are visible for a given dataset stage. */}
          {dataset.live && (
            <div className="ds-detail-controls">
              {/* Task E: ingest gate for design-stage datasets (no facts yet). */}
              <IngestControl meta={dataset.live.meta} onChanged={onChanged} />
              {/* S4 (re-)promote human-gate. */}
              <PromoteControl meta={dataset.live.meta} onChanged={onChanged} />
              {/* incremental-ingest.md: append a new batch to a promoted live feed. */}
              <AppendControl meta={dataset.live.meta} onChanged={onChanged} />
              {/* document layer: add another document to a promoted document dataset. */}
              <DocumentAppendControl meta={dataset.live.meta} onChanged={onChanged} />
              {/* part5: safe replace — re-ingest into a new version, then re-promote. */}
              <ReingestControl meta={dataset.live.meta} onChanged={onChanged} />
            </div>
          )}
        </div>
      )}

      {/* つながり (connections): the crosswalks this dataset takes part in. */}
      {tab === 'connect' && (
        <div className="ds-tab-body">
          <div className="ds-section-head">
            <span className="ds-section-title">{t('gallery:connect.head')}</span>
          </div>
          {myPersp.length > 0 ? (
            <div className="ds-conn-list">
              {myPersp.map((p) => (
                <div className="ds-conn-item" key={p.perspective_id}>
                  <span className="ds-conn-icon">
                    <ConnectIcon size={15} />
                  </span>
                  <span className="ds-conn-name">{p.dataset?.name || p.perspective_id}</span>
                  <span className="ds-conn-concept">
                    {t('gallery:connect.concept', {
                      concept: (p.config?.concepts ?? []).map((c) => c.name).join(' · '),
                    })}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="ds-empty-note">{t('gallery:connect.none')}</p>
          )}
          <div className="ds-conn-links">
            {onOpenCrosswalk && (
              <button type="button" className="btn btn--ghost btn--sm" onClick={onOpenCrosswalk}>
                <ConnectIcon size={14} /> {t('gallery:connect.seeAll')}
              </button>
            )}
            {onOpenMap && (
              <button type="button" className="btn btn--ghost btn--sm" onClick={onOpenMap}>
                <LayersIcon size={14} /> {t('gallery:connect.seeMap')}
              </button>
            )}
          </div>
        </div>
      )}

      {/* 設計 (design): the ingest rules, reused vocabularies, and grounding. */}
      {tab === 'design' && (
        <div className="ds-tab-body">
          <div className="ds-section-head">
            <span className="ds-section-title">{t('gallery:rules.title')}</span>
          </div>
          {dataset.artifacts.length > 0 ? (
            <div className="ds-artifacts">
              {dataset.artifacts.map((a) => (
                <div key={a.name} className="ds-artifact">
                  <span className="ds-artifact-kind">{a.kind}</span>
                  <code className="ds-artifact-name">{a.name}</code>
                  <span className="ds-artifact-detail">{a.detail}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="ds-empty-note">{t('gallery:rules.empty')}</p>
          )}

          {dataset.reuses.length > 0 && (
            <>
              <div className="ds-subhead">{t('gallery:design.reusesHead')}</div>
              <div className="ds-reuse-list">
                {dataset.reuses.map((r) => (
                  <span key={r.prefix} className="reuse-chip" title={t(r.what)}>
                    <code>{r.prefix}</code>
                    <span className="reuse-chip-what">{t(r.what)}</span>
                  </span>
                ))}
              </div>
            </>
          )}

          {dataset.classIris.length + dataset.predicates.length > 0 && (
            <DatasetGrounding dataset={dataset} />
          )}
        </div>
      )}
      </div>
    </div>
  )
}

function shortIri(iri: string): string {
  const m = iri.split(/[#/]/).filter(Boolean)
  return m.length ? m[m.length - 1] : iri
}

/**
 * Task E: ingest a *design*-stage dataset straight from the catalog. A design
 * dataset has a saved schema + declarative RML but no facts (0 triples) until
 * its RML is run through the substrate into a draft graph — previously only
 * reachable inside the workbench. When the design-time source CSV was persisted
 * (workbench save), this is a one-click approve; otherwise the user re-attaches
 * the CSV here. Loads into an isolated draft graph (Ask cites canonical), so it
 * is not yet a citable fact — promote does that. Only shown for design stage.
 */
/**
 * Render an ingest failure. A design-validation error (IngestValidationError)
 * carries a structured `issues` list that we show as a readable bulleted list
 * with a heading; any other error keeps the single-line message rendering.
 */
function IngestError({
  err,
  errorKey,
}: {
  err: unknown
  errorKey: string
}) {
  const { t } = useTranslation()
  if (err instanceof IngestValidationError && err.issues.length > 0) {
    return (
      <div className="promote-err ingest-issues">
        <p className="ingest-issues-head">{t('gallery:ingest.validationHead')}</p>
        <ul>
          {err.issues.map((issue, i) => (
            <li key={i}>{issue}</li>
          ))}
        </ul>
      </div>
    )
  }
  const message = err instanceof Error ? err.message : String(err)
  return <p className="promote-err">{t(errorKey, { message })}</p>
}

function IngestControl({ meta, onChanged }: { meta: LiveDataset['meta']; onChanged: () => void }) {
  const { t } = useTranslation()
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [done, setDone] = useState<IngestResult | null>(null)
  const [err, setErr] = useState<unknown>(null)

  // Only design-stage needs this gate: ingested → promote, promoted → done.
  if (datasetStage(meta) !== 'design') return null

  if (!meta.has_rml) {
    return (
      <div className="ingest-gate">
        <div className="ds-subhead">{t('gallery:ingest.head')}</div>
        <p className="ingest-hint">{t('gallery:ingest.noRml')}</p>
      </div>
    )
  }

  if (done) {
    return (
      <div className="ingest-gate">
        <div className="ds-subhead">{t('gallery:ingest.head')}</div>
        <p className="ingest-ok">{t('gallery:ingest.done', { n: done.triple_count })}</p>
      </div>
    )
  }

  const hasSource = !!meta.has_source
  const isJson = meta.source_kind === 'json'
  const sourceLabel = isJson ? 'JSON' : 'CSV'
  const canIngest = !busy && (hasSource || files.length > 0)

  async function onIngest() {
    setBusy(true)
    setErr(null)
    setProgress(null)
    try {
      // hasSource → ingest with no upload (server uses the persisted source).
      setDone(await ingestDataset(meta.id, hasSource ? [] : files, setProgress))
      onChanged() // design → draft: refresh so promote control appears
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ingest-gate">
      <div className="ds-subhead">{t('gallery:ingest.head')}</div>
      <p className="ingest-note">
        <Trans i18nKey="gallery:ingest.note">
          承認すると、この宣言 RML を Morph-KGC が実行し（生成コードは走らず、検証済みの Tier 0
          関数だけ）、結果を<strong>隔離された下書きグラフ</strong>に投入します。Ask
          の引用面（canonical）は汚しません。
        </Trans>
      </p>
      {hasSource ? (
        <p className="ingest-source">
          {t('gallery:ingest.sourceSaved', {
            source: sourceLabel,
            files: meta.source_files?.length
              ? t('gallery:ingest.filesSuffix', { names: meta.source_files.join('、') })
              : '',
          })}
        </p>
      ) : (
        <div className="ingest-pick">
          <label className="file-btn">
            {t('gallery:ingest.pickLabel', { source: sourceLabel })}
            <input
              type="file"
              accept={isJson ? '.json,.geojson' : '.csv'}
              multiple
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
          </label>
          <span className={`file-names${files.length ? '' : ' empty'}`}>
            {files.length
              ? files.map((f) => f.name).join('、')
              : t('gallery:ingest.pickPlaceholder', { source: sourceLabel })}
          </span>
        </div>
      )}
      <button type="button" className="promote-btn" onClick={onIngest} disabled={!canIngest}>
        {busy ? t('gallery:ingest.submitting') : t('gallery:ingest.submit')}
      </button>
      {busy && <IngestProgressView progress={progress} />}
      {err != null && <IngestError err={err} errorKey="gallery:ingest.error" />}
    </div>
  )
}

/**
 * incremental-ingest.md: grow a *promoted* dataset's live feed by appending a new
 * batch (the device-feed path). Materializes ONLY the batch (O(new)) and merges it
 * into the live canonical graph, so the new facts are immediately citable while
 * existing triples / IRIs are untouched (re-emitted rows dedupe). The schema + first
 * version were human-gated at promote, so per-batch appends do not re-gate — only
 * shown for a promoted, active dataset with declarative RML.
 */
function AppendControl({ meta, onChanged }: { meta: LiveDataset['meta']; onChanged: () => void }) {
  const { t } = useTranslation()
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<AppendResult | null>(null)
  const [err, setErr] = useState('')

  // Append grows a LIVE feed: a promoted, active dataset with declarative RML.
  if (datasetStage(meta) !== 'promoted' || meta.status === 'retracted' || !meta.has_rml) {
    return null
  }

  const isJson = meta.source_kind === 'json'
  const sourceLabel = isJson ? 'JSON' : 'CSV'
  const canAppend = !busy && files.length > 0

  async function onAppend() {
    setBusy(true)
    setErr('')
    try {
      const r = await appendToDataset(meta.id, files)
      setDone(r)
      setFiles([])
      onChanged() // append_seq / triple counts changed — refresh the catalog
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ingest-gate">
      <div className="ds-subhead">{t('gallery:append.head')}</div>
      <p className="ingest-note">
        <Trans
          i18nKey="gallery:append.note"
          values={{ source: sourceLabel }}
          components={{ strong: <strong />, code: <code /> }}
        />
      </p>
      {(meta.append_seq ?? 0) > 0 && (
        <p className="ingest-source">
          {t('gallery:append.progress', {
            seq: meta.append_seq,
            appended: meta.triples_appended
              ? t('gallery:append.progressAppended', { n: meta.triples_appended })
              : '',
          })}
        </p>
      )}
      <div className="ingest-pick">
        <label className="file-btn">
          {t('gallery:ingest.pickLabel', { source: sourceLabel })}
          <input
            type="file"
            accept={isJson ? '.json,.geojson' : '.csv'}
            multiple
            onChange={(e) => {
              setFiles(Array.from(e.target.files ?? []))
              setDone(null)
            }}
          />
        </label>
        <span className={`file-names${files.length ? '' : ' empty'}`}>
          {files.length
            ? files.map((f) => f.name).join('、')
            : t('gallery:append.pickPlaceholder', { source: sourceLabel })}
        </span>
      </div>
      <button type="button" className="promote-btn" onClick={onAppend} disabled={!canAppend}>
        {busy ? t('gallery:append.submitting') : t('gallery:append.submit')}
      </button>
      {done && (
        <p className="ingest-ok">
          {t('gallery:append.done', { n: done.triples_in_batch, seq: done.append_seq })}
        </p>
      )}
      {err && <p className="promote-err">{t('gallery:append.error', { message: err })}</p>}
    </div>
  )
}

/**
 * Document layer: add another document to a *promoted* document dataset. The doc
 * analogue of AppendControl — structures just the new document (Word→JATS server-side
 * when needed) and merges it into the live graph, so the dataset accumulates documents
 * (a running "定例ミーティング" of minutes) and search_text / quote_with_citation span
 * every one. Only shown for a promoted, active document dataset (source_kind === xml).
 */
function DocumentAppendControl({
  meta,
  onChanged,
}: {
  meta: LiveDataset['meta']
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<{ docs: number; triples: number } | null>(null)
  const [prog, setProg] = useState<{ i: number; n: number } | null>(null)
  const [err, setErr] = useState('')

  // A promoted, active DOCUMENT dataset (documents have no RML; their accumulation is
  // the source-kind=xml feed). Hidden otherwise.
  if (
    datasetStage(meta) !== 'promoted' ||
    meta.status === 'retracted' ||
    meta.source_kind !== 'xml'
  ) {
    return null
  }

  const canAdd = !busy && files.length > 0

  async function onAdd() {
    if (!files.length) return
    setBusy(true)
    setErr('')
    setDone(null)
    setProg(null)
    let triples = 0
    try {
      // Append each document sequentially (one POST-merge per doc into the live graph).
      for (let i = 0; i < files.length; i++) {
        setProg({ i: i + 1, n: files.length })
        const r: DocumentAppendResult = await appendDocument(meta.id, files[i])
        triples += r.triples_in_batch
      }
      setDone({ docs: files.length, triples })
      setFiles([])
      onChanged() // triple counts / doc count changed — refresh the catalog
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
      setProg(null)
    }
  }

  return (
    <div className="ingest-gate">
      <div className="ds-subhead">{t('gallery:docAppend.head')}</div>
      <p className="ingest-note">
        <Trans
          i18nKey="gallery:docAppend.note"
          components={[
            <code />,
            <code />,
            <strong />,
            <code />,
            <code />,
            <strong />,
            <code />,
          ]}
        />
      </p>
      {(meta.append_seq ?? 0) > 0 && (
        <p className="ingest-source">{t('gallery:docAppend.appended', { n: meta.append_seq })}</p>
      )}
      <div className="ingest-pick">
        <label className="file-btn">
          {t('gallery:docAppend.pick')}
          <input
            type="file"
            accept=".xml,.docx,.pdf"
            multiple
            onChange={(e) => {
              setFiles(Array.from(e.target.files ?? []))
              setDone(null)
            }}
          />
        </label>
        <span className={`file-names${files.length ? '' : ' empty'}`}>
          {files.length === 0
            ? t('gallery:docAppend.noFile')
            : files.length === 1
              ? files[0].name
              : t('gallery:docAppend.nFiles', { n: files.length })}
        </span>
      </div>
      <button type="button" className="promote-btn" onClick={onAdd} disabled={!canAdd}>
        {busy
          ? prog
            ? t('gallery:docAppend.busyN', { i: prog.i, n: prog.n })
            : t('gallery:docAppend.busy')
          : t('gallery:docAppend.submit')}
      </button>
      {done && (
        <p className="ingest-ok">
          {t('gallery:docAppend.doneN', { docs: done.docs, n: done.triples })}
        </p>
      )}
      {err && <p className="promote-err">{t('gallery:docAppend.error', { err })}</p>}
    </div>
  )
}

/**
 * The S4 human gate: review the draft's vocabulary alignment (Reuse vs New)
 * against the canonical graph, then promote so Ask can cite it.
 *
 * Derived purely from `meta` (not init-once local state) so it stays correct
 * across a re-ingest of an already-promoted dataset: a re-ingest flips
 * promoted→false, ingested→true (a fresh staged version awaiting approval), and
 * this control must then re-reveal the (re-)promote button. A pending staged
 * version (`meta.ingested`) always takes precedence over a prior promotion.
 *
 *   - meta.ingested      → a staged version awaits approval → show (re-)promote.
 *                          version ≥ 1 ⇒ re-promote (part5 version bump).
 *   - promoted, none pending → citable; show status.
 *   - neither (design)   → nothing to gate.
 */
function PromoteControl({ meta, onChanged }: { meta: LiveDataset['meta']; onChanged: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [alignment, setAlignment] = useState<AlignmentReport | null>(null)
  const version = meta.version ?? 0

  if (!meta.ingested) {
    if (meta.promoted) {
      return (
        <p className="promote-ok">
          {t('gallery:promote.ok', {
            n: meta.triples_promoted ?? 0,
            version: version ? t('gallery:promote.okVersion', { version }) : '',
          })}
        </p>
      )
    }
    return null
  }

  // A staged version exists. If the dataset was promoted before (version ≥ 1)
  // this is a re-promote: it swaps the live pointer to the new version (part5).
  const isRepromote = version >= 1

  async function preview() {
    setErr('')
    try {
      setAlignment(await getAlignment(meta.id))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }
  async function promote() {
    setBusy(true)
    setErr('')
    try {
      await promoteDataset(meta.id)
      // Refresh meta so this control settles into the citable (✓) view. Stays
      // busy/disabled through the reload — the ✓ view replaces the button once
      // the new meta lands, so there is no flash and no double-submit.
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <div className="promote-control">
      {isRepromote ? (
        <p className="promote-note">
          <Trans
            i18nKey="gallery:promote.repromoteNote"
            values={{ version, next: version + 1 }}
            components={{ strong: <strong /> }}
          />
        </p>
      ) : (
        <p className="promote-note">
          <Trans i18nKey="gallery:promote.note">
            「検索対象として公開」すると、下書きグラフのこのデータが <strong>Ask が引用する正式グラフ
            （canonical）</strong> に移ります。公開前に、使っている語彙が既存の再利用か新規かを確認できます。
          </Trans>
        </p>
      )}
      {alignment ? (
        <div className="alignment-summary">
          <span>
            {t('gallery:promote.alignmentSummary', {
              predReuse: alignment.predicates.reuse.length,
              predNew: alignment.predicates.new.length,
              classReuse: alignment.classes.reuse.length,
              classNew: alignment.classes.new.length,
            })}
          </span>
          {alignment.predicates.new.length > 0 && (
            <p className="alignment-new">
              {t('gallery:promote.alignmentNew', {
                terms: alignment.predicates.new.map(shortIri).join('、'),
              })}
            </p>
          )}
        </div>
      ) : (
        <button type="button" className="btn btn--ghost btn--sm" onClick={preview}>
          {isRepromote ? t('gallery:promote.previewRepromote') : t('gallery:promote.preview')}
        </button>
      )}
      <button type="button" className="promote-btn" onClick={promote} disabled={busy}>
        {busy
          ? isRepromote
            ? t('gallery:promote.repromoting')
            : t('gallery:promote.promoting')
          : isRepromote
            ? t('gallery:promote.repromoteSubmit', { next: version + 1 })
            : t('gallery:promote.submit')}
      </button>
      {err && (
        <p className="promote-err">
          {isRepromote
            ? t('gallery:promote.repromoteError', { message: err })
            : t('gallery:promote.error', { message: err })}
        </p>
      )}
    </div>
  )
}

/**
 * part5: safely *replace* the data of a promoted (or ingested) dataset. Re-ingest
 * streams a fresh version graph `canonical/{id}/v{n}` WITHOUT touching the live
 * one, so Ask keeps citing the current version gap-free throughout the re-stream;
 * the new version is staged (not citable) until the user re-promotes below, which
 * swaps the live pointer (O(1)) and bumps the dataset version. Shown for
 * promoted/ingested datasets with declarative RML; hidden for design (that uses
 * IngestControl) and for retracted datasets. CSV is re-attached here, or the
 * persisted design-time source is reused.
 */
function ReingestControl({ meta, onChanged }: { meta: LiveDataset['meta']; onChanged: () => void }) {
  const { t } = useTranslation()
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [err, setErr] = useState<unknown>(null)

  const stage = datasetStage(meta)
  // design → IngestControl owns the first ingest; retracted → reinstate first.
  if (stage === 'design' || meta.status === 'retracted') return null
  if (!meta.has_rml) return null

  const version = meta.version ?? 0
  // A live, citable version exists once the dataset has ever been promoted
  // (version ≥ 1) — true even right after a re-ingest flips stage to 'ingested',
  // because the previously-promoted version stays live until the re-promote.
  const published = version >= 1
  const hasSource = !!meta.has_source
  const isJson = meta.source_kind === 'json'
  const sourceLabel = isJson ? 'JSON' : 'CSV'
  const canReingest = !busy && (hasSource || files.length > 0)

  async function onReingest() {
    setBusy(true)
    setErr(null)
    setProgress(null)
    try {
      // hasSource → no upload (server reuses the persisted source); else upload.
      await ingestDataset(meta.id, hasSource ? [] : files, setProgress)
      setFiles([])
      // promoted→ingested (or another staged version): refresh so the re-promote
      // gate (PromoteControl) appears with the new staged version.
      onChanged()
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ingest-gate">
      <div className="ds-subhead">{t('gallery:reingest.head')}</div>
      <p className="ingest-note">
        <Trans
          i18nKey="gallery:reingest.notePrefix"
          values={{ source: sourceLabel, id: shortIri(meta.id) }}
          components={{ strong: <strong /> }}
        />{' '}
        {published ? (
          <Trans
            i18nKey="gallery:reingest.notePublished"
            values={{ version, next: version + 1 }}
            components={{ strong: <strong /> }}
          />
        ) : (
          t('gallery:reingest.noteUnpublished')
        )}
      </p>
      {hasSource ? (
        <p className="ingest-source">
          {t('gallery:reingest.sourceSaved', {
            source: sourceLabel,
            files: meta.source_files?.length
              ? t('gallery:ingest.filesSuffix', { names: meta.source_files.join('、') })
              : '',
          })}
        </p>
      ) : null}
      <div className="ingest-pick">
        <label className="file-btn">
          {hasSource
            ? t('gallery:reingest.pickReplace', { source: sourceLabel })
            : t('gallery:reingest.pickSelect', { source: sourceLabel })}
          <input
            type="file"
            accept={isJson ? '.json,.geojson' : '.csv'}
            multiple
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>
        <span className={`file-names${files.length ? '' : ' empty'}`}>
          {files.length
            ? files.map((f) => f.name).join('、')
            : hasSource
              ? t('gallery:reingest.placeholderKeep', { source: sourceLabel })
              : t('gallery:reingest.placeholderSelect', { source: sourceLabel })}
        </span>
      </div>
      <button type="button" className="promote-btn" onClick={onReingest} disabled={!canReingest}>
        {busy ? t('gallery:reingest.submitting') : t('gallery:reingest.submit')}
      </button>
      {busy && <IngestProgressView progress={progress} />}
      {err != null && <IngestError err={err} errorKey="gallery:reingest.error" />}
    </div>
  )
}

