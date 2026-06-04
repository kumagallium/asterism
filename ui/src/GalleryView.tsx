import { useEffect, useState } from 'react'
import {
  type AlignmentReport,
  type CatalogDataset,
  type CatalogStatusKind,
  getAlignment,
  getCatalogDatasets,
  type LiveDataset,
  promoteDataset,
} from './galleryApi'
import { ArrowIcon, LinkIcon, SearchIcon } from './icons'
import { Mermaid } from './Mermaid'

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
 * All datasets are REAL: the committed canonical ontology+mapping and the
 * workbench-materialized drafts (getCatalogDatasets). No demo placeholders.
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

  useEffect(() => {
    let cancelled = false
    getCatalogDatasets()
      .then((d) => {
        if (!cancelled) setDatasets(d)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
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
  // The shared vocabulary = the canonical ontology (a dataset with no draft handle).
  const canonical = list.find((d) => !d.live)

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
            <DatasetDetail dataset={selected} tab={tab} onTab={setTab} highlight={focusClass} />
          )}
        </div>
      )}

      {/* shared vocabulary gateway (= the canonical ontology) */}
      {canonical && (
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
              <span className="mono-strong">{canonical.classes.length}</span> クラスを共有
            </span>
            開く <ArrowIcon size={14} />
          </span>
        </button>
      )}
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
