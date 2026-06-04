import { useEffect, useState } from 'react'
import {
  DATASETS,
  SHARED_VOCAB,
  STATUS_LABEL,
  type DatasetArtifact,
  type DatasetCount,
  type DatasetEntry,
  type DatasetReuse,
  type DatasetRule,
  type DatasetStatusKind,
} from './datasetsApi'
import {
  type AlignmentReport,
  datasetStage,
  getAlignment,
  getLiveDatasets,
  type LiveDataset,
  promoteDataset,
  STARRYDATA_MERMAID,
} from './galleryApi'
import { ArrowIcon, LinkIcon, SearchIcon } from './icons'
import { Mermaid } from './Mermaid'

// A unified view-model so the dataset list + detail render demo fixtures and
// real materialized drafts the same way. Live drafts keep their backend handle
// so the promote (S4 human-gate) flow stays intact.
interface CatalogDataset {
  id: string
  name: string
  sub: string
  statusKind: DatasetStatusKind
  counts: DatasetCount[]
  purposes: string[]
  classes: { ja?: string; en: string }[]
  reuses: DatasetReuse[]
  rules: DatasetRule[]
  artifacts: DatasetArtifact[]
  mermaid?: string
  demo: boolean
  live?: LiveDataset
}

function fromDemo(d: DatasetEntry): CatalogDataset {
  return {
    id: d.id,
    name: d.name,
    sub: d.sub,
    statusKind: d.status,
    counts: d.counts,
    purposes: d.purposes,
    classes: d.classes,
    reuses: d.reuses,
    rules: d.rules,
    artifacts: d.artifacts,
    // The headline dataset shows its real committed TBox diagram.
    mermaid: d.id === 'starrydata' ? STARRYDATA_MERMAID : undefined,
    demo: true,
  }
}

function fromLive(l: LiveDataset): CatalogDataset {
  const stage = datasetStage(l.meta)
  const statusKind: DatasetStatusKind =
    stage === 'promoted' ? 'pub' : stage === 'ingested' ? 'draft' : 'design'
  const n = l.meta.triples_promoted ?? l.meta.triple_count
  return {
    id: l.ontology.id,
    name: l.meta.name,
    sub: `設計を保存 · ${l.meta.created_at.slice(0, 10)}`,
    statusKind,
    counts: [
      { value: n != null ? n.toLocaleString() : '—', label: '事実' },
      { value: String(l.ontology.classes.length), label: 'クラス' },
    ],
    purposes: [],
    classes: l.ontology.classes.map((c) => ({ en: c })),
    reuses: l.ontology.reuses.map((r) => ({ prefix: r.prefix, what: r.what })),
    rules: [],
    artifacts: l.mapping.artifacts.map((a) => ({
      kind: a.kind === 'ingester' ? 'CODE' : a.kind.toUpperCase(),
      name: a.name,
      detail: a.summary,
    })),
    mermaid: l.ontology.mermaid || undefined,
    demo: false,
    live: l,
  }
}

/**
 * Catalog — datasets are the entry point (design_handoff_asterism_ux #5). Each
 * dataset HAS a 設計図 (vocabulary) and 取り込みルール (mapping), shown as two tabs
 * inside the dataset; the SHARED vocabulary is promoted to its own board (the
 * gateway band at the bottom). Demo datasets illustrate the IA (badged); real
 * materialized drafts surface here too and keep their promote (human-gate) flow.
 */
export function GalleryView({
  focusClass,
  onOpenVocab,
}: {
  focusClass?: string | null
  onOpenVocab?: () => void
}) {
  const [live, setLive] = useState<LiveDataset[]>([])
  const [loaded, setLoaded] = useState(false)
  // User's explicit pick (null = follow focus / first). Reset when focus changes.
  const [picked, setPicked] = useState<string | null>(null)
  const [seenFocus, setSeenFocus] = useState<string | null | undefined>(focusClass)
  const [tab, setTab] = useState<'design' | 'rules'>('design')

  useEffect(() => {
    let cancelled = false
    // Best-effort: resolves [] when the workbench API is absent.
    getLiveDatasets()
      .then((l) => {
        if (!cancelled) {
          setLive(l)
          setLoaded(true)
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const datasets: CatalogDataset[] = [...DATASETS.map(fromDemo), ...live.map(fromLive)]

  // Reset the explicit pick when arriving with a new Ask focus (setState during
  // render — the React-recommended "adjust state on prop change" pattern, which
  // avoids a cascading setState-in-effect).
  if (focusClass !== seenFocus) {
    setSeenFocus(focusClass)
    setPicked(null)
  }

  // Selection: user pick → focused class's dataset → first.
  const focused = focusClass
    ? datasets.find((d) => d.classes.some((c) => c.en === focusClass))
    : undefined
  const selected =
    datasets.find((d) => d.id === picked) ?? focused ?? datasets[0] ?? null

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

      <div className="catalog-grid">
        {/* dataset list */}
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
          {!loaded && (
            <p className="loading-row">
              <span className="spinner" />
              読み込み中…
            </p>
          )}
        </div>

        {/* dataset detail */}
        {selected && <DatasetDetail dataset={selected} tab={tab} onTab={setTab} highlight={focusClass} />}
      </div>

      {/* shared vocabulary gateway */}
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
          <span className="shared-band-users">
            <span className="mono-strong">{SHARED_VOCAB.classes.length}</span> クラスを共有
          </span>
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
      <div className="ds-card-sub">
        {dataset.sub}
        {dataset.demo && <span className="ds-card-demo">demo</span>}
      </div>
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
}: {
  dataset: CatalogDataset
  tab: 'design' | 'rules'
  onTab: (t: 'design' | 'rules') => void
  highlight?: string | null
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
              <span key={p} className="purpose-pill">
                {p}
              </span>
            ))}
          </div>
        </div>
      )}

      {tab === 'design' ? (
        <div className="ds-tab-body">
          <div className="ds-section-head">
            <span className="ds-section-title">設計図（中身の構造）</span>
            <span className="ds-section-note">{dataset.classes.length} クラス · すべて出どころ付き</span>
          </div>
          <div className="ds-classes">
            {dataset.classes.map((c) => (
              <span
                key={c.en}
                className={`class-chip${c.en === highlight ? ' onto-class-chip--focus' : ''}`}
              >
                {c.ja && <span className="class-chip-ja">{c.ja}</span>}
                <code className="class-chip-en">{c.en}</code>
              </span>
            ))}
          </div>

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
              <div className="ds-subhead">他から借りている語彙（再発明しない）</div>
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
            <span className="ds-section-title">取り込みルール（項目の対応）</span>
          </div>
          {dataset.rules.length > 0 ? (
            <table className="rule-table">
              <thead>
                <tr>
                  <th>ソース項目</th>
                  <th></th>
                  <th>つなぐ先</th>
                  <th>変換</th>
                </tr>
              </thead>
              <tbody>
                {dataset.rules.map((r) => (
                  <tr key={r.source}>
                    <td className="rule-source">{r.source}</td>
                    <td className="rule-arrow">
                      <ArrowIcon size={13} />
                    </td>
                    <td className="rule-target">{r.target}</td>
                    <td>
                      <span className="rule-convert">{r.convert}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="ds-empty-note">
              この設計の項目対応表はまだありません。生成物（下）を確認してください。
            </p>
          )}

          {dataset.artifacts.length > 0 && (
            <div className="ds-artifacts">
              {dataset.artifacts.map((a) => (
                <div key={a.name} className="ds-artifact">
                  <span className="ds-artifact-kind">{a.kind}</span>
                  <code className="ds-artifact-name">{a.name}</code>
                  <span className="ds-artifact-detail">{a.detail}</span>
                </div>
              ))}
            </div>
          )}

          {/* Real materialized drafts keep the S4 promote human-gate. */}
          {dataset.live && <PromoteControl meta={dataset.live.meta} />}
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
 * The S4 human gate: review the draft's vocabulary alignment (Reuse vs New)
 * against the canonical graph, then promote (MOVE draft → canonical) so Ask can
 * cite it. Only shown for ingested-but-not-yet-promoted drafts.
 */
function PromoteControl({ meta }: { meta: LiveDataset['meta'] }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [alignment, setAlignment] = useState<AlignmentReport | null>(null)
  const [promoted, setPromoted] = useState<number | null>(
    meta.promoted ? (meta.triples_promoted ?? 0) : null,
  )

  if (promoted !== null) {
    return (
      <p className="promote-ok">
        ✓ 共有データに昇格済み（{promoted} 件）。Ask が引用できます（正式グラフ＝canonical）。
      </p>
    )
  }
  if (!meta.ingested) return null

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
      setPromoted((await promoteDataset(meta.id)).triples_promoted)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="promote-control">
      <p className="promote-note">
        「共有データに昇格」すると、下書きグラフのこのデータが <strong>Ask が引用する正式グラフ
        （canonical）</strong> に移ります。昇格前に、使っている語彙が既存の再利用か新規かを確認できます。
      </p>
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
          語彙の差分を確認（昇格前チェック）
        </button>
      )}
      <button type="button" className="promote-btn" onClick={promote} disabled={busy}>
        {busy ? '昇格中…' : '共有データに昇格（Ask で使えるように）'}
      </button>
      {err && <p className="promote-err">昇格に失敗しました: {err}</p>}
    </div>
  )
}
