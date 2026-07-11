import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type CatalogDataset, getCatalogDatasets, getGraphStats } from './galleryApi'
import { AddIcon, ArrowIcon, AskIcon, ChevronIcon, LayersIcon } from './icons'

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
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    let cancelled = false
    getCatalogDatasets()
      .then((d) => !cancelled && setDatasets(d))
      // 障害を「まだデータセットがありません」という誤った空状態にしない
      .catch(() => {
        if (!cancelled) {
          setDatasets([])
          setLoadFailed(true)
        }
      })
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
        <div className="home-band-head">{t('home:band.head')}</div>
        <div className="home-stats">
          <Stat value={fmt(stats?.facts)} label={t('home:stat.facts')} tone="entity" />
          <Stat value={stats ? String(stats.datasets) : '—'} label={t('home:stat.datasets')} />
          <Stat value={fmt(stats?.classes)} label={t('home:stat.classes')} tone="primary" />
        </div>
        {/* SPARQL 統計だけ取れない配備（書き込みトークン未設定/raw SPARQL 非公開）で
            「—」を黙って出すと故障に見える。原因への手がかりを一言添える。 */}
        {stats && stats.facts == null && stats.classes == null && (
          <p className="home-stats-note">{t('home:stat.unavailable')}</p>
        )}
      </section>

      <div className="home-actions">
        <button type="button" className="home-action home-action--primary" onClick={() => onNavigate('workbench')}>
          <span className="home-action-icon">
            <AddIcon size={21} />
          </span>
          <span className="home-action-body">
            <span className="home-action-title">{t('home:action.add.title')}</span>
            <span className="home-action-sub">{t('home:action.add.sub')}</span>
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
            <span className="home-action-title">{t('home:action.ask.title')}</span>
            <span className="home-action-sub">{t('home:action.ask.sub')}</span>
          </span>
          <span className="home-action-arrow">
            <ArrowIcon size={18} />
          </span>
        </button>
      </div>

      <section className="home-recent card">
        <div className="home-recent-head">
          <h3 className="card-h">{t('home:recent.head')}</h3>
          <button type="button" className="link-btn" onClick={() => onNavigate('gallery')}>
            {t('home:recent.seeAll')} <ArrowIcon size={14} />
          </button>
        </div>
        {!datasets && (
          <p className="loading-row">
            <span className="spinner" />
            {t('home:recent.loading')}
          </p>
        )}
        {datasets && recent.length === 0 && (
          <p className="ds-empty-note">
            {loadFailed ? t('home:recent.loadFailed') : t('home:recent.empty')}
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

function Stat({ value, label, tone }: { value: string; label: string; tone?: 'primary' | 'entity' }) {
  return (
    <div className="home-stat">
      <span className={`home-stat-value${tone ? ` home-stat-value--${tone}` : ''}`}>{value}</span>
      <span className="home-stat-label">{label}</span>
    </div>
  )
}

function DatasetRow({ dataset, onOpen }: { dataset: CatalogDataset; onOpen: () => void }) {
  const { t } = useTranslation()
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
      <span className={`status-pill status-pill--${dataset.statusKind}`}>{t(`home:status.${dataset.statusKind}`)}</span>
      <span className="ds-row-chevron">
        <ChevronIcon size={16} />
      </span>
    </button>
  )
}
