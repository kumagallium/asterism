import { useEffect, useState } from 'react'
import { ingestDataset, type IngestProgress, type IngestResult } from './api'
import { getSchema } from './demoApi'
import {
  type AlignmentReport,
  type CatalogDataset,
  type CatalogStatusKind,
  datasetStage,
  deleteDataset,
  getAlignment,
  getCatalogDatasets,
  type LiveDataset,
  promoteDataset,
  reinstateDataset,
  retractDataset,
} from './galleryApi'
import { ArrowIcon, LinkIcon, SearchIcon } from './icons'
import { IngestProgressView } from './IngestProgressView'
import { Mermaid } from './Mermaid'
import { localName } from './vocab'

const STATUS_LABEL: Record<CatalogStatusKind, string> = {
  pub: '公開済み',
  draft: '下書き',
  design: '設計中',
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
}: {
  focusClass?: string | null
  onOpenVocab?: () => void
}) {
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  const [error, setError] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [seenFocus, setSeenFocus] = useState<string | null | undefined>(focusClass)
  const [tab, setTab] = useState<'design' | 'rules'>('design')
  // Live count of shared classes actually in the store (for the gateway band).
  // null = unavailable (query layer down) → the band omits the number.
  const [sharedClassCount, setSharedClassCount] = useState<number | null>(null)

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

  const list = datasets ?? []
  const focused = focusClass ? list.find((d) => d.classes.includes(focusClass)) : undefined
  const selected = list.find((d) => d.id === picked) ?? focused ?? list[0] ?? null

  return (
    <div className="catalog">
      <p className="catalog-intro">
        作った<strong>データセット</strong>が主役です。各データセットは「<strong>設計図（語彙）</strong>」と
        「<strong>取り込みルール</strong>」を持ちます。共通で使う語彙は下にまとめています。
      </p>

      {focusClass && (
        <div className="vocab-focus-banner">
          Ask の引用に対応する語彙クラス：<strong>{focusClass}</strong>
          <span className="vocab-focus-sub">この回答はこのクラスで型付けされたデータに基づきます。</span>
        </div>
      )}

      {error && <pre className="error">{error}</pre>}

      {!datasets && !error && (
        <p className="loading-row">
          <span className="spinner" />
          カタログを読み込み中…
        </p>
      )}

      {datasets && datasets.length === 0 && (
        <div className="state-block">
          <span className="state-icon state-icon--primary">
            <SearchIcon size={26} />
          </span>
          <p className="state-title">まだデータセットがありません</p>
          <p className="state-sub">「データを追加」でデータを取り込むと、ここに並びます。</p>
        </div>
      )}

      {datasets && datasets.length > 0 && (
        <div className="catalog-grid">
          <div className="catalog-list">
            <div className="catalog-list-head">
              <h3 className="card-h">データセット</h3>
              <span className="catalog-count">{datasets.length}</span>
            </div>
            {datasets.map((d) => (
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
            共有の語彙 <span className="shared-band-en">shared vocabulary</span>
            <span className="shared-band-warn">変更は全体に影響 · 要注意</span>
          </span>
          <span className="shared-band-sub">
            複数のデータセットが共通で使う設計図。揃えておくと<strong>横断して検索・比較</strong>できます。
          </span>
        </span>
        <span className="shared-band-cta">
          {sharedClassCount != null && (
            <span className="shared-band-users">
              <span className="mono-strong">{sharedClassCount}</span> クラスを共有
            </span>
          )}
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
  return (
    <button type="button" className={`ds-card${active ? ' active' : ''}`} onClick={onSelect}>
      <div className="ds-card-head">
        <span className="ds-card-name">{dataset.name}</span>
        <span className={`status-pill status-pill--${dataset.statusKind}`}>
          {STATUS_LABEL[dataset.statusKind]}
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
  tab: 'design' | 'rules'
  onTab: (t: 'design' | 'rules') => void
  highlight?: string | null
  onChanged: () => void
}) {
  return (
    <div className="ds-detail card">
      <div className="ds-detail-head">
        <h2 className="ds-detail-name">{dataset.name}</h2>
        <span className={`status-pill status-pill--${dataset.statusKind}`}>
          {STATUS_LABEL[dataset.statusKind]}
        </span>
        <div className="ds-tabs">
          <button
            type="button"
            className={`ds-tab${tab === 'design' ? ' active' : ''}`}
            onClick={() => onTab('design')}
          >
            設計図 <span className="ds-tab-en">ontology</span>
          </button>
          <button
            type="button"
            className={`ds-tab${tab === 'rules' ? ' active' : ''}`}
            onClick={() => onTab('rules')}
          >
            取り込みルール <span className="ds-tab-en">mapping</span>
          </button>
        </div>
      </div>

      {dataset.purposes.length > 0 && (
        <div className="ds-purposes">
          <div className="ds-purposes-label">
            <SearchIcon size={13} /> このデータが答えられる問い
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

      {tab === 'design' ? (
        <div className="ds-tab-body">
          <div className="ds-section-head">
            <span className="ds-section-title">設計図（中身の構造）</span>
            <span className="ds-section-note">{dataset.classes.length} クラス</span>
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
            <p className="ds-empty-note">クラス情報はありません。</p>
          )}

          {dataset.predicates.length > 0 && (
            <>
              <div className="ds-subhead">使っている述語（実データの語彙）</div>
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
              <summary>クラス図を見る</summary>
              <div className="onto-diagram">
                <Mermaid chart={dataset.mermaid} />
              </div>
            </details>
          )}

          {dataset.reuses.length > 0 && (
            <>
              <div className="ds-subhead">他から借りている語彙（実データの名前空間から検出）</div>
              <div className="ds-reuse-list">
                {dataset.reuses.map((r) => (
                  <span key={r.prefix} className="reuse-chip" title={r.what}>
                    <code>{r.prefix}</code>
                    <span className="reuse-chip-what">{r.what}</span>
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="ds-tab-body">
          <div className="ds-section-head">
            <span className="ds-section-title">取り込みルール（生成物）</span>
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
            <p className="ds-empty-note">取り込みルールの生成物はまだありません。</p>
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
        <div className="ds-subhead">取り込み（Oxigraph へ投入）</div>
        <p className="ingest-hint">
          この設計には宣言 RML マッピングが無いため取り込めません。ワークベンチの「AI が設計」で
          §RML（宣言マッピング）を出すと、ここから安全に投入できるようになります。
        </p>
      </div>
    )
  }

  if (done) {
    return (
      <div className="ingest-gate">
        <div className="ds-subhead">取り込み（Oxigraph へ投入）</div>
        <p className="ingest-ok">
          ✓ 下書きグラフに取り込みました（{done.triple_count} 件）。次に下の「共有データに昇格」を押すと
          Ask が引用できます。
        </p>
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
      <div className="ds-subhead">取り込み（Oxigraph へ投入）</div>
      <p className="ingest-note">
        承認すると、この宣言 RML を Morph-KGC が実行し（生成コードは走らず、検証済みの Tier 0
        関数だけ）、結果を<strong>隔離された下書きグラフ</strong>に投入します。Ask
        の引用面（canonical）は汚しません。
      </p>
      {hasSource ? (
        <p className="ingest-source">
          設計時の{sourceLabel}を保存済み
          {meta.source_files?.length ? `（${meta.source_files.join('、')}）` : ''}
          。再添付なしで取り込めます。
        </p>
      ) : (
        <div className="ingest-pick">
          <label className="file-btn">
            {sourceLabel}を選択
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
              : `設計に使った${sourceLabel}を選んでください`}
          </span>
        </div>
      )}
      <button type="button" className="promote-btn" onClick={onIngest} disabled={!canIngest}>
        {busy ? '取り込み中…' : '取り込み（Oxigraph へ投入）'}
      </button>
      {busy && <IngestProgressView progress={progress} />}
      {err && <p className="promote-err">取り込みに失敗しました: {err}</p>}
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
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [alignment, setAlignment] = useState<AlignmentReport | null>(null)
  const version = meta.version ?? 0

  if (!meta.ingested) {
    if (meta.promoted) {
      return (
        <p className="promote-ok">
          ✓ 共有データに昇格済み（{meta.triples_promoted ?? 0} 件{version ? `・版 v${version}` : ''}）。Ask
          が引用できます（正式グラフ＝canonical）。
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
          新しい版を下書きグラフに取り込み済みです。「共有データに反映（再昇格）」を押すと、Ask が引用する版が{' '}
          <strong>v{version} → v{version + 1}</strong>{' '}
          に上がり、新しいデータに切り替わります。切り替えは一瞬で、旧版は自動で片付けられます（取り込み中も今の版が引用され続けるので途切れません）。
        </p>
      ) : (
        <p className="promote-note">
          「共有データに昇格」すると、下書きグラフのこのデータが <strong>Ask が引用する正式グラフ
          （canonical）</strong> に移ります。昇格前に、使っている語彙が既存の再利用か新規かを確認できます。
        </p>
      )}
      {alignment ? (
        <div className="alignment-summary">
          <span>
            述語: 既存の再利用 {alignment.predicates.reuse.length} / 新規 {alignment.predicates.new.length}{' '}
            ／ クラス: 既存の再利用 {alignment.classes.reuse.length} / 新規 {alignment.classes.new.length}
          </span>
          {alignment.predicates.new.length > 0 && (
            <p className="alignment-new">
              新規の述語（既存語彙に無い）: {alignment.predicates.new.map(shortIri).join('、')}
            </p>
          )}
        </div>
      ) : (
        <button type="button" className="btn btn--ghost btn--sm" onClick={preview}>
          語彙の差分を確認（{isRepromote ? '再昇格' : '昇格'}前チェック）
        </button>
      )}
      <button type="button" className="promote-btn" onClick={promote} disabled={busy}>
        {busy
          ? isRepromote
            ? '反映中…'
            : '昇格中…'
          : isRepromote
            ? `共有データに反映（再昇格 → v${version + 1}）`
            : '共有データに昇格（Ask で使えるように）'}
      </button>
      {err && (
        <p className="promote-err">
          {isRepromote ? '再昇格' : '昇格'}に失敗しました: {err}
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
      <div className="ds-subhead">データを更新（再取り込み）</div>
      <p className="ingest-note">
        新しい CSV を<strong>別バージョンのグラフ</strong>（canonical/{shortIri(meta.id)}/v…）に取り込みます。
        {published ? (
          <>
            取り込み中も<strong>今の公開版（v{version}）が Ask に引用され続ける</strong>ので、回答が途切れません。
            完了したら下の「共有データに反映（再昇格）」で <strong>v{version} → v{version + 1}</strong>{' '}
            に切り替わり、旧版は自動で片付けられます。
          </>
        ) : (
          <>
            まだ公開していない下書きを取り直します（Ask には影響しません）。完了したら下の「共有データに昇格」で公開できます。
          </>
        )}
      </p>
      {hasSource ? (
        <p className="ingest-source">
          設計時の CSV を保存済み
          {meta.source_files?.length ? `（${meta.source_files.join('、')}）` : ''}
          。再添付なしで取り込めます。別の CSV に差し替えたい場合は下で選んでください。
        </p>
      ) : null}
      <div className="ingest-pick">
        <label className="file-btn">
          {hasSource ? 'CSV を差し替え' : 'CSV を選択'}
          <input
            type="file"
            accept=".csv"
            multiple
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>
        <span className={`file-names${files.length ? '' : ' empty'}`}>
          {files.length
            ? files.map((f) => f.name).join('、')
            : hasSource
              ? '差し替えない場合は保存済み CSV を使います'
              : '更新に使う CSV を選んでください'}
        </span>
      </div>
      <button type="button" className="promote-btn" onClick={onReingest} disabled={!canReingest}>
        {busy ? '取り込み中…' : '新しいデータで再取り込み'}
      </button>
      {busy && <IngestProgressView progress={progress} />}
      {err && <p className="promote-err">再取り込みに失敗しました: {err}</p>}
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
      <div className="ds-subhead">ライフサイクル操作</div>
      {retracted && (
        <p className="lifecycle-status">
          状態: <strong>撤回済み</strong>（Ask の引用対象外。データ・IRI は残るので既存の引用は壊れません。復帰できます）
        </p>
      )}
      <div className="lifecycle-actions">
        {stage === 'promoted' && !retracted && (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={!!busy}
            onClick={() =>
              window.confirm(
                'このデータセットを撤回しますか？\nAsk の引用対象から外しますが、データと IRI は残るので既存の引用は壊れません（後で復帰できます）。',
              ) &&
              run('retract', async () => {
                await retractDataset(meta.id)
                return '撤回しました（canonical から除外・引用は維持）。'
              })
            }
          >
            {busy === 'retract' ? '撤回中…' : '撤回（Ask の引用から外す）'}
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
                return '復帰しました（再び Ask の引用対象です）。'
              })
            }
          >
            {busy === 'reinstate' ? '復帰中…' : '復帰（再び引用対象に）'}
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
                ? 'このデータセットを完全に削除しますか？\n昇格済み（引用可能）なので、既存の引用が 404 になる恐れがあります。通常は「撤回」を推奨します。\n\nそれでも削除しますか？'
                : 'このデータセットを削除しますか？（未昇格なので安全に削除できます）',
            )
            if (ok)
              run('delete', async () => {
                await deleteDataset(meta.id, promoted)
                return '削除しました。'
              })
          }}
        >
          {busy === 'delete' ? '削除中…' : '削除'}
        </button>
      </div>
      {msg && <p className="lifecycle-ok">{msg}</p>}
      {err && <p className="lifecycle-err">操作に失敗しました: {err}</p>}
    </div>
  )
}
