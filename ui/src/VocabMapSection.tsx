// 「共通のことば」ページの育つ地図の**節**（データ取得＋統計帯＋図＋凡例）。
//
// データ源はすべて決定論・読み取り専用（shared-vocab-graph.md §3）:
//   ・各データセットの保存済み取り込みルール（⑤と同じ）
//   ・公開グラフの種類ごとの件数（データセット単位は GET /api/kinds/counts。
//     取れないときだけ親の getSchema() の全体の件数に落ちる）
//   ・接地候補は POST /api/ground/terms（exact 級のみ・1 往復）
//   ・対応は crosswalk の alignment グラフ
//   ・「全体」表示だけ、つながり（ハブ）の一覧 GET /api/crosswalks と、ハブの件数
// どれかが取れなくても図は残りで描く（欠けは「線が無い」だけ — 嘘は描かない）。
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Alignment, CrosswalkPerspective } from './crosswalkApi'
import { getAlignments, getCrosswalks } from './crosswalkApi'
import type { CatalogDataset, DatasetRules } from './galleryApi'
import { getDatasetRules, getKindCounts } from './galleryApi'
import type { GroundCandidate } from './groundingApi'
import { groundTermsBatch } from './groundingApi'
import type { SchemaSummary } from './demoApi'
import { KindOverview } from './KindOverviewMap'
import { hubCountsOf, layoutKindOverview } from './kindOverview'
import { VocabMap } from './VocabMap'
import {
  collectMintedTermQueries,
  collectStandardIris,
  classCountsByCatalogId,
  composeVocabGraph,
  datasetApiId,
} from './vocabGraph'

interface Loaded {
  /** `apiId` は登録 id（つながりの参加者と突き合わせる）。 */
  datasets: { id: string; apiId: string; name: string; rules: DatasetRules }[]
  alignments: Alignment[]
  candidates: Record<string, GroundCandidate[]>
  standardNames: Record<string, string>
  /** データセット単位の件数（節の id → 種類 IRI → 件数）。取れなかったときは null で、
   *  地図は全体の件数（`schema`）に落ちる。 */
  countsByDataset: Record<string, Record<string, number>> | null
  /** ハブの種類 IRI → 件数（「全体」表示）。取れなければ空 — ハブは最小の丸で出る。 */
  hubCounts: Record<string, number>
  /** つながり（ハブ）の一覧（「全体」表示）。取れなければ空 — ハブを描かないだけ。 */
  crosswalks: CrosswalkPerspective[]
  /** 取り込みルールを読みに行ったデータセットの数。0 件なら地図は出さない、
   *  1 件以上あって 1 つも読めなかったならその事実を出す（黙って消えない）。 */
  attempted: number
}

type MapView = 'overview' | 'detail'
const VIEW_KEY = 'asterism.vocabMapView'

/** 選んだ表示を覚える。既定は「全体」（全体 → 詳細の順で見せる）。保存できなくても動く。 */
function readView(): MapView {
  try {
    return window.localStorage.getItem(VIEW_KEY) === 'detail' ? 'detail' : 'overview'
  } catch {
    return 'overview'
  }
}

export function VocabMapSection({
  datasets,
  schema,
  onOpenDataset,
  onOpenKind,
  onOpenCrosswalk,
}: {
  datasets: CatalogDataset[]
  schema: SchemaSummary | null
  onOpenDataset: (datasetId: string) => void
  /** 「全体」の丸を押したときの行き先（種類のページ）。 */
  onOpenKind?: (classIri: string) => void
  /** 「全体」のハブを押したときの行き先（つながりの画面）。 */
  onOpenCrosswalk?: () => void
}) {
  const { t } = useTranslation()
  const [view, setView] = useState<MapView>(readView)
  const chooseView = (v: MapView) => {
    setView(v)
    try {
      window.localStorage.setItem(VIEW_KEY, v)
    } catch {
      /* 覚えられないだけ */
    }
  }
  const [loaded, setLoaded] = useState<Loaded | null>(null)

  useEffect(() => {
    let cancelled = false
    // 図に出るのは実体のあるデータセットだけ（crosswalk ハブは仕組みなので除く）。
    // 0 件でも同じ非同期経路で流す — effect 内の同期 setState は禁止（連鎖描画）。
    const targets = datasets.filter((d) => d.live && !d.isCrosswalk)
    ;(async () => {
      const withRules = (
        await Promise.all(
          targets.map(async (d) => {
            try {
              // API は登録 id を取る。カタログの `d.id` は `live-…` の表示用で、
              // そのまま投げると 404 → 地図が丸ごと消える（datasetApiId 参照）。
              // 節の id は表示用のまま（画面遷移がそれで動く）。
              const rules = await getDatasetRules(datasetApiId(d))
              return rules.maps.length > 0
                ? { id: d.id, apiId: datasetApiId(d), name: d.name, rules }
                : null
            } catch {
              return null // 設計前のデータセットに取り込みルールは無い — 図から抜くだけ
            }
          }),
        )
      ).filter((d): d is Loaded['datasets'][number] => d !== null)
      // 対応の両端の語も名前を引くので、対応を先に読む（接地は 1 往復のまま）。
      const alignments = await getAlignments()
        .then((r) => r.alignments)
        .catch(() => [] as Alignment[])
      const grounded = await groundTermsBatch(
        collectMintedTermQueries(withRules),
        collectStandardIris(withRules, alignments),
      ).catch(() => ({ terms: {} as Record<string, GroundCandidate[]>, names: {} }))
      const counts = await getKindCounts().catch(() => null)
      const countsByDataset = counts ? classCountsByCatalogId(counts, targets) : null
      const hubCounts = counts ? hubCountsOf(counts) : {}
      const crosswalks = await getCrosswalks().catch(() => [] as CrosswalkPerspective[])
      if (!cancelled)
        setLoaded({
          datasets: withRules,
          alignments,
          candidates: grounded.terms,
          standardNames: grounded.names,
          countsByDataset,
          hubCounts,
          crosswalks,
          attempted: targets.length,
        })
    })()
    return () => {
      cancelled = true
    }
  }, [datasets])

  const classCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const c of schema?.classes ?? []) m[c.iri] = c.count
    return m
  }, [schema])

  const shape = useMemo(() => {
    if (!loaded || loaded.datasets.length === 0) return null
    return composeVocabGraph({
      datasets: loaded.datasets,
      classCounts,
      classCountsByDataset: loaded.countsByDataset ?? undefined,
      candidates: loaded.candidates,
      standardNames: loaded.standardNames,
      alignments: loaded.alignments,
      words: {
        more: (n) => t('vocab:map.moreFields', { n }),
        count: (n) => t('vocab:map.count', { n }),
        aligned: t('vocab:map.aligned'),
      },
    })
  }, [loaded, classCounts, t])

  const overview = useMemo(() => {
    if (!loaded || loaded.datasets.length === 0) return null
    return layoutKindOverview({
      datasets: loaded.datasets,
      classCounts,
      classCountsByDataset: loaded.countsByDataset ?? undefined,
      crosswalks: loaded.crosswalks,
      hubCounts: loaded.hubCounts,
      standardNames: loaded.standardNames,
      alignments: loaded.alignments,
      unnamedHub: t('vocab:overview.unnamedHub'),
    })
  }, [loaded, classCounts, t])

  // ⭐取れなかったときに**黙って消えない**。設計のあるデータセットが 1 つも
  // 読めなかったのに節ごと消すと、画面から機能が丸ごと無くなったように見える
  // （実際そう見えていた — 利用者報告 2026-09-03「グラフがないのですが」）。
  // データセット自体が無いときだけ、従来どおり何も出さない。
  if (!shape || shape.nodes.length === 0) {
    if (!loaded || loaded.attempted === 0) return null
    return (
      <div className="card vocab-map-card">
        <div className="vocab-card-head">
          <h3 className="card-h">{t('vocab:map.title')}</h3>
        </div>
        <p className="kz-note kz-prose">{t('vocab:map.unavailable')}</p>
      </div>
    )
  }

  const showOverview = view === 'overview' && overview !== null
  const legend = showOverview
    ? ([
        { kind: 'link', dashed: false, text: t('vocab:overview.legend.link') },
        { kind: 'hub', dashed: false, text: t('vocab:overview.legend.hub') },
        { kind: 'candidate', dashed: true, text: t('vocab:overview.legend.standard') },
        { kind: 'alignment', dashed: true, text: t('vocab:overview.legend.alignment') },
      ] as const)
    : ([
        { kind: 'link', dashed: false, text: t('vocab:map.legend.link') },
        { kind: 'used', dashed: false, text: t('vocab:map.legend.used') },
        { kind: 'candidate', dashed: true, text: t('vocab:map.legend.candidate') },
        { kind: 'alignment', dashed: true, text: t('vocab:map.legend.alignment') },
      ] as const)

  return (
    <div className="card vocab-map-card">
      <div className="vocab-card-head">
        <h3 className="card-h">{t('vocab:map.title')}</h3>
      </div>
      <div className="vocab-map-toggle" role="group" aria-label={t('vocab:overview.toggleAria')}>
        {(['overview', 'detail'] as const).map((v) => (
          <button
            key={v}
            type="button"
            className={view === v ? 'vocab-map-toggle-btn is-on' : 'vocab-map-toggle-btn'}
            aria-pressed={view === v}
            onClick={() => chooseView(v)}
          >
            {t(v === 'overview' ? 'vocab:overview.all' : 'vocab:overview.detail')}
          </button>
        ))}
      </div>
      <p className="vocab-map-lead">{showOverview ? t('vocab:overview.lead') : t('vocab:map.lead')}</p>
      <div className="vocab-map-stats" aria-label={t('vocab:map.statsAria')}>
        <span>
          <strong>{shape.stats.datasets}</strong> {t('vocab:map.stats.datasets')}
        </span>
        <span>
          <strong>{shape.stats.kinds}</strong> {t('vocab:map.stats.kinds')}
        </span>
        <span>
          <strong>{shape.stats.items}</strong> {t('vocab:map.stats.items')}
        </span>
        <span>
          <strong>{shape.stats.used}</strong> {t('vocab:map.stats.used')}
        </span>
        <span>
          <strong>{shape.stats.candidates}</strong> {t('vocab:map.stats.candidates')}
        </span>
        <span>
          <strong>{shape.stats.alignments}</strong> {t('vocab:map.stats.alignments')}
        </span>
      </div>
      {showOverview ? (
        <KindOverview
          layout={overview}
          ariaLabel={t('vocab:overview.aria')}
          onOpenKind={onOpenKind}
          onOpenDataset={onOpenDataset}
          onOpenCrosswalk={onOpenCrosswalk}
        />
      ) : (
        <VocabMap shape={shape} ariaLabel={t('vocab:map.aria')} onOpenDataset={onOpenDataset} />
      )}
      <div className="vocab-map-legend">
        {legend.map((l) => (
          <span key={l.kind} className="vocab-map-leg">
            <svg width="30" height="10" aria-hidden>
              <line
                x1="1"
                y1="5"
                x2="29"
                y2="5"
                strokeWidth="2"
                strokeDasharray={l.dashed ? '5 4' : undefined}
                className={`vocab-map-leg-line vocab-map-leg-line--${l.kind}`}
              />
            </svg>
            {l.text}
          </span>
        ))}
      </div>
    </div>
  )
}
