import {
  CATALOG_STATS,
  DATASETS,
  isDemoDatasets,
  STATUS_LABEL,
  type DatasetEntry,
} from './datasetsApi'
import { AddIcon, ArrowIcon, AskIcon, ChevronIcon, LayersIcon } from './icons'

/**
 * Home — the orientation screen (design_handoff_asterism_ux #1). Answers "what
 * do I have, and what's the next move" in plain language before any jargon: a
 * status band, two big actions, and the recent datasets. Data is illustrative
 * (badged demo) until a live /api summary endpoint exists.
 */
export function HomeView({ onNavigate }: { onNavigate: (tab: 'workbench' | 'ask' | 'gallery') => void }) {
  return (
    <div className="home">
      <section className="home-band">
        <div className="home-band-head">
          今ある「つながったデータ」
          {isDemoDatasets && <span className="demo-badge">demo データ</span>}
        </div>
        <div className="home-stats">
          {CATALOG_STATS.map((s) => (
            <div className="home-stat" key={s.label}>
              <span className={`home-stat-value home-stat-value--${s.tone ?? 'fg'}`}>{s.value}</span>
              <span className="home-stat-label">{s.label}</span>
            </div>
          ))}
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
        <div className="ds-rows">
          {DATASETS.map((d) => (
            <DatasetRow key={d.id} dataset={d} onOpen={() => onNavigate('gallery')} />
          ))}
        </div>
      </section>
    </div>
  )
}

function DatasetRow({ dataset, onOpen }: { dataset: DatasetEntry; onOpen: () => void }) {
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
      <span className={`status-pill status-pill--${dataset.status}`}>{STATUS_LABEL[dataset.status]}</span>
      <span className="ds-row-chevron">
        <ChevronIcon size={16} />
      </span>
    </button>
  )
}
