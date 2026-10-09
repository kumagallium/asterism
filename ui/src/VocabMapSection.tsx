// 「ことば」ページの育つ地図の**節**（データ取得＋統計帯＋図＋凡例）。
//
// データ源はすべて決定論・読み取り専用（shared-vocab-graph.md §3）:
//   ・各データセットの保存済み取り込みルール（⑤と同じ）
//   ・公開グラフの種類ごとの件数（データセット単位は GET /api/kinds/counts。
//     取れないときだけ親の getSchema() の全体の件数に落ちる）
//   ・接地候補は POST /api/ground/terms（exact 級のみ・1 往復）
//   ・対応は crosswalk の alignment グラフ
//   ・「全体」表示だけ、つながり（ハブ）の一覧 GET /api/crosswalks と、ハブの件数
//   ・共有のことば（GET /api/vocab/shared）— 標準の帯の上の帯。データセットが 0 件でも描く
//   ・線の起点: 「線を引く」で丸を 2 つ選ぶと LineForm（配置は変えない・選択状態はここが持つ）
// どれかが取れなくても図は残りで描く（欠けは「線が無い」だけ — 嘘は描かない）。
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Alignment, CrosswalkPerspective } from './crosswalkApi'
import { getAlignments, getCrosswalks } from './crosswalkApi'
import type { CatalogDataset, DatasetRules } from './galleryApi'
import { getDatasetRules, getKindCounts } from './galleryApi'
import type { GroundCandidate } from './groundingApi'
import { groundTermsBatch } from './groundingApi'
import type { SchemaSummary } from './demoApi'
import { LineForm } from './LineForm'
import type { PickEnd } from './lineChoice'
import type { SharedTerm } from './vocabApi'
import { listTerms } from './vocabApi'
import { KindOverview } from './KindOverviewMap'
import { hubCountsOf, layoutKindOverview, overviewStats } from './kindOverview'
import {
  chooseOverviewLevel,
  focusOverview,
  layoutDatasetOverview,
  type OverviewFocus,
} from './kindOverviewScale'
import { VocabMap, type VocabPick } from './VocabMap'
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
  /** 鋳造済みの共有のことば。取れなければ空（帯を描かないだけ）。 */
  sharedTerms: SharedTerm[]
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
  reloadKey = 0,
  onChanged,
}: {
  datasets: CatalogDataset[]
  schema: SchemaSummary | null
  onOpenDataset: (datasetId: string) => void
  /** 「全体」の丸を押したときの行き先（種類のページ）。 */
  onOpenKind?: (classIri: string) => void
  /** 「全体」のハブを押したときの行き先（つながりの画面）。 */
  onOpenCrosswalk?: () => void
  /** 親の版。変わったら（語・問い・線が別の節で変わったとき）地図を取り直す。 */
  reloadKey?: number
  /** 線を引けた（親が版を進め、ほかの節が読み直す）。 */
  onChanged?: () => void
}) {
  const { t } = useTranslation()
  const [view, setView] = useState<MapView>(readView)
  const chooseView = (v: MapView) => {
    setView(v)
    setPicked([]) // 選んだ丸は切り替え先の図に無いことがある
    try {
      window.localStorage.setItem(VIEW_KEY, v)
    } catch {
      /* 覚えられないだけ */
    }
  }
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  // 線を引いたあとの読み直し（地図は読み取りの結果なので、データを取り直して描き直す）。
  const [reloadTick, setReloadTick] = useState(0)

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
      const sharedTerms = await listTerms().catch(() => [] as SharedTerm[])
      if (!cancelled)
        setLoaded({
          datasets: withRules,
          alignments,
          candidates: grounded.terms,
          standardNames: grounded.names,
          countsByDataset,
          hubCounts,
          crosswalks,
          sharedTerms,
          attempted: targets.length,
        })
    })()
    return () => {
      cancelled = true
    }
  }, [datasets, reloadTick, reloadKey])

  const classCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const c of schema?.classes ?? []) m[c.iri] = c.count
    return m
  }, [schema])

  // データセットが 0 件でも、共有のことばがあれば帯は描く。
  const hasContent = !!loaded && (loaded.datasets.length > 0 || loaded.sharedTerms.length > 0)

  const shape = useMemo(() => {
    if (!loaded || !hasContent) return null
    return composeVocabGraph({
      datasets: loaded.datasets,
      classCounts,
      classCountsByDataset: loaded.countsByDataset ?? undefined,
      candidates: loaded.candidates,
      standardNames: loaded.standardNames,
      alignments: loaded.alignments,
      sharedTerms: loaded.sharedTerms,
      words: {
        more: (n) => t('vocab:map.moreFields', { n }),
        count: (n) => t('vocab:map.count', { n }),
        aligned: t('vocab:map.aligned'),
        shared: (kids, orphan) =>
          orphan ? t('vocabmap:sharedBand.orphan') : t('vocabmap:sharedBand.kids', { n: kids }),
      },
    })
  }, [loaded, hasContent, classCounts, t])

  const [focusState, setFocusState] = useState<OverviewFocus | null>(null)

  // ── 線の起点: 丸を 2 つ選ぶ（選択状態はフォーカス状態と同じ置き場） ──
  const [pickMode, setPickMode] = useState(false)
  const [picked, setPicked] = useState<PickEnd[]>([])
  const onPick = useCallback((end: PickEnd) => {
    setPicked((prev) =>
      prev.some((p) => p.id === end.id) ? prev.filter((p) => p.id !== end.id) : [...prev, end].slice(-2),
    )
  }, [])
  const stopPicking = useCallback(() => {
    setPickMode(false)
    setPicked([])
  }, [])
  // 見ている図が変わったら（フォーカス・表示の切り替え）、選んだ丸は図に無いかもしれないので空にする。
  const setFocus = useCallback((f: OverviewFocus | null) => {
    setFocusState(f)
    setPicked([])
  }, [])
  const pick = useMemo<VocabPick>(
    () => ({ active: pickMode, picked: picked.map((p) => p.id), onPick }),
    [pickMode, picked, onPick],
  )
  const cqChoices = useMemo(() => {
    const seen = new Set<string>()
    const out: { tool_name: string; title: string }[] = []
    for (const term of loaded?.sharedTerms ?? []) {
      for (const c of term.cqs ?? []) {
        if (seen.has(c.tool_name)) continue
        seen.add(c.tool_name)
        out.push({ tool_name: c.tool_name, title: c.title })
      }
    }
    return out
  }, [loaded])

  const overviewInput = useMemo(() => {
    if (!loaded || !hasContent) return null
    return {
      datasets: loaded.datasets,
      classCounts,
      classCountsByDataset: loaded.countsByDataset ?? undefined,
      crosswalks: loaded.crosswalks,
      hubCounts: loaded.hubCounts,
      standardNames: loaded.standardNames,
      alignments: loaded.alignments,
      sharedTerms: loaded.sharedTerms,
      unnamedHub: t('vocab:overview.unnamedHub'),
    }
  }, [loaded, hasContent, classCounts, t])

  // 段は自動（小さければ種類まで・超えたらデータセットごと）。丸を押したらその周りだけを種類まで。
  // 押した先が今のデータに無ければ（読み直し後など）フォーカスは無いものとして俯瞰に戻る。
  const focused = useMemo(
    () => (overviewInput && focusState ? focusOverview(overviewInput, focusState) : null),
    [overviewInput, focusState],
  )
  const overviewMode: 'kind' | 'dataset' | 'focus' = focused
    ? 'focus'
    : overviewInput && chooseOverviewLevel(overviewInput.datasets) === 'dataset'
      ? 'dataset'
      : 'kind'
  const overview = useMemo(() => {
    if (!overviewInput) return null
    if (focused) return layoutKindOverview(focused.input)
    return overviewMode === 'dataset' ? layoutDatasetOverview(overviewInput) : layoutKindOverview(overviewInput)
  }, [overviewInput, focused, overviewMode])

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
  const legendBase = showOverview
    ? ([
        ...(overviewMode === 'dataset'
          ? []
          : [{ kind: 'link', dashed: false, text: t('vocab:overview.legend.link') } as const]),
        {
          kind: 'hub',
          dashed: false,
          text: t(overviewMode === 'dataset' ? 'vocab:overview.legend.hubDataset' : 'vocab:overview.legend.hub'),
        } as const,
        ...(overviewMode === 'dataset'
          ? []
          : [{ kind: 'candidate', dashed: false, text: t('vocab:overview.legend.standard') } as const]),
        { kind: 'alignment', dashed: true, text: t('vocab:overview.legend.alignment') } as const,
      ] as const)
    : ([
        { kind: 'link', dashed: false, text: t('vocab:map.legend.link') },
        { kind: 'used', dashed: false, text: t('vocab:map.legend.used') },
        { kind: 'candidate', dashed: true, text: t('vocab:map.legend.candidate') },
        { kind: 'alignment', dashed: true, text: t('vocab:map.legend.alignment') },
      ] as const)

  // 上位への線（共有のことば・⊂）が図にあるときだけ凡例に足す。
  const hasUpper = showOverview
    ? overview.edges.some((e) => e.kind === 'upper')
    : shape.edges.some((e) => e.kind === 'upper')
  const hasShared = showOverview ? (overview.shared?.length ?? 0) > 0 : shape.stats.shared > 0
  const legend: readonly { kind: string; dashed: boolean; text: string }[] = hasUpper
    ? [...legendBase, { kind: 'upper', dashed: false, text: t('vocabmap:legend.upper') }]
    : legendBase

  // 線を引く選択は、丸を選べる図のときだけ（データセットごとの俯瞰は丸がデータセット）。
  const canPick = !(showOverview && overviewMode === 'dataset')

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
      {canPick ? (
        <div className="vocab-map-pickbar">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            aria-pressed={pickMode}
            onClick={() => (pickMode ? stopPicking() : setPickMode(true))}
          >
            {pickMode ? t('vocabmap:pick.stop') : t('vocabmap:pick.start')}
          </button>
          {pickMode && (
            <span className="vocab-map-pickbar-hint">
              {picked.length < 2
                ? `${t('vocabmap:pick.hint')} ${t('vocabmap:pick.count', { n: picked.length })}`
                : t('vocabmap:pick.count', { n: picked.length })}
            </span>
          )}
        </div>
      ) : (
        <p className="vocab-map-pickbar-hint">{t('vocabmap:pick.unavailable')}</p>
      )}
      <p className="vocab-map-lead">
        {showOverview
          ? t(
              overviewMode === 'focus'
                ? 'vocab:overview.leadFocus'
                : overviewMode === 'dataset'
                  ? 'vocab:overview.leadDataset'
                  : 'vocab:overview.lead',
            )
          : t('vocab:map.lead')}
      </p>
      {showOverview && overview ? (
        // 「全体」は図に描いたものだけを数える（項目・接地の候補は「詳しく」の数字）。
        <div className="vocab-map-stats" aria-label={t('vocab:map.statsAria')}>
          {(() => {
            const st = overviewStats(overview)
            return (
              <>
                <span>
                  <strong>{st.datasets}</strong> {t('vocab:map.stats.datasets')}
                </span>
                <span>
                  <strong>{st.kinds}</strong> {t('vocab:map.stats.kinds')}
                </span>
                <span>
                  <strong>{st.hubs}</strong> {t('vocab:overview.stats.hubs')}
                </span>
                {overviewMode !== 'dataset' && (
                  <span>
                    <strong>{st.shared}</strong> {t('vocabmap:stats.shared')}
                  </span>
                )}
                {overviewMode === 'kind' && (
                  <span>
                    <strong>{st.standards}</strong> {t('vocab:overview.stats.standards')}
                  </span>
                )}
                {overviewMode !== 'focus' && (
                  <span>
                    <strong>{st.records.toLocaleString('en-US')}</strong> {t('vocab:overview.stats.records')}
                  </span>
                )}
              </>
            )
          })()}
        </div>
      ) : (
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
          <span>
            <strong>{shape.stats.shared}</strong> {t('vocabmap:stats.shared')}
          </span>
        </div>
      )}
      {showOverview ? (
        <KindOverview
          layout={overview}
          ariaLabel={t('vocab:overview.aria')}
          onOpenKind={onOpenKind}
          onOpenDataset={onOpenDataset}
          onOpenCrosswalk={onOpenCrosswalk}
          onFocus={setFocus}
          pick={canPick ? pick : undefined}
          focus={
            focused
              ? {
                  label: focused.label,
                  onBack: () => setFocus(null),
                  hiddenDatasets: focused.hiddenDatasets,
                  hiddenHubs: focused.hiddenHubs,
                }
              : undefined
          }
        />
      ) : (
        <VocabMap shape={shape} ariaLabel={t('vocab:map.aria')} onOpenDataset={onOpenDataset} pick={pick} />
      )}
      {canPick && pickMode && picked.length === 2 && (
        <LineForm
          // 選び直したら状態（向き・関係・問い・エラー）を初期化する。
          key={picked.map((p) => p.id).join('|')}
          a={picked[0]}
          b={picked[1]}
          cqs={cqChoices}
          onDone={() => {
            stopPicking()
            setReloadTick((n) => n + 1)
            onChanged?.()
          }}
          onCancel={() => setPicked([])}
        />
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
        {hasShared && showOverview && (
          <span className="vocab-map-leg">{t('vocabmap:legend.sharedSize')}</span>
        )}
        {showOverview && (
          <span className="vocab-map-leg">
            <svg width="34" height="16" aria-hidden>
              <circle cx="6" cy="8" r="4" strokeWidth="1" className="vocab-map-leg-circle" />
              <circle cx="22" cy="8" r="7.5" strokeWidth="1" className="vocab-map-leg-circle" />
            </svg>
            {t('vocab:overview.legend.size')}
          </span>
        )}
      </div>
    </div>
  )
}
