import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { ingestDataset, type IngestProgress, type IngestResult } from './api'
import { getCrosswalk } from './crosswalkApi'
import { DatasetGrounding } from './DatasetGrounding'
import { getSchema } from './demoApi'
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
import { ArrowIcon, LayersIcon, LinkIcon, SearchIcon } from './icons'
import { IngestProgressView } from './IngestProgressView'
import { Mermaid } from './Mermaid'
import { ToolsPanel } from './ToolsPanel'
import { localName } from './vocab'

type DetailTab = 'design' | 'rules' | 'tools'

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
  onOpenVocab,
  onOpenCrosswalk,
  onOpenMap,
}: {
  focusClass?: string | null
  onOpenVocab?: () => void
  onOpenCrosswalk?: () => void
  onOpenMap?: () => void
}) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  const [error, setError] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [seenFocus, setSeenFocus] = useState<string | null | undefined>(focusClass)
  const [tab, setTab] = useState<DetailTab>('design')
  // Live count of shared classes actually in the store (for the gateway band).
  // null = unavailable (query layer down) → the band omits the number.
  const [sharedClassCount, setSharedClassCount] = useState<number | null>(null)
  // crosswalk-hub.md ④: # of datasets the live crosswalk joins (for the band CTA).
  const [crosswalkCount, setCrosswalkCount] = useState<number | null>(null)

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
    getSchema()
      .then((s) => !cancelled && setSharedClassCount(s ? s.classes.length : null))
      .catch(() => {})
    getCrosswalk()
      .then((c) => {
        if (cancelled) return
        const n = c.exists ? (c.config?.concepts.flatMap((x) => x.participants).length ?? 0) : 0
        setCrosswalkCount(n)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // Reset the explicit pick when arriving with a new Ask focus (setState during
  // render — the "adjust state on prop change" pattern; avoids effect cascades).
  if (focusClass !== seenFocus) {
    setSeenFocus(focusClass)
    setPicked(null)
  }

  // The crosswalk hub is a bridge, not a dataset — it surfaces as the クロスウォーク band
  // / view below, never as a list card (crosswalk-hub.md ④).
  const list = (datasets ?? []).filter((d) => !d.isCrosswalk)
  const focused = focusClass ? list.find((d) => d.classes.includes(focusClass)) : undefined
  const selected = list.find((d) => d.id === picked) ?? focused ?? list[0] ?? null

  return (
    <div className="catalog">
      <p className="catalog-intro">
        <Trans i18nKey="gallery:intro">
          作った<strong>データセット</strong>が主役です。各データセットは「<strong>設計図（語彙）</strong>」と
          「<strong>取り込みルール</strong>」を持ちます。共通で使う語彙は下にまとめています。
        </Trans>
      </p>

      {focusClass && (
        <div className="vocab-focus-banner">
          {t('gallery:focusBanner.label')}
          <strong>{focusClass}</strong>
          <span className="vocab-focus-sub">{t('gallery:focusBanner.sub')}</span>
        </div>
      )}

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
        </div>
      )}

      {datasets && list.length > 0 && (
        <div className="catalog-grid">
          <div className="catalog-list">
            <div className="catalog-list-head">
              <h3 className="card-h">{t('gallery:list.head')}</h3>
              <span className="catalog-count">{list.length}</span>
            </div>
            {list.map((d) => (
              <DatasetListCard
                key={d.id}
                dataset={d}
                active={d.id === selected?.id}
                onSelect={() => setPicked(d.id)}
              />
            ))}
          </div>

          {selected && (
            // Key by dataset id so the detail (and its controls' local state —
            // IngestControl's done/files, PromoteControl's promoted/alignment)
            // remounts fresh when switching datasets, never leaking across them.
            <DatasetDetail
              key={selected.id}
              dataset={selected}
              tab={tab}
              onTab={setTab}
              highlight={focusClass}
              onChanged={reload}
            />
          )}
        </div>
      )}

      {/* shared vocabulary gateway → the live cross-dataset vocabulary board */}
      <button type="button" className="shared-band" onClick={onOpenVocab}>
        <span className="shared-band-icon">
          <LinkIcon size={19} />
        </span>
        <span className="shared-band-body">
          <span className="shared-band-title">
            {t('gallery:sharedBand.title')} <span className="shared-band-en">{t('gallery:sharedBand.en')}</span>
            <span className="shared-band-warn">{t('gallery:sharedBand.warn')}</span>
          </span>
          <span className="shared-band-sub">
            <Trans i18nKey="gallery:sharedBand.sub">
              複数のデータセットが共通で使う設計図。揃えておくと<strong>横断して検索・比較</strong>できます。
            </Trans>
          </span>
        </span>
        <span className="shared-band-cta">
          {sharedClassCount != null && (
            <span className="shared-band-users">
              <Trans
                i18nKey="gallery:sharedBand.users"
                values={{ n: sharedClassCount }}
                components={[<span className="mono-strong" key="n" />]}
              />
            </span>
          )}
          {t('gallery:sharedBand.open')} <ArrowIcon size={14} />
        </span>
      </button>

      {/* crosswalk gateway → the cross-dataset bridge (compositions joined across datasets) */}
      <button type="button" className="shared-band" onClick={onOpenCrosswalk}>
        <span className="shared-band-icon">
          <LinkIcon size={19} />
        </span>
        <span className="shared-band-body">
          <span className="shared-band-title">
            {t('gallery:crosswalkBand.title')} <span className="shared-band-en">{t('gallery:crosswalkBand.en')}</span>
          </span>
          <span className="shared-band-sub">
            <Trans i18nKey="gallery:crosswalkBand.sub">
              同じ概念（組成・結晶系・著者…）を共有する複数のデータセットを<strong>1つの橋でつなぐ</strong>。
              「この値は何データセットが報告？」を横断で答えられます。
            </Trans>
          </span>
        </span>
        <span className="shared-band-cta">
          {crosswalkCount != null && crosswalkCount > 0 && (
            <span className="shared-band-users">
              <Trans
                i18nKey="gallery:crosswalkBand.users"
                values={{ n: crosswalkCount }}
                components={[<span className="mono-strong" key="n" />]}
              />
            </span>
          )}
          {t('gallery:crosswalkBand.open')} <ArrowIcon size={14} />
        </span>
      </button>

      {/* ontology map gateway → the bird's-eye view of all ontologies + their links */}
      <button type="button" className="shared-band" onClick={onOpenMap}>
        <span className="shared-band-icon">
          <LayersIcon size={19} />
        </span>
        <span className="shared-band-body">
          <span className="shared-band-title">
            オントロジーの全体像 <span className="shared-band-en">ontology map</span>
          </span>
          <span className="shared-band-sub">
            どんなオントロジーがあり、<strong>どうつながっているか</strong>を1枚の図で俯瞰します
            （データセット × クロスウォークの橋 × 整合）。
          </span>
        </span>
        <span className="shared-band-cta">
          開く <ArrowIcon size={14} />
        </span>
      </button>
    </div>
  )
}

function DatasetListCard({
  dataset,
  active,
  onSelect,
}: {
  dataset: CatalogDataset
  active: boolean
  onSelect: () => void
}) {
  const { t } = useTranslation()
  return (
    <button type="button" className={`ds-card${active ? ' active' : ''}`} onClick={onSelect}>
      <div className="ds-card-head">
        <span className="ds-card-name">{dataset.name}</span>
        <span className={`status-pill status-pill--${dataset.statusKind}`}>
          {statusLabel(t, dataset.statusKind)}
        </span>
      </div>
      <div className="ds-card-sub">{dataset.sub}</div>
      <div className="ds-card-counts">
        {dataset.counts.map((c) => (
          <span className="ds-row-count" key={c.label}>
            <span className="ds-row-count-val">{c.value}</span> {c.label}
          </span>
        ))}
      </div>
    </button>
  )
}

function DatasetDetail({
  dataset,
  tab,
  onTab,
  highlight,
  onChanged,
}: {
  dataset: CatalogDataset
  tab: DetailTab
  onTab: (t: DetailTab) => void
  highlight?: string | null
  onChanged: () => void
}) {
  const { t } = useTranslation()
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
          <button
            type="button"
            className={`ds-tab${tab === 'design' ? ' active' : ''}`}
            onClick={() => onTab('design')}
          >
            {t('gallery:tab.design')} <span className="ds-tab-en">{t('gallery:tab.designEn')}</span>
          </button>
          <button
            type="button"
            className={`ds-tab${tab === 'rules' ? ' active' : ''}`}
            onClick={() => onTab('rules')}
          >
            {t('gallery:tab.rules')} <span className="ds-tab-en">{t('gallery:tab.rulesEn')}</span>
          </button>
          {dataset.live && (
            <button
              type="button"
              className={`ds-tab${tab === 'tools' ? ' active' : ''}`}
              onClick={() => onTab('tools')}
            >
              {t('gallery:tab.tools')} <span className="ds-tab-en">{t('gallery:tab.toolsEn')}</span>
            </button>
          )}
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

      {tab === 'tools' && dataset.live ? (
        <ToolsPanel datasetId={dataset.live.meta.id} />
      ) : tab === 'design' ? (
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
            <details className="ds-diagram-details">
              <summary>{t('gallery:design.diagramSummary')}</summary>
              <div className="onto-diagram">
                <Mermaid chart={dataset.mermaid} />
              </div>
            </details>
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
      ) : (
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
        </div>
      )}

      {/* Dataset-level controls — shown under BOTH tabs (not mapping-specific). */}
      {dataset.live && (
        <div className="ds-detail-controls">
          {/* Task E: ingest gate for design-stage datasets (no facts yet). */}
          <IngestControl meta={dataset.live.meta} onChanged={onChanged} />
          {/* S4 (re-)promote human-gate: first promote of a draft, or re-promote
              of a re-ingested replacement (version bump). */}
          <PromoteControl meta={dataset.live.meta} onChanged={onChanged} />
          {/* incremental-ingest.md: grow a promoted dataset's live feed by appending
              a new batch (device-feed path — O(new), no re-ingest of the whole source). */}
          <AppendControl meta={dataset.live.meta} onChanged={onChanged} />
          {/* document layer: add another document to a promoted document dataset
              (the "定例ミーティング" path — one doc at a time, searchable across all). */}
          <DocumentAppendControl meta={dataset.live.meta} onChanged={onChanged} />
          {/* part5: safe replace — re-ingest a promoted/ingested dataset into a new
              version graph (gap-free), then re-promote to swap the live pointer. */}
          <ReingestControl meta={dataset.live.meta} onChanged={onChanged} />
          {/* #20 P3 lifecycle: retract / reinstate / delete (human-gated). */}
          <LifecycleControl meta={dataset.live.meta} onChanged={onChanged} />
        </div>
      )}
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
function IngestControl({ meta, onChanged }: { meta: LiveDataset['meta']; onChanged: () => void }) {
  const { t } = useTranslation()
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [done, setDone] = useState<IngestResult | null>(null)
  const [err, setErr] = useState('')

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
    setErr('')
    setProgress(null)
    try {
      // hasSource → ingest with no upload (server uses the persisted source).
      setDone(await ingestDataset(meta.id, hasSource ? [] : files, setProgress))
      onChanged() // design → draft: refresh so promote control appears
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
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
      {err && <p className="promote-err">{t('gallery:ingest.error', { message: err })}</p>}
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
            「共有データに昇格」すると、下書きグラフのこのデータが <strong>Ask が引用する正式グラフ
            （canonical）</strong> に移ります。昇格前に、使っている語彙が既存の再利用か新規かを確認できます。
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
  const [err, setErr] = useState('')

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
    setErr('')
    setProgress(null)
    try {
      // hasSource → no upload (server reuses the persisted source); else upload.
      await ingestDataset(meta.id, hasSource ? [] : files, setProgress)
      setFiles([])
      // promoted→ingested (or another staged version): refresh so the re-promote
      // gate (PromoteControl) appears with the new staged version.
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
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
      {err && <p className="promote-err">{t('gallery:reingest.error', { message: err })}</p>}
    </div>
  )
}

/**
 * #20 P3 lifecycle controls (human-gated): retract (withdraw from the citable
 * corpus — tombstone, IRIs kept), reinstate (undo), and delete (hard removal;
 * a promoted/citable dataset requires an explicit force confirm). Backend-backed
 * by /api/datasets/{id}/{retract,reinstate} and DELETE /api/datasets/{id}.
 */
function LifecycleControl({ meta, onChanged }: { meta: LiveDataset['meta']; onChanged: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const retracted = meta.status === 'retracted'
  const stage = datasetStage(meta)

  async function run(label: string, fn: () => Promise<string>) {
    setBusy(label)
    setErr('')
    setMsg('')
    try {
      setMsg(await fn())
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="lifecycle-control">
      <div className="ds-subhead">{t('gallery:lifecycle.head')}</div>
      {retracted && (
        <p className="lifecycle-status">
          <Trans i18nKey="gallery:lifecycle.retractedStatus">
            状態: <strong>撤回済み</strong>（Ask の引用対象外。データ・IRI は残るので既存の引用は壊れません。復帰できます）
          </Trans>
        </p>
      )}
      <div className="lifecycle-actions">
        {stage === 'promoted' && !retracted && (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={!!busy}
            onClick={() =>
              window.confirm(t('gallery:lifecycle.retractConfirm')) &&
              run('retract', async () => {
                await retractDataset(meta.id)
                return t('gallery:lifecycle.retractDone')
              })
            }
          >
            {busy === 'retract' ? t('gallery:lifecycle.retracting') : t('gallery:lifecycle.retract')}
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
                return t('gallery:lifecycle.reinstateDone')
              })
            }
          >
            {busy === 'reinstate' ? t('gallery:lifecycle.reinstating') : t('gallery:lifecycle.reinstate')}
          </button>
        )}
        <button
          type="button"
          className="btn btn--danger btn--sm"
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
                return t('gallery:lifecycle.deleteDone')
              })
          }}
        >
          {busy === 'delete' ? t('gallery:lifecycle.deleting') : t('gallery:lifecycle.delete')}
        </button>
      </div>
      {msg && <p className="lifecycle-ok">{msg}</p>}
      {err && <p className="lifecycle-err">{t('gallery:lifecycle.error', { message: err })}</p>}
    </div>
  )
}
