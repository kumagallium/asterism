import { useEffect, useState } from 'react'
import { type CatalogDataset, getCatalogDatasets, getGraphStats } from './galleryApi'
import { AddIcon, ArrowIcon, AskIcon, ChevronIcon, LayersIcon } from './icons'

const STATUS_LABEL: Record<CatalogDataset['statusKind'], string> = {
  pub: '公開済み',
  draft: '下書き',
  design: '設計中',
}

interface Stats {
  facts: number | null
  classes: number | null
  datasets: number
}

/**
 * Home — the orientation screen (design_handoff_asterism_ux #1). Answers "what
 * do I have, and what's the next move" in plain language. All numbers are REAL:
 * dataset counts from the catalog and triple/class counts measured from the
 * store via SPARQL (shown as "—" when the store is unavailable). No fabricated
 * figures.
 */
export function HomeView({ onNavigate }: { onNavigate: (tab: 'workbench' | 'ask' | 'gallery') => void }) {
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    let cancelled = false
    getCatalogDatasets()
      .then((d) => !cancelled && setDatasets(d))
      .catch(() => !cancelled && setDatasets([]))
    getGraphStats()
      .then((s) => !cancelled && setStats(s))
      .catch(() => !cancelled && setStats({ facts: null, classes: null, datasets: 0 }))
    return () => {
      cancelled = true
    }
  }, [])

  const fmt = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString())
  // The crosswalk hub is a bridge surfaced on its own — keep it out of the dataset
  // list here, matching the Catalog.
  const recent = (datasets ?? []).filter((d) => !d.isCrosswalk).slice(0, 5)

  return (
    <div className="home">
      <section className="home-band">
        <div className="home-band-head">今ある「つながったデータ」</div>
        <div className="home-stats">
          <Stat value={fmt(stats?.facts)} label="事実の数 / triples" />
          <Stat value={stats ? String(stats.datasets) : '—'} label="データセット" />
          <Stat value={fmt(stats?.classes)} label="語彙のクラス" tone="primary" />
        </div>
      </section>

      <div className="home-actions">
        <button type="button" className="home-action home-action--primary" onClick={() => onNavigate('workbench')}>
          <span className="home-action-icon">
            <AddIcon size={21} />
          </span>
          <span className="home-action-body">
            <span className="home-action-title">データを追加</span>
            <span className="home-action-sub">CSV などをつなぐと、AI が設計を下書きします</span>
          </span>
          <span className="home-action-arrow">
            <ArrowIcon size={18} />
          </span>
        </button>
        <button type="button" className="home-action" onClick={() => onNavigate('ask')}>
          <span className="home-action-icon">
            <AskIcon size={21} />
          </span>
          <span className="home-action-body">
            <span className="home-action-title">質問する</span>
            <span className="home-action-sub">取り込んだデータに、根拠つきで答えます</span>
          </span>
          <span className="home-action-arrow">
            <ArrowIcon size={18} />
          </span>
        </button>
      </div>

      <section className="home-recent card">
        <div className="home-recent-head">
          <h3 className="card-h">最近のデータセット</h3>
          <button type="button" className="link-btn" onClick={() => onNavigate('gallery')}>
            カタログで全部見る <ArrowIcon size={14} />
          </button>
        </div>
        {!datasets && (
          <p className="loading-row">
            <span className="spinner" />
            読み込み中…
          </p>
        )}
        {datasets && recent.length === 0 && (
          <p className="ds-empty-note">
            まだデータセットがありません。「データを追加」から始めましょう。
          </p>
        )}
        <div className="ds-rows">
          {recent.map((d) => (
            <DatasetRow key={d.id} dataset={d} onOpen={() => onNavigate('gallery')} />
          ))}
        </div>
      </section>
    </div>
  )
}

function Stat({ value, label, tone }: { value: string; label: string; tone?: 'primary' }) {
  return (
    <div className="home-stat">
      <span className={`home-stat-value home-stat-value--${tone ?? 'fg'}`}>{value}</span>
      <span className="home-stat-label">{label}</span>
    </div>
  )
}

function DatasetRow({ dataset, onOpen }: { dataset: CatalogDataset; onOpen: () => void }) {
  return (
    <button type="button" className="ds-row" onClick={onOpen}>
      <span className="ds-row-icon">
        <LayersIcon size={16} />
      </span>
      <span className="ds-row-name">
        <span className="ds-row-title">{dataset.name}</span>
        <span className="ds-row-sub">{dataset.sub}</span>
      </span>
      <span className="ds-row-counts">
        {dataset.counts.map((c) => (
          <span className="ds-row-count" key={c.label}>
            <span className="ds-row-count-val">{c.value}</span> {c.label}
          </span>
        ))}
      </span>
      <span className={`status-pill status-pill--${dataset.statusKind}`}>{STATUS_LABEL[dataset.statusKind]}</span>
      <span className="ds-row-chevron">
        <ChevronIcon size={16} />
      </span>
    </button>
  )
}
