import type {
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  ReactNode,
} from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Trans, useTranslation } from 'react-i18next'
import {
  ApiError,
  fetchProposal,
  fetchTrialQueries,
  IngestCancelledError,
  type IngestJobHandle,
  type IngestProgress,
  type IngestResult,
  IngestValidationError,
  resumeIngestJob,
  StaleIngestJobError,
  startIngestJob,
} from './api'
import { plainAdvisories, type TermLabels } from './advisoryPlain'
import { validateDesign } from './api'
import { prefillAskQuestion } from './askPrefill'
import { plainError } from './kantan/errorMessages'
import { clearIngestJob, loadIngestJob, saveIngestJob } from './ingestJob'
import type { RedesignTarget } from './WorkbenchView'
import { type CrosswalkPerspective, getCrosswalks } from './crosswalkApi'
import { conceptLabel } from './crosswalkLabels'
import { DatasetGrounding } from './DatasetGrounding'
import { RulesSection } from './RulesPanel'
import { TABULAR_ACCEPT } from './datasetsApi'
import {
  type AlignmentReport,
  alignmentWordSplit,
  appendDocument,
  appendToDataset,
  type AppendResult,
  type CatalogDataset,
  type CatalogStatusKind,
  datasetStage,
  deleteDataset,
  type DatasetRules,
  getAlignment,
  getCatalogDatasets,
  getDatasetRules,
  type LiveDataset,
  promoteDataset,
  reinstateDataset,
  renameDataset,
  retractDataset,
} from './galleryApi'
import { ArrowIcon, ConnectIcon, DataIcon, FileIcon, LayersIcon, SearchIcon } from './icons'
import { IngestProgressView } from './IngestProgressView'
import { ToolsPanel } from './ToolsPanel'
import { rulesShape } from './shapeGraph'
import { ShapeGraph } from './kantan/ShapeGraph'
import { localName } from './vocab'

export type DetailTab = 'structure' | 'tools' | 'files' | 'connect' | 'design'

/** Which control on the files tab the state band is sending the reader to. */
type ControlFocus = 'ingest' | 'promote' | 'append' | 'reingest' | null

/** Where a caller wants this detail page to LAND. A tab alone is not enough when
 * the thing that was clicked lives partway down a long tab — かんたん S9's
 * 「標準のことばに合わせる」 would otherwise drop the person at the top of 設計
 * and make them find the same button again. */
export type DetailFocus = 'append' | 'reingest' | 'grounding'

/** Scroll a control into view when the state band points at it. */
function useFocusScroll(focus: boolean | undefined, ref: { current: HTMLDivElement | null }) {
  useEffect(() => {
    if (!focus) return
    ref.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    // ref identity is stable for the lifetime of the control
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus])
}

/**
 * Display label for a dataset's source kind (the small type tag on the card and
 * in the file table). Says what the thing IS in the reader's language — an .xlsx
 * that came in as a table used to be tagged 「CSV」, and a document 「DOC」.
 */
function sourceTag(t: (k: string) => string, meta?: LiveDataset['meta']): string {
  const k = meta?.source_kind
  return k === 'json'
    ? t('gallery:fileKind.json')
    : k === 'xml'
      ? t('gallery:fileKind.document')
      : t('gallery:fileKind.tabular')
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
 * The file names the server said it was expecting, when the caller could not
 * supply them (`expected one of ['zem.csv']`). Best-effort: an empty list just
 * leaves the sentence without the example, never a raw English fragment.
 */
function expectedFromMessage(raw: string): string[] {
  const m = /expected one of \[(.*?)\]/i.exec(raw)
  if (!m) return []
  return m[1]
    .split(',')
    .map((s) => s.trim().replace(/^['"]|['"]$/g, ''))
    .filter(Boolean)
}

/**
 * The HTTP status and the server's own sentence behind a failed call, from
 * either shape this screen sees.
 *
 * `api.ts` throws a typed {@link ApiError}; `galleryApi.ts`, a job stream and the
 * catalog list surface a plain `Error` whose message the api module composed
 * (`promote failed (HTTP 409): {…}`), so the status has to be read back out of
 * the text. Both paths are best-effort: an unrecognised message simply yields no
 * status and no sentence, and the caller falls back to the generic family.
 */
function errorFacts(err: unknown): { status: number | null; sentence: string } {
  if (err instanceof ApiError) {
    return {
      status: err.status,
      sentence: err.detail && !err.detail.trimStart().startsWith('{') ? err.detail : '',
    }
  }
  const raw = err instanceof Error ? err.message : String(err)
  const m = /\(HTTP (\d{3})\)/.exec(raw)
  if (!m) {
    // Not the api module's wrapper at all — the message IS the sentence (the
    // server answers several of these endpoints with a finished one).
    return { status: null, sentence: raw.trimStart().startsWith('{') ? '' : raw }
  }
  const body = raw.slice(m.index + m[0].length).replace(/^:\s*/, '')
  let sentence = ''
  if (body.trimStart().startsWith('{')) {
    try {
      const parsed = JSON.parse(body) as { detail?: unknown }
      if (typeof parsed.detail === 'string') sentence = parsed.detail.trim()
    } catch {
      /* not JSON after all — no sentence to show */
    }
  }
  return { status: Number(m[1]), sentence }
}

/**
 * The catalog-only failures, each recognised by the ONE server sentence it comes
 * with — never by the status alone.
 *
 * 409 is not a single situation: the api answers it for a delete that would
 * break citations (whose fix is "unpublish first") and for an append onto data
 * that is not live (whose fix is the exact opposite — publish it, or reinstate
 * it). A status-only rule would have sent two of these three readers the wrong
 * way, so each one keys on the sentence and anything unrecognised falls through
 * to `plainError`'s families.
 *
 * Matching on English prose is a deliberate best-effort: a miss costs the
 * generic wording, never a wrong instruction. If the server ever gains a
 * machine-readable `code` for these, switch to it here.
 */
function catalogFamily(raw: string, status: number | null): { title: string; body: string } | null {
  // The commonest append failure: the instrument named today's file with today's
  // date, and the server matches batch files against the design's source name.
  // Said in the server's words it is `does not match any rml:source in the
  // mapping` — a sentence about our internals, in a screen about their file.
  if (/does not match any rml:source/i.test(raw))
    return { title: 'gallery:append.nameMismatchTitle', body: 'gallery:append.nameMismatchBody' }
  if (status !== 409) return null
  if (/retract it instead|citable canonical/i.test(raw))
    return { title: 'gallery:error.conflictTitle', body: 'gallery:error.conflictBody' }
  if (/reinstate it before appending/i.test(raw))
    return { title: 'gallery:error.retractedTitle', body: 'gallery:error.retractedBody' }
  if (/append needs a live canonical graph/i.test(raw))
    return { title: 'gallery:error.notLiveTitle', body: 'gallery:error.notLiveBody' }
  return null
}

/**
 * The plain-language face of a failed catalog action (K11, ADR §5.1).
 *
 * Every action here used to print the api's own sentence — `promote failed
 * (HTTP 409): {"detail":…}` — which names neither the cause nor the next move.
 * The classification is the SAME one the kantan stop card uses (`plainError`),
 * so one failure never reads two ways depending on which screen you are on;
 * what is added on top is {@link catalogFamily}, the handful of failures only
 * this screen can act on. The raw string stays one click away for whoever needs
 * it.
 */
function ErrorNote({
  err,
  titleKey,
  expectedFiles,
}: {
  err: unknown
  titleKey: string
  /** The design-time file names, for the one append failure that is about names. */
  expectedFiles?: string[]
}) {
  const { t } = useTranslation()
  const raw = err instanceof Error ? err.message : String(err)
  const { status, sentence: serverSentence } = errorFacts(err)
  const own = catalogFamily(raw, status)
  const p = plainError(raw)
  // The server answers these endpoints with its own plain sentence. It beats the
  // generic "something went wrong" nudge, but never a classified family (those
  // carry the recovery step too).
  const generic = p.body === 'kantan:s5.plain.genericBody'
  const expected = (expectedFiles?.length ? expectedFiles : expectedFromMessage(raw)).join('、')
  const title = own ? t(own.title) : p.title ? t(p.title) : t(titleKey)
  const body = own
    ? t(own.body, { expected })
    : generic && serverSentence
      ? serverSentence
      : t(p.body)
  return (
    <div className="promote-err ingest-issues">
      <p className="ingest-issues-head">{title}</p>
      <p>{body}</p>
      <details className="ds-advisory-raw">
        <summary>{t('kantan:s5.stop.detailSummary')}</summary>
        <pre className="rules-code-block">{raw}</pre>
      </details>
    </div>
  )
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
  selectedId = null,
  detailTab = 'structure',
  onSelect,
  onDetailTab,
  onOpenCrosswalk,
  onCreateCrosswalk,
  onOpenMap,
  onAddData,
  onRedesign,
  detailFocus = null,
  onDetailFocusConsumed,
}: {
  focusClass?: string | null
  /** 選択中データセット（App の hash ルートが真実源 — 一覧⇄詳細の往復や他画面
   *  への寄り道・リロードでも選択が消えない）。 */
  selectedId?: string | null
  detailTab?: DetailTab
  onSelect?: (id: string | null) => void
  onDetailTab?: (tab: DetailTab) => void
  onOpenCrosswalk?: () => void
  /** つながりを作るフローへ直行（このデータセットが未接続のとき）。 */
  onCreateCrosswalk?: () => void
  onOpenMap?: () => void
  onAddData?: () => void
  /** Open the workbench on this dataset's stored design to revise it ("見直す"). */
  onRedesign?: (target: RedesignTarget) => void
  /** Which control/section the caller wants scrolled to on arrival. */
  detailFocus?: DetailFocus | null
  /** Fired once the focus has been acted on, so a later visit does not re-scroll. */
  onDetailFocusConsumed?: () => void
}) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  const [error, setError] = useState('')
  const seenFocusRef = useRef<string | null | undefined>(focusClass)
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

  const list = (datasets ?? []).filter((d) => !d.isCrosswalk)
  // Arriving with a new Ask focus opens that dataset's detail directly.
  // 選択は親（hash ルート）が持つため、render 中の setState ではなく effect で通知する。
  useEffect(() => {
    if (focusClass === seenFocusRef.current) return
    if (!datasets) return
    seenFocusRef.current = focusClass
    const f = focusClass ? list.find((d) => d.classes.includes(focusClass)) : undefined
    onSelect?.(f ? f.id : null)
    // list は datasets から導出されるため datasets を依存に取る
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusClass, datasets])
  // Default view is the full-width grid; a dataset is opened on demand (v2 #5).
  // Deep links accept BOTH id forms: the synthetic catalog id (`live-<id>`)
  // and the bare registry id — outside callers (the kantan S9 exits, redesign
  // links) know only the registry id.
  const selected = selectedId
    ? (list.find((d) => d.id === selectedId || d.live?.meta.id === selectedId) ?? null)
    : null
  const filtered = query.trim()
    ? list.filter((d) => d.name.toLowerCase().includes(query.trim().toLowerCase()))
    : list

  return (
    <div className="catalog">
      {/* A failed list load used to end here, as `datasets: HTTP 500`. The reader
          needs the same two things every other failure gives them: what happened
          in their own words, and the way back. */}
      {error && (
        <div className="state-block">
          <p className="state-title">{t('gallery:listError.title')}</p>
          <p className="state-sub">{t('gallery:listError.body')}</p>
          <button
            type="button"
            className="btn btn--soft btn--sm"
            onClick={() => {
              setError('')
              reload()
            }}
          >
            {t('gallery:listError.retry')}
          </button>
          <details className="ds-advisory-raw">
            <summary>{t('kantan:s5.stop.detailSummary')}</summary>
            <pre className="rules-code-block">{error}</pre>
          </details>
        </div>
      )}

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
            <button type="button" className="ds-add-tile ds-add-tile--empty" onClick={onAddData}>
              <span className="ds-add-plus">+</span>
              <span className="ds-add-title">{t('gallery:grid.addTitle')}</span>
              <span className="ds-add-sub">{t('gallery:grid.addSub')}</span>
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
          tab={detailTab}
          onTab={(dt) => onDetailTab?.(dt)}
          highlight={focusClass}
          onChanged={reload}
          onBack={() => onSelect?.(null)}
          onOpenCrosswalk={onOpenCrosswalk}
          onCreateCrosswalk={onCreateCrosswalk}
          onOpenMap={onOpenMap}
          onRedesign={onRedesign}
          onAddData={onAddData}
          detailFocus={detailFocus}
          onDetailFocusConsumed={onDetailFocusConsumed}
        />
      )}

      {/* Full-width 3-column grid (v2 ScreenDatasets) + add tile. */}
      {datasets && !selected && list.length > 0 && (
        <>
          <div className="catalog-toolbar">
            <p className="catalog-intro">{t('gallery:intro')}</p>
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
                  onSelect?.(d.id)
                  if (t && t !== 'structure') onDetailTab?.(t)
                }}
                onChanged={reload}
                onRedesign={onRedesign}
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
  onRedesign,
}: {
  dataset: CatalogDataset
  connections: number
  onSelect: (tab?: DetailTab) => void
  onChanged: () => void
  onRedesign?: (target: RedesignTarget) => void
}) {
  const { t } = useTranslation()
  const meta = dataset.live?.meta
  const files = meta?.source_files?.length ?? 0
  function open(tab?: DetailTab) {
    onSelect(tab)
  }
  // ARIA nested-interactive fix: the card is a non-interactive container. The
  // single "open" control is a LEAF <button> (the name) whose ::after stretches
  // over the whole card, so the entire card is still clickable — but the action
  // <button>s (publish/retract/⋯) are now SIBLINGS, not descendants of an
  // interactive element. onClick keeps the exact routing behavior (no href), so
  // the hash-router path is unchanged. Actions sit above the overlay via z-index.
  return (
    <div className="ds-grid-card">
      <div className="ds-grid-card-head">
        <span className="ds-grid-card-icon">
          <DataIcon size={18} />
        </span>
        <span className="ds-grid-card-titles">
          <button type="button" className="ds-grid-card-open" onClick={() => open()}>
            <span className="ds-grid-card-name">{dataset.name}</span>
          </button>
          <span className="ds-grid-card-type">{sourceTag(t, meta)}</span>
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
        {/* The bare date said nothing about WHICH date it was (it was the design's
            save date, even on a published dataset). `sub` is the same labelled
            line the home screen shows, so one dataset reads the same in both. */}
        {dataset.sub && <span className="ds-grid-card-updated">{dataset.sub}</span>}
      </div>
      {meta && (
        <CardActions
          meta={meta}
          counts={dataset.counts}
          onChanged={onChanged}
          onOpen={open}
          onRedesign={onRedesign}
        />
      )}
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
 *   - design, with something to run → 「データを取り込む」 opens the detail's ingest form.
 *   - ingested (draft) → publish, through the SAME confirm dialog every other
 *     route uses (K10). A re-stage of an already-published version (version ≥ 1)
 *     is a re-promote whose version bump belongs in the detail, so that case
 *     opens the detail instead.
 *   - promoted, active → 「撤回」(retract, confirm).
 *   - promoted, retracted → 「復帰」(reinstate).
 *   - always → 「削除」(delete) tucked behind a compact ⋯ menu (window.confirm gated).
 *
 * The richer flows (first ingest needing a CSV, append, re-ingest) stay in the
 * detail; the card only carries the quick actions.
 */
function CardActions({
  meta,
  counts,
  onChanged,
  onOpen,
  onRedesign,
}: {
  meta: LiveDataset['meta']
  counts: CatalogDataset['counts']
  onChanged: () => void
  onOpen: (tab?: DetailTab) => void
  /** 「直したい」は一覧を見ている最中に起きる。詳細を開いてタブを選んで
   *  スクロールした先にしか無いのでは、思いついた場所から遠すぎる。 */
  onRedesign?: (target: RedesignTarget) => void
}) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState<unknown>(null)
  const [menu, setMenu] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const stage = datasetStage(meta)
  const retracted = meta.status === 'retracted'
  const version = meta.version ?? 0

  // ⋯ メニューは Escape / メニュー外クリックで閉じる（role="menu" の期待挙動）
  const menuWrapRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!menu) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenu(false)
    }
    const onDown = (e: PointerEvent) => {
      if (menuWrapRef.current && !menuWrapRef.current.contains(e.target as Node)) setMenu(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onDown)
    }
  }, [menu])

  // Stop card-open when interacting with the action footer.
  function stop(e: ReactMouseEvent | ReactKeyboardEvent) {
    e.stopPropagation()
  }

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label)
    setErr(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setErr(e)
    } finally {
      setBusy('')
      setMenu(false)
    }
  }

  // A staged draft of an already-published dataset (version ≥ 1) is a re-promote;
  // its alignment preview / version bump belongs in the detail (Files tab), so the
  // card "publish" routes there rather than one-clicking.
  const isRepromote = stage === 'ingested' && version >= 1
  // A design-stage dataset can only be ingested when there is something to run:
  // compiled rules, or a document (whose ingest goes through the structurer).
  const canIngest = stage === 'design' && (meta.source_kind === 'xml' || !!meta.has_rml)

  return (
    <div className="ds-card-actions" onClick={stop} onKeyDown={stop} role="presentation">
      {/* A kantan run whose auto-ingest failed lands back here as 「設計中」. Without
          this the card offers no way forward and the ingest sits four clicks deep. */}
      {canIngest && (
        <button
          type="button"
          className="btn btn--soft btn--sm ds-card-cta"
          onClick={() => onOpen('files')}
        >
          {t('gallery:ingest.submit')}
        </button>
      )}

      {stage === 'ingested' &&
        (isRepromote ? (
          /* Already published — this stages the NEXT version. Calling it 「公開」
             read as "not published yet?" on a dataset the reader just published. */
          <button
            type="button"
            className="btn btn--soft btn--sm ds-card-cta"
            onClick={() => onOpen('files')}
          >
            {t('gallery:card.publishUpdate')}
          </button>
        ) : (
          /* K10: publishing is one road, and it goes through the confirm dialog —
             the card must not be a shortcut past what you are agreeing to. */
          <button
            type="button"
            className="btn btn--soft btn--sm ds-card-cta"
            disabled={!!busy}
            onClick={() => setPublishing(true)}
          >
            {t('gallery:card.publish')}
          </button>
        ))}

      {/* 「この列の意味が違う」と気づくのは一覧を眺めている最中で、詳細を開いて
          タブを選んでスクロールした先にしか無いのでは、思いついた場所から遠い。
          設計が残っているならここから直接ワークベンチへ。設計が無い / 取れない
          ときだけ詳細の中身タブに送る（そこの見直し欄が理由を説明する）。 */}
      {onRedesign && meta.has_proposal !== false && !retracted && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={!!busy}
          onClick={() => {
            // run() ではなく直書き: 成功時はワークベンチへ出ていくので、この
            // カードの再読み込み (onChanged) を走らせる相手がもう居ない。
            void (async () => {
              setBusy('redesign')
              setErr(null)
              try {
                const p = await fetchProposal(meta.id)
                if (!p.has_proposal || !p.proposal_md.trim()) {
                  onOpen('structure')
                  return
                }
                onRedesign({
                  datasetId: meta.id,
                  datasetName: p.dataset_name || meta.name,
                  proposalMd: p.proposal_md,
                })
              } catch (e) {
                setErr(e)
              } finally {
                setBusy('')
              }
            })()
          }}
        >
          {busy === 'redesign' ? t('gallery:redesign.loading') : t('gallery:redesign.open')}
        </button>
      )}

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
      <div className="ds-card-menu-wrap" ref={menuWrapRef}>
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

      {err != null && (
        <div className="ds-card-err">
          <ErrorNote err={err} titleKey="gallery:lifecycle.error" />
        </div>
      )}

      {publishing && (
        <PublishDialog
          meta={meta}
          counts={counts}
          onClose={() => setPublishing(false)}
          onDone={() => {
            setPublishing(false)
            onChanged()
          }}
        />
      )}
    </div>
  )
}

/**
 * One named group of the publish dialog's word list (reused / new to this data).
 * The human name from the Mapping IR leads and the identifier follows as a
 * receipt — with no label the identifier stands alone rather than nothing.
 */
function WordGroup({
  head,
  iris,
  labels,
}: {
  head: string
  iris: string[]
  labels?: TermLabels
}) {
  if (iris.length === 0) return null
  return (
    <>
      <p className="ingest-hint">{head}</p>
      <ul className="ds-advisory-list">
        {iris.map((iri) => {
          const local = localName(iri)
          const label = labels?.[iri] ?? labels?.[local]
          return (
            <li key={iri}>
              {label ? <>{label} </> : null}
              <code title={iri}>{local}</code>
            </li>
          )
        })}
      </ul>
    </>
  )
}

/**
 * K10: the one confirm dialog every first publish goes through — the catalog
 * card, the dataset detail, and the kantan wizard's S8 all say the same four
 * things before anything becomes citable: the name it will be published under,
 * what is inside, which words it uses, and that this is reversible. The word
 * summary loads by itself: an optional "check the differences" button is a
 * button first-timers never press (K9), which is how it stopped being part of
 * the decision.
 */
function PublishDialog({
  meta,
  counts,
  labels,
  onClose,
  onDone,
}: {
  meta: LiveDataset['meta']
  counts?: CatalogDataset['counts']
  /** Human names for the words, so the list is not a column of identifiers. */
  labels?: TermLabels
  onClose: () => void
  onDone: () => void
}) {
  const { t } = useTranslation()
  const [alignment, setAlignment] = useState<AlignmentReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  const version = meta.version ?? 0
  const isRepromote = version >= 1

  useEffect(() => {
    let cancelled = false
    getAlignment(meta.id)
      .then((a) => {
        if (!cancelled) setAlignment(a)
      })
      .catch(() => {
        /* the summary is a reassurance, not a gate — publishing still works */
      })
    return () => {
      cancelled = true
    }
  }, [meta.id])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const name = (meta.name ?? '').trim()
  const words = alignment ? alignmentWordSplit(alignment) : null
  // K12: 「what is inside」 is counted in kinds, never in triples. The catalog's
  // `counts` still carries a `fact` entry (the raw triple total) for the detail
  // tooling, and that number is exactly the one a collapsed primary key hides —
  // so the publish decision is never phrased with it.
  const plainCounts = (counts ?? []).filter((c) => c.key !== 'fact')
  const countsText =
    plainCounts.length > 0
      ? plainCounts.map((c) => `${c.value} ${c.label}`).join(' · ')
      : t('gallery:promote.dialogCountsUnknown')

  async function publish() {
    setBusy(true)
    setErr(null)
    try {
      await promoteDataset(meta.id)
      onDone()
    } catch (e) {
      setErr(e)
      setBusy(false)
    }
  }

  // Portalled to <body>: one of the two callers is the catalog card, whose
  // `.ds-card-actions` is a `z-index: 2` stacking context. Rendered in place the
  // `position: fixed` overlay would be stacked at 2 for the whole page — under
  // the sticky topbar (z-index 5) and under the action row of every card after
  // this one, which would show through the dim backdrop.
  return createPortal(
    <div
      className="rules-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="rules-modal" role="dialog" aria-modal="true" aria-label={t('kantan:s8.title')}>
        <header className="rules-modal-head">
          <span className="ds-section-title">{t('kantan:s8.title')}</span>
          <button
            type="button"
            className="rules-modal-close"
            aria-label={t('gallery:rules.viewer.close')}
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <div className="rules-modal-body">
          <p className="promote-note">
            {t('kantan:s8.nameLabel')}: <strong>{name || meta.id}</strong>
          </p>
          <p className="promote-note">{t('gallery:promote.dialogContent', { counts: countsText })}</p>
          <p className="promote-note">
            {words
              ? t('kantan:s8.words', { reuse: words.reuse.length, added: words.added.length })
              : t('gallery:promote.alignmentLoading')}
          </p>
          {/* The words themselves, in the same shape S8 shows them: the summary
              sentence is the decision, the identifiers are the receipt. They used
              to be dumped raw ("hasMeasurement、seebeckCoefficient…") next to the
              publish button, which is where a reader without the vocabulary
              stalls. */}
          {words && words.reuse.length + words.added.length > 0 && (
            <details className="ds-advisory-raw">
              <summary>{t('kantan:s8.wordsList')}</summary>
              <WordGroup head={t('kantan:s8.wordsReuse')} iris={words.reuse} labels={labels} />
              <WordGroup head={t('kantan:s8.wordsNew')} iris={words.added} labels={labels} />
            </details>
          )}
          <p className="ingest-hint">{t('kantan:s8.promise')}</p>
          {!name && <p className="ingest-hint">{t('kantan:s8.needName')}</p>}
          <div className="rules-viewer-actions">
            <button type="button" className="promote-btn" disabled={busy || !name} onClick={publish}>
              {busy
                ? t('kantan:s8.publishing')
                : isRepromote
                  ? t('gallery:promote.repromoteSubmit')
                  : t('kantan:s8.publish')}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={busy}
              onClick={onClose}
            >
              {t('gallery:promote.cancel')}
            </button>
          </div>
          {err != null && (
            <ErrorNote
              err={err}
              titleKey={isRepromote ? 'gallery:promote.repromoteError' : 'gallery:promote.error'}
            />
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}

/**
 * The one line that says where this dataset is and what the next move is, kept
 * directly under the name on every tab. Each button leads to the control that
 * already does the work (the files tab's ingest / append / replace forms, the
 * publish dialog) — nothing here performs a state change on its own, so the
 * card, the band and the detail can never disagree about what publishing means.
 */
function StateBand({
  meta,
  onGo,
  onPublish,
  onChanged,
}: {
  meta: LiveDataset['meta']
  onGo: (ctl: Exclude<ControlFocus, null>) => void
  onPublish: () => void
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const stage = datasetStage(meta)
  const retracted = meta.status === 'retracted'
  const version = meta.version ?? 0
  const isDoc = meta.source_kind === 'xml'
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)

  // 公開済みは名前の横の「公開済み」バッジが既に言っている。二度言わない。
  const line = retracted
    ? t('gallery:band.retractedLine')
    : stage === 'design'
      ? t('gallery:band.designLine')
      : stage === 'promoted'
        ? ''
        : version >= 1
          ? t('gallery:band.stagedLine')
          : t('gallery:band.draftLine')

  const actions: ReactNode[] = []
  if (retracted) {
    actions.push(
      <button
        key="reinstate"
        type="button"
        className="btn btn--soft btn--sm"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setErr(null)
          try {
            await reinstateDataset(meta.id)
            onChanged()
          } catch (e) {
            setErr(e)
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? t('gallery:lifecycle.reinstating') : t('gallery:band.republish')}
      </button>,
    )
  } else if (stage === 'design') {
    if (isDoc || meta.has_rml)
      actions.push(
        <button
          key="ingest"
          type="button"
          className="btn btn--soft btn--sm"
          onClick={() => onGo('ingest')}
        >
          {t('gallery:ingest.submit')}
        </button>,
      )
  } else if (stage === 'ingested') {
    actions.push(
      version >= 1 ? (
        <button
          key="repromote"
          type="button"
          className="btn btn--soft btn--sm"
          onClick={() => onGo('promote')}
        >
          {t('gallery:promote.repromoteSubmit')}
        </button>
      ) : (
        <button key="publish" type="button" className="btn btn--soft btn--sm" onClick={onPublish}>
          {t('gallery:band.publish')}
        </button>
      ),
    )
  } else {
    // S9 hands a published dataset three exits: ask it something, connect it,
    // grow it. Publishing from the detail skipped S9 entirely, so the first two
    // were missing here — and the question arrives pre-filled the same way the
    // wizard's chips do (deterministic trial queries; nothing is auto-sent).
    // 公開後の band は「公開済み」バッジと同じことを二度言い、その下に並ぶ 4 つは
    // どれも本籍のタブに実体がある（つなぐ=つながり、足す/差し替える=取り込み・公開）。
    // 毎タブの一番上に居座る理由がないので出さない。カタログから直接聞ける価値のある
    // 「質問してみる」だけ、中身タブの末尾に本籍を移した（AskAboutDataset）。
  }

  // 言うことも、する操作も無い状態（＝公開済み）では帯そのものを出さない。
  if (!line && actions.length === 0 && err == null) return null
  return (
    <div className="promote-control">
      {line && <p className="ingest-note">{line}</p>}
      {actions.length > 0 && <div className="rules-viewer-actions">{actions}</div>}
      {err != null && <ErrorNote err={err} titleKey="gallery:lifecycle.error" />}
    </div>
  )
}

/**
 * 「このデータに質問してみる」— 中身を見たあとに聞く、が自然な順。公開後の帯に
 * 他の 3 つと並んでいたが、あれらは本籍のタブに実体があるのに対し、これはカタログ
 * から Ask へ質問を持って直行する固有の導線なので、中身タブの末尾に移した。
 */
function AskAboutDataset({ meta }: { meta: LiveDataset['meta'] }) {
  const { t } = useTranslation()
  return (
    <div className="rules-viewer-actions">
      <button
        type="button"
        className="btn btn--soft btn--sm"
        onClick={() => {
          void (async () => {
            try {
              const tr = await fetchTrialQueries(meta.id)
              const q =
                !tr.available || (tr.classes.length === 0 && !tr.entities)
                  ? null
                  : tr.classes.length === 1
                    ? t('kantan:s7.qCountOne', {
                        label: tr.classes[0].label ?? localName(tr.classes[0].iri),
                      })
                    : tr.classes.length > 1
                      ? t('kantan:s7.qCountMany')
                      : t('kantan:s7.qCountAny')
              if (q) prefillAskQuestion(q)
            } catch {
              /* the Ask screen still opens — just without a question in it */
            }
            window.location.hash = '#/ask'
          })()
        }}
      >
        {t('gallery:band.ask')}
      </button>
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
  onCreateCrosswalk,
  onOpenMap,
  onRedesign,
  onAddData,
  detailFocus = null,
  onDetailFocusConsumed,
}: {
  dataset: CatalogDataset
  perspectives: CrosswalkPerspective[]
  tab: DetailTab
  onTab: (t: DetailTab) => void
  highlight?: string | null
  onChanged: () => void
  onBack?: () => void
  onOpenCrosswalk?: () => void
  /** つながりを作るフローへ直行（このデータセットが未接続のとき）。 */
  onCreateCrosswalk?: () => void
  onOpenMap?: () => void
  onRedesign?: (target: RedesignTarget) => void
  /** 「データを追加」へ戻る（設計が無く、この画面では先へ進めないとき）。 */
  onAddData?: () => void
  /** Where the caller wants this page to land (かんたん S9 の導線など)。 */
  detailFocus?: DetailFocus | null
  onDetailFocusConsumed?: () => void
}) {
  const { t } = useTranslation()
  const meta = dataset.live?.meta
  // Design weaknesses (entities with no link between them, unmapped columns).
  // Checked LIVE rather than read from meta so it (a) covers datasets published
  // before the field existed — the live ZEM is exactly that — and (b) always
  // reflects the design as it stands now: fix the link, redesign, and the notice
  // disappears on its own. Falls back to the persisted list when the call fails.
  const [advisories, setAdvisories] = useState<string[]>(meta?.advisories ?? [])
  useEffect(() => {
    const id = meta?.id
    if (!id) return
    let cancelled = false
    validateDesign(id)
      .then((check) => {
        if (!cancelled) setAdvisories(check.advisories)
      })
      .catch(() => {
        /* advisory only — keep whatever the registry recorded */
      })
    return () => {
      cancelled = true
    }
  }, [meta?.id])
  // The dataset's own words (model.yaml labels + the Mapping IR units), so the
  // 設計図 tab can say 「測定 Measurement」 instead of a bare English identifier
  // (K8): the label is what the wizard's 項目の意味 table and the rules tab already
  // show for the very same thing. Advisory only — an unlabelled term is printed
  // exactly as the design wrote it.
  const [rules, setRules] = useState<DatasetRules | null>(null)
  useEffect(() => {
    const id = meta?.id
    if (!id) return
    let cancelled = false
    getDatasetRules(id)
      .then((r) => {
        if (!cancelled) setRules(r)
      })
      .catch(() => {
        /* labels are enrichment — the identifiers still render */
      })
    return () => {
      cancelled = true
    }
  }, [meta?.id])
  const termLabels: TermLabels = useMemo(() => {
    const index: TermLabels = {}
    for (const [iri, label] of Object.entries(rules?.labels ?? {})) {
      if (!label) continue
      index[iri] = label
      const local = localName(iri)
      if (!(local in index)) index[local] = label
    }
    return index
  }, [rules])
  /** Human unit notation per predicate (IRI and local name), from the Mapping IR. */
  const termUnits: Record<string, string> = useMemo(() => {
    const index: Record<string, string> = {}
    for (const m of rules?.maps ?? [])
      for (const p of m.properties) {
        if (!p.unit) continue
        if (p.predicate_iri) index[p.predicate_iri] = p.unit
        const local = localName(p.predicate_iri || p.predicate)
        if (local && !(local in index)) index[local] = p.unit
      }
    return index
  }, [rules])
  // Which control on the files tab the header band just sent the reader to.
  const [focusCtl, setFocusCtl] = useState<ControlFocus>(null)
  const [publishing, setPublishing] = useState(false)
  function goToControl(ctl: Exclude<ControlFocus, null>) {
    onTab('files')
    setFocusCtl(null)
    // Re-arm on the next frame so pressing the same button twice scrolls again.
    requestAnimationFrame(() => setFocusCtl(ctl))
  }
  // 取り込んだファイル: the design-time source + every appended batch (no dedupe
  // detection yet — that needs a backend content hash; surfaced as a note).
  const fileRows: { name: string; type: string; when: string; tag: string }[] = []
  if (meta) {
    const typeLabel = sourceTag(t, meta)
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
  // Reading order = the order the work happens in, with the two developer-facing
  // tabs (the raw rules, the SPARQL tool list) last. ツール used to be second,
  // one tab away from 中身, and it opens on saved SPARQL and an AI drafting box.
  const tabs: [DetailTab, string][] = [
    ['structure', t('gallery:tab.structure')],
    ['files', t('gallery:tab.files')],
    ['connect', t('gallery:tab.connect')],
    ['design', t('gallery:tab.design')],
    ['tools', t('gallery:tab.tools')],
  ]
  // The design tab's two destinations, for its sub-nav.
  const rulesRef = useRef<HTMLDivElement | null>(null)
  const groundingRef = useRef<HTMLDivElement | null>(null)
  const showGrounding = dataset.classIris.length + dataset.predicates.length > 0
  // A caller asked to LAND on something, not just on a tab. Acting on it once —
  // and telling the caller it was consumed — keeps a later visit to the same
  // dataset from yanking the page around for a reason nobody remembers.
  useEffect(() => {
    if (!detailFocus) return
    const ctl = detailFocus === 'grounding' ? null : detailFocus
    // The grounding section is not on the page until the tab switch has rendered
    // it (and it exists only once the dataset has minted terms — i.e. after
    // publish). Staying UNCONSUMED until the ref is real is what makes this land:
    // giving up on the first pass would leave the person at the top of a long
    // page, hunting for the very button they just pressed.
    const el = ctl ? null : groundingRef.current
    if (!ctl && !el) return
    // Next frame, so the scroll happens against the laid-out page (and so no
    // state is set synchronously inside the effect).
    let settle: ReturnType<typeof setTimeout> | undefined
    const id = requestAnimationFrame(() => {
      if (el) {
        el.scrollIntoView({ block: 'start' })
        // The section fills itself in asynchronously (existing alignments, then
        // a candidate lookup per term), so the page grows underneath the scroll
        // and the first jump is undone by the reflow. Land once more when it has
        // settled — measured at scrollTop ≈ 1 without this.
        settle = setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }), 400)
      } else if (ctl) goToControl(ctl)
      onDetailFocusConsumed?.()
    })
    return () => {
      cancelAnimationFrame(id)
      if (settle) clearTimeout(settle)
    }
    // Re-runs when the section appears, not on every render of this page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detailFocus, tab, showGrounding])
  // dataset rename (kept from #231) — inline edit in the detail header.
  const [editingName, setEditingName] = useState(false)
  const [draftName, setDraftName] = useState(dataset.name)
  const [renaming, setRenaming] = useState(false)
  const [renameErr, setRenameErr] = useState<unknown>(null)

  async function saveRename() {
    const n = draftName.trim()
    if (!n || n === dataset.name) {
      setEditingName(false)
      return
    }
    setRenaming(true)
    setRenameErr(null)
    try {
      // CatalogDataset.id is the synthetic catalog id (`live-<id>`); the registry id is
      // dataset.live.meta.id (what every other control uses). Strip the prefix as a fallback.
      const realId = dataset.live?.meta.id ?? dataset.id.replace(/^live-/, '')
      await renameDataset(realId, n)
      setEditingName(false)
      onChanged() // name changed — refresh the catalog list + detail
    } catch (e) {
      setRenameErr(e)
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
                // IME 変換確定の Enter（isComposing）で改名を確定させない
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) saveRename()
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
                setRenameErr(null)
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
        {renameErr != null && <ErrorNote err={renameErr} titleKey="gallery:rename.error" />}
        <div className="ds-tabs">
          {tabs.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`ds-tab${tab === id ? ' active' : ''}`}
              onClick={() => onTab(id)}
              aria-current={tab === id || undefined}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* 「今どの段階で、次に何をすれば完了か」. Without this the detail opens on a
          list of English identifiers and the publish button is a tab away and a
          scroll down; a reader who came from the wizard has no idea the dataset
          is still a draft. One sentence + the one or two moves that stage allows,
          always in the same place. */}
      {meta && (
        <StateBand
          meta={meta}
          onGo={goToControl}
          onPublish={() => setPublishing(true)}
          onChanged={onChanged}
        />
      )}

      {publishing && meta && (
        <PublishDialog
          meta={meta}
          counts={dataset.counts}
          labels={termLabels}
          onClose={() => setPublishing(false)}
          onDone={() => {
            setPublishing(false)
            onChanged()
          }}
        />
      )}

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

      {/* Destructive lifecycle actions (retract / delete) stay on the catalog
          cards; the band above carries the forward move for the current stage,
          and the full forms (first ingest, publish, append, replace) live in the
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
            <>
              <div className="ds-subhead">{t('gallery:design.classesHead')}</div>
              <div className="ds-classes">
                {dataset.classes.map((c) => (
                  <span
                    key={c}
                    className={`class-chip${c === highlight ? ' onto-class-chip--focus' : ''}`}
                  >
                    {termLabels[c] && <span>{termLabels[c]}</span>}
                    <code className="class-chip-en">{c}</code>
                  </span>
                ))}
              </div>
            </>
          ) : (
            <p className="ds-empty-note">{t('gallery:design.noClasses')}</p>
          )}

          {dataset.predicates.length > 0 && (
            <>
              <div className="ds-subhead">{t('gallery:design.predicatesHead')}</div>
              <div className="ds-classes">
                {dataset.predicates.map((p) => {
                  const local = localName(p)
                  const label = termLabels[p] ?? termLabels[local]
                  const unit = termUnits[p] ?? termUnits[local]
                  return (
                    <span key={p} className="class-chip" title={p}>
                      {label && <span>{label}</span>}
                      <code className="class-chip-en">{local}</code>
                      {unit && <code className="class-chip-en">{unit}</code>}
                    </span>
                  )
                })}
              </div>
            </>
          )}

          {/* 構造図。⑤「ためす」で見た形と**同じ部品**で描く — 同じデータセットを
              2 つの絵の言葉で見せない（利用者評価 2026-08-29）。ここだけは箱の中に
              項目も並べる: このページは「何が入っているか」を見に来る場所で、
              形だけでは足りない。図は AI の散文ではなく保存済みの取り込みルール
              から組む。 */}
          {rules && rules.maps.length > 0 && (
            <div className="ds-diagram-block">
              <div className="ds-subhead">{t('gallery:design.diagramSummary')}</div>
              <div className="onto-diagram">
                <ShapeGraph
                  shape={rulesShape(rules, { withFields: true })}
                  ariaLabel={t('gallery:design.diagramSummary')}
                  perRow={3}
                  nodeWidth={248}
                  maxHeight={720}
                />
              </div>
            </div>
          )}

          {/* Sits directly under the diagram on purpose: "why are there no
              lines between these boxes?" is the question the picture provokes,
              and this is the answer — with the join-key candidates that say how
              to draw them. Not role="alert": the dataset is fine to use, this
              is what it cannot yet answer. */}
          {advisories.length > 0 && (
            <div className="ds-advisories">
              <div className="ds-subhead">{t('gallery:design.advisoryHead')}</div>
              {/* Plain sentences, not the raw advisories: those are precise
                  English written for the AI fix to act on, and thirteen of them
                  verbatim buried this page in untranslated jargon (2026-07-24).
                  The originals stay one click away. */}
              <ul className="ds-advisory-list">
                {plainAdvisories(advisories, termLabels).map((a, i) => (
                  <li key={i}>{a.text}</li>
                ))}
              </ul>
              <details className="ds-advisory-raw">
                <summary>{t('gallery:advisory.rawSummary')}</summary>
                <ul className="ds-advisory-list">
                  {advisories.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </details>
              {/* The same control the 設計を見直す tab offers — reused whole so
                  the "no stored design" case and the proposal fetch behave
                  identically here. */}
              {onRedesign && dataset.live && (
                <RedesignControl
                  meta={dataset.live.meta}
                  onRedesign={onRedesign}
                  onAddData={onAddData}
                  advisories={advisories}
                />
              )}
            </div>
          )}

          {/* 「rho は電気抵抗率、単位は ohm*m、それは QUDT の ohm metre のこと」は
              ひと続きの話。途中でタブをまたぐ理由がないので、項目と単位のすぐ下に
              置く。以前は 変換ルール（技術情報）の一番下にあり、意味の判断が技術
              情報の見出しの下に隠れていた。 */}
          {showGrounding && (
            <div ref={groundingRef}>
              <DatasetGrounding dataset={dataset} />
            </div>
          )}

          {/* "The column means something else" is realised long after publishing,
              and on a dataset with nothing worth flagging the only way back used
              to be a tab labelled 技術情報. The way to fix a design belongs with
              the design. */}
          {advisories.length === 0 && onRedesign && dataset.live && (
            <RedesignControl
              meta={dataset.live.meta}
              onRedesign={onRedesign}
              onAddData={onAddData}
            />
          )}

          {/* 中身を見たあとに聞く、が自然な順。公開済みのときだけ（下書きに質問して
              も答えは出ない）。 */}
          {dataset.live && datasetStage(dataset.live.meta) === 'promoted' &&
            dataset.live.meta.status !== 'retracted' && (
              <AskAboutDataset meta={dataset.live.meta} />
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
            <>
              {/* "Run 「データを取り込む」 first" named a control on another tab
                  without saying which, and left no way to get there. */}
              <p className="ds-empty-note">{t('gallery:tools.none')}</p>
              <button
                type="button"
                className="btn btn--soft btn--sm"
                onClick={() => goToControl('ingest')}
              >
                {t('gallery:tools.goFiles')}
              </button>
            </>
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
                  {/* 設計段階（未取り込み）のソースに「取り込み済み」と出すのは事実に反する */}
                  {meta && datasetStage(meta) === 'design' ? (
                    <span className="ds-file-status ds-file-status--pending">
                      {t('gallery:files.statusSavedOnly')}
                    </span>
                  ) : (
                    <span className="ds-file-status">✓ {t('gallery:files.statusIngested')}</span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="ds-empty-note">{t('gallery:files.none')}</p>
          )}
          {/* A roadmap footnote about content hashes used to sit here on every
              visit; what the reader can act on is already in the note above. */}

          {/* Operations on this dataset's ingested data live here (the natural home
              for ingest / append / re-ingest / promote / lifecycle). State-gated, so
              usually only one or two are visible for a given dataset stage. */}
          {dataset.live && (
            <div className="ds-detail-controls">
              {/* Task E: ingest gate for design-stage datasets (no facts yet). */}
              <IngestControl
                meta={dataset.live.meta}
                onChanged={onChanged}
                onRedesign={onRedesign}
                onAddData={onAddData}
                labels={termLabels}
                focus={focusCtl === 'ingest'}
              />
              {/* S4 (re-)promote human-gate. */}
              <PromoteControl
                meta={dataset.live.meta}
                counts={dataset.counts}
                labels={termLabels}
                onChanged={onChanged}
                focus={focusCtl === 'promote'}
              />
              {/* 育てる: 「足す」と「差し替える」 are one choice, not two forms open at
                  once — a promoted tabular dataset used to show both in full. */}
              <GrowBlock
                meta={dataset.live.meta}
                onChanged={onChanged}
                onRedesign={onRedesign}
                onAddData={onAddData}
                labels={termLabels}
                focus={focusCtl}
                onFocus={setFocusCtl}
              />
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
              {myPersp.map((p, i) => (
                <div className="ds-conn-item" key={p.perspective_id}>
                  <span className="ds-conn-icon">
                    <ConnectIcon size={15} />
                  </span>
                  {/* An unnamed connection used to show its raw id here. It is a
                      machine-minted string; the number is what the reader can use. */}
                  <span className="ds-conn-name" title={p.perspective_id}>
                    {p.dataset?.name || t('gallery:connect.unnamed', { n: i + 1 })}
                  </span>
                  <span className="ds-conn-concept">
                    {t('gallery:connect.concept', {
                      concept: (p.config?.concepts ?? [])
                        .map((c) => conceptLabel(c.name))
                        .join(' · '),
                    })}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <>
              <p className="ds-empty-note">{t('gallery:connect.none')}</p>
              {/* Saying "not connected to anything" without offering the way to
                  connect it is where this tab used to stop. */}
              {onCreateCrosswalk && (
                <button type="button" className="promote-btn" onClick={onCreateCrosswalk}>
                  {t('gallery:connect.createBtn')}
                </button>
              )}
            </>
          )}
          <div className="ds-conn-links">
            {myPersp.length > 0 && onCreateCrosswalk && (
              <button type="button" className="btn btn--ghost btn--sm" onClick={onCreateCrosswalk}>
                <ConnectIcon size={14} /> {t('gallery:connect.createMore')}
              </button>
            )}
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
      {/* 元のファイルとの対応 — WHERE THE DATA CAME FROM, and nothing else.
          This tab used to hold three unrelated things: the column mapping (source),
          the RML (source), 「標準のことばに合わせる」 (meaning), and a second copy of
          「設計を見直す」. Grounding a term to QUDT/FOAF is a judgement about MEANING,
          not a technical detail, so it moved to 中身 next to the columns and units it
          talks about — and the two in-page jump links that existed only to reach it
          from here went with it. What is left answers one question. */}
      {tab === 'design' && (
        <div className="ds-tab-body">
          <div ref={rulesRef}>
            <RulesSection dataset={dataset} />
          </div>
        </div>
      )}
      </div>
    </div>
  )
}

/** Reuse/new counts for `kantan:s8.words` — structural terms excluded, once. */
function wordCounts(alignment: AlignmentReport): { reuse: number; added: number } {
  const w = alignmentWordSplit(alignment)
  return { reuse: w.reuse.length, added: w.added.length }
}

/**
 * "設計を見直す" (redesign): reopen this dataset's STORED design in the workbench so
 * the user can refine/edit it (e.g. fix a wrong column reference or function param now
 * surfaced by ingest validation) and re-materialize the SAME dataset — no delete +
 * recreate, identity / graphs / lifecycle / source preserved. It loads the persisted
 * proposal markdown (fetchProposal), then hands a RedesignTarget to the workbench.
 *
 * Disabled with a hint when the dataset has no stored design (`has_proposal` false) —
 * those were materialized before the design was persisted, so reopening would lose the
 * existing artifacts; the user recreates instead.
 */
function RedesignControl({
  meta,
  onRedesign,
  onAddData,
  advisories,
}: {
  meta: LiveDataset['meta']
  onRedesign: (target: RedesignTarget) => void
  /** The only way out for a dataset with no stored design — "recreate it" used
   *  to be an instruction with no button behind it. */
  onAddData?: () => void
  /** Carried into the review so the reviewer sees what prompted it. */
  advisories?: string[]
}) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  // Two different things used to share one string state: "this dataset has no
  // stored design" (a fact about the dataset, already in the reader's words) and
  // "the fetch failed" (which arrived as `proposal: HTTP 500`).
  const [note, setNote] = useState('')
  const [err, setErr] = useState<unknown>(null)
  const hasProposal = meta.has_proposal !== false

  async function onClick() {
    setBusy(true)
    setNote('')
    setErr(null)
    try {
      const p = await fetchProposal(meta.id)
      if (!p.has_proposal || !p.proposal_md.trim()) {
        setNote(t('gallery:redesign.noProposal'))
        return
      }
      onRedesign({
        datasetId: meta.id,
        datasetName: p.dataset_name || meta.name,
        proposalMd: p.proposal_md,
        advisories,
      })
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ds-redesign">
      <div className="ds-redesign-text">
        <div className="ds-subhead">{t('gallery:redesign.head')}</div>
        <p className="ingest-hint">
          {hasProposal ? t('gallery:redesign.note') : t('gallery:redesign.noProposal')}
        </p>
      </div>
      <button
        type="button"
        className="btn btn--soft btn--sm"
        disabled={busy || !hasProposal}
        onClick={onClick}
      >
        {busy ? t('gallery:redesign.loading') : t('gallery:redesign.open')}
      </button>
      {(!hasProposal || note !== '') && onAddData && (
        <button type="button" className="btn btn--soft btn--sm" onClick={onAddData}>
          {t('gallery:redesign.recreate')}
        </button>
      )}
      {note && <p className="ingest-hint">{note}</p>}
      {err != null && <ErrorNote err={err} titleKey="gallery:redesign.error" />}
    </div>
  )
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
  meta,
  onRedesign,
  onAddData,
  labels,
}: {
  err: unknown
  errorKey: string
  meta: LiveDataset['meta']
  onRedesign?: (target: RedesignTarget) => void
  /** Carried down so the dataset with no stored design keeps its ONE exit here
   *  too: this branch renders RedesignControl, whose review button is disabled
   *  for exactly those datasets — without it the reader is told to start over
   *  with nothing to press. */
  onAddData?: () => void
  labels?: TermLabels
}) {
  const { t } = useTranslation()
  if (err instanceof IngestValidationError && err.issues.length > 0) {
    return (
      <div className="promote-err ingest-issues">
        {/* The issues are precise English written for the AI fix to act on. Shown
            raw they told the reader to "fix them" without saying who fixes what;
            the same deterministic classifier the advisories use says it in their
            language, and the way out is the control that hands these very
            strings back to the AI. */}
        <p className="ingest-issues-head">
          {t('gallery:ingest.validationHead', { n: err.issues.length })}
        </p>
        <ul>
          {plainAdvisories(err.issues, labels).map((a, i) => (
            <li key={i}>{a.text}</li>
          ))}
        </ul>
        <details className="ds-advisory-raw">
          <summary>{t('gallery:advisory.rawSummary')}</summary>
          <ul className="ds-advisory-list">
            {err.issues.map((issue, i) => (
              <li key={i}>{issue}</li>
            ))}
          </ul>
        </details>
        {onRedesign && (
          <RedesignControl
            meta={meta}
            onRedesign={onRedesign}
            onAddData={onAddData}
            advisories={err.issues}
          />
        )}
      </div>
    )
  }
  return <ErrorNote err={err} titleKey={errorKey} />
}

function IngestControl({
  meta,
  onChanged,
  onRedesign,
  onAddData,
  labels,
  focus,
}: {
  meta: LiveDataset['meta']
  onChanged: () => void
  onRedesign?: (target: RedesignTarget) => void
  /** 設計が無いデータセットの唯一の出口（「データを追加」へ戻る）。 */
  onAddData?: () => void
  labels?: TermLabels
  focus?: boolean
}) {
  const { t } = useTranslation()
  const rootRef = useRef<HTMLDivElement | null>(null)
  useFocusScroll(focus, rootRef)
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [done, setDone] = useState<IngestResult | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [cancelled, setCancelled] = useState(false)
  const [job, setJob] = useState<IngestJobHandle | null>(null)
  const [lastPulseAt, setLastPulseAt] = useState<number | null>(null)

  // A document dataset (source_kind=xml) has NO RML — it ingests through the
  // deterministic structurer, not Morph-KGC. So the "no RML" dead-end is CSV/JSON-
  // only. A document created via the "文書を追加" panel whose ingest failed (Docling
  // down, a disconnect) lands here at the design stage; because its source was
  // persisted at create, it can be re-ingested straight from the catalog with no
  // re-upload — instead of forcing a re-upload that would mint a duplicate dataset.
  const isDocument = meta.source_kind === 'xml'
  const renders = datasetStage(meta) === 'design' && (isDocument || !!meta.has_rml)

  // Reload recovery: a saved in-flight ingest job for THIS dataset re-attaches
  // (SSE replay recovers everything, including a completion that happened while
  // the tab was gone). Gated on `renders` so the sibling ReingestControl — which
  // handles the other stages — doesn't race for the same job.
  useEffect(() => {
    if (!renders) return
    const saved = loadIngestJob()
    if (!saved || saved.kind !== 'ingest' || saved.datasetId !== meta.id) return
    const handle = resumeIngestJob(saved.jobId, meta.id, setProgress, () =>
      setLastPulseAt(Date.now()),
    )
    void track(handle)
    return () => handle.close() // release the stream; the server job keeps running
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta.id, renders])

  // Await one job to its end and settle every piece of UI state. Shared by the
  // fresh-start and the reload-recovery paths.
  async function track(handle: IngestJobHandle) {
    saveIngestJob({ jobId: handle.jobId, datasetId: meta.id, kind: 'ingest' })
    setJob(handle)
    setBusy(true)
    setErr(null)
    setCancelled(false)
    try {
      setDone(await handle.result)
      onChanged() // design → draft: refresh so promote control appears
    } catch (e) {
      if (e instanceof IngestCancelledError) {
        setCancelled(true) // clean stop — nothing was committed server-side
        setProgress(null)
      } else if (e instanceof StaleIngestJobError) {
        setProgress(null) // saved id belonged to another dataset — silent reset
      } else {
        setErr(e)
      }
    } finally {
      clearIngestJob(handle.jobId)
      setJob(null)
      setBusy(false)
    }
  }

  // Only design-stage needs this gate: ingested → promote, promoted → done.
  if (datasetStage(meta) !== 'design') return null

  if (!isDocument && !meta.has_rml) {
    return (
      <div className="ingest-gate" ref={rootRef}>
        <div className="ds-subhead">{t('gallery:ingest.head')}</div>
        {/* Nothing on this screen can produce the missing design, so the only
            honest next move is the one that can — as a button, not a sentence. */}
        <p className="ingest-hint">{t('gallery:ingest.noRml')}</p>
        {onAddData && (
          <button type="button" className="btn btn--soft btn--sm" onClick={onAddData}>
            {t('gallery:ingest.goAddData')}
          </button>
        )}
      </div>
    )
  }

  if (done) {
    return (
      <div className="ingest-gate" ref={rootRef}>
        <div className="ds-subhead">{t('gallery:ingest.head')}</div>
        {/* K12: the triple count said nothing the reader could check. */}
        <p className="ingest-ok">{t('gallery:ingest.done')}</p>
      </div>
    )
  }

  const hasSource = !!meta.has_source
  const isJson = meta.source_kind === 'json'
  const sourceLabel = isDocument
    ? t('gallery:ingest.sourceDocument')
    : isJson
      ? 'JSON'
      : t('gallery:sourceKind.tabular')
  const accept = isDocument ? '.xml,.docx,.pdf' : isJson ? '.json,.geojson' : TABULAR_ACCEPT
  const canIngest = !busy && (hasSource || files.length > 0)

  async function onIngest() {
    setBusy(true)
    setErr(null)
    setProgress(null)
    setCancelled(false)
    try {
      // hasSource → ingest with no upload (server uses the persisted source).
      const handle = await startIngestJob(meta.id, hasSource ? [] : files, setProgress, () =>
        setLastPulseAt(Date.now()),
      )
      await track(handle)
    } catch (e) {
      // startIngestJob failure (the POST itself) — track() handles its own.
      setErr(e)
      setBusy(false)
    }
  }

  return (
    <div className="ingest-gate" ref={rootRef}>
      <div className="ds-subhead">{t('gallery:ingest.head')}</div>
      <p className="ingest-note">
        {isDocument ? t('gallery:ingest.noteDocument') : t('gallery:ingest.note')}
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
              accept={accept}
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
      {busy && (
        <>
          <IngestProgressView
            progress={progress}
            onCancel={job ? job.cancel : undefined}
            lastPulseAt={lastPulseAt}
          />
          {/* The wizard says this at the same point; a minutes-long wait in the
              catalog left the reader guessing whether leaving cancels it. */}
          <p className="ingest-hint">{t('gallery:ingest.keepGoing')}</p>
        </>
      )}
      {cancelled && <p className="ingest-hint">{t('gallery:ingest.cancelled')}</p>}
      {err != null && (
        <IngestError
          err={err}
          errorKey="gallery:ingest.error"
          meta={meta}
          onRedesign={onRedesign}
          onAddData={onAddData}
          labels={labels}
        />
      )}
    </div>
  )
}

/**
 * 「足す」か「差し替える」か — one question, not two open forms.
 *
 * A promoted tabular dataset used to render the append form AND the re-ingest
 * form in full, each with its own paragraph, file picker and button: two
 * paragraphs to read before knowing which one you wanted. S9 already settled
 * this as two buttons, so the catalog says it the same way. When only one of the
 * two applies (a document dataset, a staged draft) there is no choice to make,
 * and the controls render exactly as before.
 */
function GrowBlock({
  meta,
  onChanged,
  onRedesign,
  onAddData,
  labels,
  focus,
  onFocus,
}: {
  meta: LiveDataset['meta']
  onChanged: () => void
  onRedesign?: (target: RedesignTarget) => void
  /** The exit for a dataset whose design was never stored — see IngestError. */
  onAddData?: () => void
  labels?: TermLabels
  focus: ControlFocus
  onFocus: (ctl: ControlFocus) => void
}) {
  const { t } = useTranslation()
  // Which pane is open is the same fact as "which control the band points at",
  // so it lives in one place — press 「新しい測定分を足す」 in the band or here and
  // the result is identical.
  const mode = focus === 'append' ? 'append' : focus === 'reingest' ? 'replace' : null

  const stage = datasetStage(meta)
  const retracted = meta.status === 'retracted'
  const isDoc = meta.source_kind === 'xml'
  const canAppend = stage === 'promoted' && !retracted && (isDoc || !!meta.has_rml)
  const canReplace = stage !== 'design' && !retracted && !!meta.has_rml

  const appendPane = (
    <>
      <AppendControl meta={meta} onChanged={onChanged} embedded focus={focus === 'append'} />
      <DocumentAppendControl
        meta={meta}
        onChanged={onChanged}
        embedded
        focus={focus === 'append'}
      />
    </>
  )
  const replacePane = (
    <ReingestControl
      meta={meta}
      onChanged={onChanged}
      onRedesign={onRedesign}
      onAddData={onAddData}
      labels={labels}
      embedded
      focus={focus === 'reingest'}
    />
  )

  if (!(canAppend && canReplace)) {
    return (
      <>
        <AppendControl meta={meta} onChanged={onChanged} focus={focus === 'append'} />
        <DocumentAppendControl meta={meta} onChanged={onChanged} focus={focus === 'append'} />
        <ReingestControl
          meta={meta}
          onChanged={onChanged}
          onRedesign={onRedesign}
          onAddData={onAddData}
          labels={labels}
          focus={focus === 'reingest'}
        />
      </>
    )
  }

  return (
    <div className="ingest-gate">
      <div className="ds-subhead">{t('gallery:grow.head')}</div>
      <p className="ingest-hint">{t('gallery:grow.hint')}</p>
      <div className="rules-viewer-actions">
        <button
          type="button"
          className={`btn btn--sm ${mode === 'append' ? 'btn--soft' : 'btn--ghost'}`}
          aria-pressed={mode === 'append'}
          onClick={() => onFocus(mode === 'append' ? null : 'append')}
        >
          {t('gallery:grow.append')}
        </button>
        <button
          type="button"
          className={`btn btn--sm ${mode === 'replace' ? 'btn--soft' : 'btn--ghost'}`}
          aria-pressed={mode === 'replace'}
          onClick={() => onFocus(mode === 'replace' ? null : 'reingest')}
        >
          {t('gallery:grow.replace')}
        </button>
      </div>
      {mode === 'append' && appendPane}
      {mode === 'replace' && replacePane}
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
function AppendControl({
  meta,
  onChanged,
  embedded,
  focus,
}: {
  meta: LiveDataset['meta']
  onChanged: () => void
  /** Rendered inside the 「データを育てる」 block — the box is the block's. */
  embedded?: boolean
  focus?: boolean
}) {
  const { t } = useTranslation()
  const rootRef = useRef<HTMLDivElement | null>(null)
  useFocusScroll(focus, rootRef)
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<AppendResult | null>(null)
  const [err, setErr] = useState<unknown>(null)

  // Append grows a LIVE feed: a promoted, active dataset with declarative RML.
  if (datasetStage(meta) !== 'promoted' || meta.status === 'retracted' || !meta.has_rml) {
    return null
  }

  const isJson = meta.source_kind === 'json'
  const sourceLabel = isJson ? 'JSON' : t('gallery:sourceKind.tabular')
  const canAppend = !busy && files.length > 0

  async function onAppend() {
    setBusy(true)
    setErr(null)
    try {
      const r = await appendToDataset(meta.id, files)
      setDone(r)
      setFiles([])
      onChanged() // append_seq / triple counts changed — refresh the catalog
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={embedded ? '' : 'ingest-gate'} ref={rootRef}>
      <div className="ds-subhead">{t('gallery:append.head')}</div>
      <p className="ingest-note">
        {t('gallery:append.note')}
        {/* GAL-A-40: a single-source design now resolves a differently-named
            batch by its column content (server-side), so the name only still
            matters for a MULTI-source design (ambiguous which source a lone
            file continues) — the note is shown only then. */}
        {(meta.source_files?.length ?? 0) > 1
          ? ` ${t('gallery:append.noteFilename', { names: meta.source_files!.join('、') })}`
          : ''}
      </p>
      {(meta.append_seq ?? 0) > 0 && (
        // K12: "how many times you added" is checkable; "+~4,812 facts" is not.
        <p className="ingest-source">{t('gallery:append.progress', { seq: meta.append_seq })}</p>
      )}
      <div className="ingest-pick">
        <label className="file-btn">
          {t('gallery:ingest.pickLabel', { source: sourceLabel })}
          <input
            type="file"
            accept={isJson ? '.json,.geojson' : TABULAR_ACCEPT}
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
        <p className="ingest-ok">{t('gallery:append.done', { seq: done.append_seq })}</p>
      )}
      {err != null && (
        <ErrorNote
          err={err}
          titleKey="gallery:append.error"
          expectedFiles={meta.source_files}
        />
      )}
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
  embedded,
  focus,
}: {
  meta: LiveDataset['meta']
  onChanged: () => void
  embedded?: boolean
  focus?: boolean
}) {
  const { t } = useTranslation()
  const rootRef = useRef<HTMLDivElement | null>(null)
  useFocusScroll(focus, rootRef)
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  // K12: what was added is counted in DOCUMENTS. The triple total the server
  // returns per batch is a number about our storage, not about the user's work,
  // so it is not carried here at all.
  const [done, setDone] = useState<{ docs: number } | null>(null)
  const [prog, setProg] = useState<{ i: number; n: number } | null>(null)
  const [err, setErr] = useState<unknown>(null)

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
    setErr(null)
    setDone(null)
    setProg(null)
    try {
      // Append each document sequentially (one POST-merge per doc into the live graph).
      for (let i = 0; i < files.length; i++) {
        setProg({ i: i + 1, n: files.length })
        await appendDocument(meta.id, files[i])
      }
      setDone({ docs: files.length })
      setFiles([])
      onChanged() // triple counts / doc count changed — refresh the catalog
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
      setProg(null)
    }
  }

  return (
    <div className={embedded ? '' : 'ingest-gate'} ref={rootRef}>
      <div className="ds-subhead">{t('gallery:docAppend.head')}</div>
      <p className="ingest-note">{t('gallery:docAppend.note')}</p>
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
      {/* K12: what the reader added is documents, not triples — the fact count
          was the only number here and meant nothing to them. */}
      {done && <p className="ingest-ok">{t('gallery:docAppend.doneN', { docs: done.docs })}</p>}
      {err != null && <ErrorNote err={err} titleKey="gallery:docAppend.error" />}
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
function PromoteControl({
  meta,
  counts,
  labels,
  onChanged,
  focus,
}: {
  meta: LiveDataset['meta']
  counts?: CatalogDataset['counts']
  labels?: TermLabels
  onChanged: () => void
  focus?: boolean
}) {
  const { t } = useTranslation()
  const rootRef = useRef<HTMLDivElement | null>(null)
  useFocusScroll(focus, rootRef)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  const [alignment, setAlignment] = useState<AlignmentReport | null>(null)
  const [confirming, setConfirming] = useState(false)
  const version = meta.version ?? 0
  const staged = !!meta.ingested
  const retracted = meta.status === 'retracted'

  // K9/K10: the word summary is part of the decision, so it loads by itself —
  // an optional "check the differences" button is one first-timers never press.
  useEffect(() => {
    if (!staged) return
    let cancelled = false
    getAlignment(meta.id)
      .then((a) => {
        if (!cancelled) setAlignment(a)
      })
      .catch(() => {
        /* reassurance, not a gate */
      })
    return () => {
      cancelled = true
    }
  }, [meta.id, staged])

  if (!staged) {
    // `promoted` stays true through a retraction, so it alone must never be read
    // as "citable" — it said 「引用できます」 about data Ask no longer answers from.
    if (retracted) {
      return (
        <div className="promote-control" ref={rootRef}>
          <p className="promote-note">
            <Trans i18nKey="gallery:lifecycle.retractedStatus" components={{ 1: <strong /> }} />
          </p>
          <button
            type="button"
            className="promote-btn"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              setErr(null)
              try {
                await reinstateDataset(meta.id)
                onChanged()
              } catch (e) {
                setErr(e)
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? t('gallery:lifecycle.reinstating') : t('gallery:band.republish')}
          </button>
          {err != null && <ErrorNote err={err} titleKey="gallery:lifecycle.error" />}
        </div>
      )
    }
    if (meta.promoted) {
      return (
        <div ref={rootRef}>
          <p className="promote-ok">
            {t('gallery:promote.ok', {
              version: version ? t('gallery:promote.okVersion', { version }) : '',
            })}
          </p>
        </div>
      )
    }
    return null
  }

  // A staged version exists. If the dataset was promoted before (version ≥ 1)
  // this is a re-promote: it swaps the live pointer to the new version (part5).
  const isRepromote = version >= 1

  return (
    <div className="promote-control" ref={rootRef}>
      <p className="promote-note">
        {isRepromote ? t('gallery:promote.repromoteNote') : t('gallery:promote.note')}
      </p>
      {alignment ? (
        <div className="alignment-summary">
          {/* The same split, and the same sentence, the publish dialog and the
              wizard's S8 use — a raw reuse/new count here disagreed with S8 on
              the very same dataset (structural terms were in this one). The list
              of the words themselves lives in the publish dialog, where it can
              carry their human names instead of bare identifiers. */}
          <span>{t('kantan:s8.words', wordCounts(alignment))}</span>
        </div>
      ) : (
        <p className="promote-note">{t('gallery:promote.alignmentLoading')}</p>
      )}
      {/* K10: one road to publishing, and it goes through the confirm dialog. */}
      <button type="button" className="promote-btn" onClick={() => setConfirming(true)}>
        {isRepromote ? t('gallery:promote.repromoteSubmit') : t('gallery:promote.submit')}
      </button>
      {confirming && (
        <PublishDialog
          meta={meta}
          counts={counts}
          labels={labels}
          onClose={() => setConfirming(false)}
          onDone={() => {
            setConfirming(false)
            onChanged()
          }}
        />
      )}
      {err != null && (
        <ErrorNote
          err={err}
          titleKey={isRepromote ? 'gallery:promote.repromoteError' : 'gallery:promote.error'}
        />
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
function ReingestControl({
  meta,
  onChanged,
  onRedesign,
  onAddData,
  labels,
  embedded,
  focus,
}: {
  meta: LiveDataset['meta']
  onChanged: () => void
  onRedesign?: (target: RedesignTarget) => void
  /** The exit for a dataset whose design was never stored — see IngestError. */
  onAddData?: () => void
  labels?: TermLabels
  embedded?: boolean
  focus?: boolean
}) {
  const { t } = useTranslation()
  const rootRef = useRef<HTMLDivElement | null>(null)
  useFocusScroll(focus, rootRef)
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [cancelled, setCancelled] = useState(false)
  const [job, setJob] = useState<IngestJobHandle | null>(null)
  const [lastPulseAt, setLastPulseAt] = useState<number | null>(null)

  const stage = datasetStage(meta)
  const renders = stage !== 'design' && meta.status !== 'retracted' && !!meta.has_rml

  // Reload recovery — mirror of IngestControl. This control also picks up a job
  // STARTED at the design stage that completed while the tab was away (the stage
  // has flipped to 'ingested' by the reload, so IngestControl no longer renders):
  // the replay settles instantly and onChanged() refreshes the catalog.
  useEffect(() => {
    if (!renders) return
    const saved = loadIngestJob()
    if (!saved || saved.kind !== 'ingest' || saved.datasetId !== meta.id) return
    const handle = resumeIngestJob(saved.jobId, meta.id, setProgress, () =>
      setLastPulseAt(Date.now()),
    )
    void track(handle)
    return () => handle.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta.id, renders])

  async function track(handle: IngestJobHandle) {
    saveIngestJob({ jobId: handle.jobId, datasetId: meta.id, kind: 'ingest' })
    setJob(handle)
    setBusy(true)
    setErr(null)
    setCancelled(false)
    try {
      await handle.result
      setFiles([])
      // promoted→ingested (or another staged version): refresh so the re-promote
      // gate (PromoteControl) appears with the new staged version.
      onChanged()
    } catch (e) {
      if (e instanceof IngestCancelledError) {
        setCancelled(true)
        setProgress(null)
      } else if (e instanceof StaleIngestJobError) {
        setProgress(null)
      } else {
        setErr(e)
      }
    } finally {
      clearIngestJob(handle.jobId)
      setJob(null)
      setBusy(false)
    }
  }

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
  const sourceLabel = isJson ? 'JSON' : t('gallery:sourceKind.tabular')
  // 「差し替える」 means "with THIS file". Letting it run on the saved source when
  // no file is picked silently re-ingested the same data — the same button for
  // two different intentions. Rebuilding from the saved file (what you do after
  // revising the design) is its own action below.
  const canReingest = !busy && files.length > 0

  async function run(upload: File[]) {
    setBusy(true)
    setErr(null)
    setProgress(null)
    setCancelled(false)
    try {
      // Empty upload → the server reuses the persisted design-time source.
      const handle = await startIngestJob(meta.id, upload, setProgress, () =>
        setLastPulseAt(Date.now()),
      )
      await track(handle)
    } catch (e) {
      setErr(e)
      setBusy(false)
    }
  }

  return (
    <div className={embedded ? '' : 'ingest-gate'} ref={rootRef}>
      <div className="ds-subhead">{t('gallery:reingest.head')}</div>
      {/* What happens to the reader's answers, not what happens to the version
          graph: the version numbers and the old-version cleanup were the whole
          of this paragraph and none of the decision. */}
      <p className="ingest-note">
        {published
          ? t('gallery:reingest.notePublished')
          : t('gallery:reingest.noteUnpublished')}
      </p>
      {hasSource ? (
        <p className="ingest-source">
          {t('gallery:reingest.sourceSaved', {
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
            accept={isJson ? '.json,.geojson' : TABULAR_ACCEPT}
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
      <button
        type="button"
        className="promote-btn"
        onClick={() => run(files)}
        disabled={!canReingest}
      >
        {busy ? t('gallery:reingest.submitting') : t('gallery:reingest.submit')}
      </button>
      {!busy && files.length === 0 && <p className="ingest-hint">{t('gallery:reingest.needFile')}</p>}
      {hasSource && (
        <>
          <p className="ingest-hint">{t('gallery:reingest.rerunNote')}</p>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => run([])}
            disabled={busy}
          >
            {t('gallery:reingest.rerunSaved')}
          </button>
        </>
      )}
      {busy && (
        <>
          <IngestProgressView
            progress={progress}
            onCancel={job ? job.cancel : undefined}
            lastPulseAt={lastPulseAt}
          />
          <p className="ingest-hint">{t('gallery:ingest.keepGoing')}</p>
        </>
      )}
      {cancelled && <p className="ingest-hint">{t('gallery:ingest.cancelled')}</p>}
      {err != null && (
        <IngestError
          err={err}
          errorKey="gallery:reingest.error"
          meta={meta}
          onRedesign={onRedesign}
          onAddData={onAddData}
          labels={labels}
        />
      )}
    </div>
  )
}

