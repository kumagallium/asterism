import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type CatalogDataset, getCatalogDatasets, getGraphStats, isAskable } from './galleryApi'
import { AddIcon, ArrowIcon, AskIcon, ChevronIcon, ConnectIcon, LayersIcon, RetryIcon } from './icons'

/** Which pill a row shows. `statusKind` alone cannot say "withdrawn" (the registry
 *  keeps `promoted` set when a dataset is retracted) nor "published, update staged"
 *  (a re-ingest clears `promoted` while the published version stays citable). */
function rowStatusKey(d: CatalogDataset): string {
  if (d.retracted) return 'retracted'
  if (d.updatePending) return 'pubStaged'
  return d.statusKind
}

/** The pill's LOOK. The two extra states say something new in words, but they are
 *  the same two situations the existing tones already carry — published and live
 *  (green) vs. saved but not answering (grey) — so they reuse those classes
 *  instead of leaving `.status-pill--<new>` unstyled, which would strip the pill
 *  of its shape. A dedicated tone can be added later without touching this file. */
function rowStatusTone(key: string): string {
  if (key === 'pubStaged') return 'pub'
  if (key === 'retracted') return 'design'
  return key
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
export function HomeView({
  onNavigate,
  onOpenDataset,
  onCreateCrosswalk,
}: {
  onNavigate: (tab: 'workbench' | 'ask' | 'gallery') => void
  /** 最近の行から当該データセットの詳細へ直行（従来は一覧に飛ぶだけで探し直しだった） */
  onOpenDataset?: (id: string) => void
  /** つながりを作る導線。公開ずみが 2 件以上あるときだけ出す（1 件では作れない
   *  ので、出しても行き止まりになる）。 */
  onCreateCrosswalk?: () => void
}) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [stats, setStats] = useState<Stats | null>(null)

  // 一覧の取得だけは押し直せる必要がある（デスクトップ版は起動直後、ローカルサーバが
  // まだ上がっていないだけで失敗する＝待てば直る一時状態）。
  const loadDatasets = useCallback(
    () =>
      getCatalogDatasets()
        .then((d) => {
          setDatasets(d)
          setLoadFailed(false)
        })
        // 障害を「まだデータセットがありません」という誤った空状態にしない
        .catch(() => {
          setDatasets([])
          setLoadFailed(true)
        }),
    [],
  )

  useEffect(() => {
    void loadDatasets()
  }, [loadDatasets])

  function retryLoad() {
    setDatasets(null)
    void loadDatasets()
  }

  useEffect(() => {
    let cancelled = false
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
  // Connectable datasets: published, and not a connection itself.
  const publishedCount = (datasets ?? []).filter(isAskable).length
  // Nothing there yet (and we know it — a failed fetch is not an empty account):
  // the stats band would be "— / 0 / —", which says nothing about what to do.
  const firstRun = !!datasets && !loadFailed && datasets.length === 0
  const recipeFlow = ['1', '2', '3', '4', '5']
    .map((n, i) => `${'①②③④⑤'[i]} ${t(`kantan:recipe.step${n}`)}`)
    .join(' → ')

  return (
    <div className="home">
      {firstRun ? (
        <section className="home-band">
          <div className="home-band-head">{t('home:firstRun.title')}</div>
          <p className="home-first-run">
            {t('home:firstRun.lead')} {t('home:firstRun.steps', { flow: recipeFlow })}
          </p>
          <p className="home-stats-note">{t('home:firstRun.privacy')}</p>
        </section>
      ) : (
        <section className="home-band">
          <div className="home-band-head">{t('home:band.head')}</div>
          <div className="home-stats">
            <Stat
              value={fmt(stats?.facts)}
              label={t('home:stat.facts')}
              title={t('home:stat.factsTitle')}
              tone="entity"
            />
            <Stat value={stats ? String(stats.datasets) : '—'} label={t('home:stat.datasets')} />
            <Stat value={fmt(stats?.classes)} label={t('home:stat.classes')} tone="primary" />
          </div>
          {/* SPARQL 統計だけ取れない配備（書き込みトークン未設定/raw SPARQL 非公開）で
              「—」を黙って出すと故障に見える。本文は「使い続けて大丈夫」と次の一手
              だけにし、管理者にしか意味のない設定名は title に退避する。 */}
          {stats && stats.facts == null && stats.classes == null && (
            <p className="home-stats-note" title={t('home:stat.unavailableTitle')}>
              {t('home:stat.unavailable')}
            </p>
          )}
        </section>
      )}

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
            {/* 押せるままにする（存在を見せた方が到達目標が伝わる）。ただし公開が 0 件
                なら、押した先で行き止まりになる理由をここで言う。 */}
            <span className="home-action-sub">
              {datasets && !loadFailed && publishedCount === 0
                ? t('home:action.ask.subNone')
                : t('home:action.ask.sub')}
            </span>
          </span>
          <span className="home-action-arrow">
            <ArrowIcon size={18} />
          </span>
        </button>
        {/* Only once there is something to connect — offering it with one published
            dataset would lead straight to a dead end. */}
        {onCreateCrosswalk && publishedCount >= 2 && (
          <button type="button" className="home-action" onClick={onCreateCrosswalk}>
            <span className="home-action-icon">
              <ConnectIcon size={21} />
            </span>
            <span className="home-action-body">
              <span className="home-action-title">{t('home:action.connect.title')}</span>
              <span className="home-action-sub">{t('home:action.connect.sub')}</span>
            </span>
            <span className="home-action-arrow">
              <ArrowIcon size={18} />
            </span>
          </button>
        )}
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
        {datasets && recent.length === 0 && !loadFailed && (
          <p className="ds-empty-note">{t('home:recent.empty')}</p>
        )}
        {/* 「再読み込みしてください」と言うだけで手段が無いのが行き止まりだった。
            文と同じ場所に、もう一度取りに行くボタンを置く。 */}
        {datasets && loadFailed && (
          <p className="ds-empty-note">
            {t('home:recent.loadFailed')}{' '}
            <button type="button" className="link-btn" onClick={retryLoad}>
              <RetryIcon size={14} /> {t('home:recent.retry')}
            </button>
          </p>
        )}
        <div className="ds-rows">
          {recent.map((d) => (
            <DatasetRow
              key={d.id}
              dataset={d}
              onOpen={() => (onOpenDataset ? onOpenDataset(d.id) : onNavigate('gallery'))}
            />
          ))}
        </div>
      </section>
    </div>
  )
}

function Stat({
  value,
  label,
  title,
  tone,
}: {
  value: string
  label: string
  title?: string
  tone?: 'primary' | 'entity'
}) {
  return (
    <div className="home-stat">
      <span className={`home-stat-value${tone ? ` home-stat-value--${tone}` : ''}`}>{value}</span>
      <span className="home-stat-label" title={title}>
        {label}
      </span>
    </div>
  )
}

function DatasetRow({ dataset, onOpen }: { dataset: CatalogDataset; onOpen: () => void }) {
  const { t } = useTranslation()
  const statusKey = rowStatusKey(dataset)
  // ホームは「次の一手」を掲げる画面 — 途中で離れた人に、その行から何をすれば
  // 質問できるようになるかを 1 行で言う（遷移は従来どおり詳細へ）。
  const hint =
    dataset.statusKind === 'draft' && !dataset.updatePending
      ? t('home:rowHint.draft')
      : dataset.statusKind === 'design'
        ? t('home:rowHint.design')
        : ''
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
        {hint && <span className="ds-row-count ds-row-hint">{hint}</span>}
      </span>
      <span className={`status-pill status-pill--${rowStatusTone(statusKey)}`}>
        {t(`home:status.${statusKey}`)}
      </span>
      <span className="ds-row-chevron">
        <ChevronIcon size={16} />
      </span>
    </button>
  )
}
