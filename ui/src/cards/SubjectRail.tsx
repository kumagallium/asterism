import { type FormEvent, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Route } from '../App'
import {
  classSchema,
  listDatasets,
  type CardsDatasetSummary,
  type SetResolveResult,
  type SubjectItem,
  type SubjectSearchItem,
  searchSubjects,
} from './cardsApi'
import { buildRailTree, type RailChild, type RailDatasetNode } from './railTree'
import './rail.css'
import { SetForm } from './SetForm'
import { subjectDisplayLabel } from './subjectLabel'
import { formatSetTitle } from './setTitle'
import { addSubjectAndPersist, useSubjects } from './subjectStore'

export interface SubjectRailProps {
  /** 展開の既定（契約メモ §2.1: 4 つ以上は「いま開いているページのデータセット」
   *  だけ展開）を決めるために現在地を見る。 */
  route: Route
  navigate: (route: Route, opts?: { replace?: boolean }) => void
}

const EXPANDED_STORAGE = 'asterism.rail.expanded'

function loadExpandedOverrides(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(EXPANDED_STORAGE)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, boolean>) : {}
  } catch {
    return {}
  }
}

function saveExpandedOverrides(overrides: Record<string, boolean>): void {
  try {
    localStorage.setItem(EXPANDED_STORAGE, JSON.stringify(overrides))
  } catch {
    /* private mode 等 — 表示は今回のセッションだけ効く */
  }
}

/** いま開いているページのデータセット（契約メモ §2.1 の展開の既定）。 */
function currentDatasetId(route: Route, subjects: SubjectItem[]): string | null {
  if (route.datasetPageId) return route.datasetPageId
  if (route.setDatasetId) return route.setDatasetId
  if (route.subjectKey) {
    return subjects.find((s) => s.subject_key === route.subjectKey)?.dataset_id ?? null
  }
  return null
}

/** 左の一覧（契約メモ contract_pr_f2.md §2.1）。親＝データセット、子＝その中の
 *  1 件・条件で集めた一覧。データセットの出どころ（`GET /api/datasets`）で
 *  「自分のデータ」「オープンデータ」に振り分け、appdata の主語をその
 *  `dataset_id` で子として吊るす（木の組み立ては `railTree.ts` の純関数）。 */
export function SubjectRail({ route, navigate }: SubjectRailProps) {
  const { t } = useTranslation('cards')
  const all = useSubjects()
  const [datasets, setDatasets] = useState<CardsDatasetSummary[]>([])
  const [expandedOverrides, setExpandedOverrides] = useState<Record<string, boolean>>(
    loadExpandedOverrides,
  )

  useEffect(() => {
    let cancelled = false
    listDatasets()
      .then((d) => {
        if (!cancelled) setDatasets(d)
      })
      .catch(() => {
        /* best-effort: 取れなければ「自分のデータ」「オープンデータ」が空のまま */
      })
    return () => {
      cancelled = true
    }
  }, [])

  // /define・/details のあいだもレールでそのデータセット行が選択状態のまま
  // （契約メモ contract_pr_f5.md §1.4）。展開の既定と同じ値をそのまま
  // 「選択中」の判定にも使う — 現在地は 1 つしかない。
  const currentId = currentDatasetId(route, all)

  const tree = buildRailTree({
    datasets,
    subjects: all,
    currentDatasetId: currentId,
    expandedOverrides,
  })

  function toggleExpanded(datasetId: string, expandedNow: boolean) {
    setExpandedOverrides((cur) => {
      const next = { ...cur, [datasetId]: !expandedNow }
      saveExpandedOverrides(next)
      return next
    })
  }

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SubjectSearchItem[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(false)
  // 「条件で絞り込む」は SetForm.tsx（他担当）が要求する classIri が要る
  // （契約メモ §6.3 は class を選ぶ手段を明記していない）。名前検索の結果に出てきた
  // 種類（class）の 1 つを選んで絞り込みを開く、という形に倒す — 新規の API は
  // 増やさない。
  const [filterClass, setFilterClass] = useState<{ iri: string; label: string } | null>(null)

  function openSubject(item: SubjectItem) {
    navigate({ tab: 'cards', subjectKey: item.subject_key })
  }

  function openChild(child: RailChild) {
    navigate({ tab: 'cards', subjectKey: child.subjectKey })
  }

  async function onSearchSubmit(e: FormEvent) {
    e.preventDefault()
    const q = query.trim()
    if (!q) {
      setResults(null)
      setSearchError(false)
      return
    }
    setSearching(true)
    setSearchError(false)
    try {
      setResults(await searchSubjects(q))
    } catch {
      setResults(null)
      setSearchError(true)
    } finally {
      setSearching(false)
    }
  }

  function openSearchResult(result: SubjectSearchItem) {
    const item: SubjectItem = {
      kind: 'individual',
      id: result.iri,
      label: result.label,
      class_label: result.class_label,
      source: 'open',
      card_count: null,
      match: null,
      subject_key: `i:${result.iri}`,
      created_at: new Date().toISOString(),
      dataset_id: result.dataset_id ?? undefined,
    }
    addSubjectAndPersist(item)
    setResults(null)
    setQuery('')
    openSubject(item)
  }

  async function onSetSubmit(result: SetResolveResult) {
    // 種類（class）から親のデータセットを引く（契約メモ §2.1: 新しく保存する
    // ときは classSchema(spec.class).dataset_id から埋める）。取れなくても
    // 致命的にしない — その場合はレールの「その他」節に出る。
    let datasetId: string | undefined
    try {
      const schema = await classSchema(result.spec.class)
      datasetId = schema?.dataset_id ?? undefined
    } catch {
      /* best-effort */
    }
    // label は種類名だけでなく条件も含めた要約にする（同じ種類の絞り込みを
    // 見分けられるように・チェッカー指摘）。純関数 setTitle.ts の
    // formatSetTitle を使う（class_label と clauses から
    // 「国: 人口 が 1,000 万 より大きい」のような文を組む）。
    const item: SubjectItem = {
      kind: 'set',
      id: result.set_id,
      label: formatSetTitle(result.title, t),
      class_label: result.title.class_label,
      source: 'open',
      card_count: null,
      match: null,
      subject_key: `s:${result.set_id}`,
      spec: result.spec,
      created_at: new Date().toISOString(),
      dataset_id: datasetId,
    }
    addSubjectAndPersist(item)
    setFilterClass(null)
    openSubject(item)
  }

  // 検索結果に出てきた種類（class）の重複を除いた一覧（決定論: 出現順）。
  const resultClasses = (results ?? []).reduce<{ iri: string; label: string }[]>((acc, r) => {
    if (!r.class_iri || acc.some((c) => c.iri === r.class_iri)) return acc
    acc.push({ iri: r.class_iri, label: subjectDisplayLabel(r.class_label, r.class_iri) })
    return acc
  }, [])

  return (
    <div className="rail">
      <div className="rail-section">
        <h4 className="rail-section-title">{t('rail.own_title')}</h4>
        {tree.own.length === 0 ? (
          <p className="rail-empty">
            {t('rail.own_empty')}{' '}
            <button
              type="button"
              className="link-btn"
              onClick={() => navigate({ tab: 'gallery' })}
            >
              {t('rail.own_empty_link')}
            </button>
          </p>
        ) : (
          <div className="rail-datasets">
            {tree.own.map((node) => (
              <DatasetRow
                key={node.datasetId}
                node={node}
                isCurrent={node.datasetId === currentId}
                onToggle={() => toggleExpanded(node.datasetId, node.expanded)}
                onOpenDataset={() => navigate({ tab: 'cards', datasetPageId: node.datasetId })}
                onOpenChild={openChild}
                onCollect={() =>
                  navigate({ tab: 'cards', setNew: true, setDatasetId: node.datasetId })
                }
              />
            ))}
          </div>
        )}
      </div>

      <div className="rail-section">
        <h4 className="rail-section-title">{t('rail.open_title')}</h4>
        <form className="rail-search" onSubmit={onSearchSubmit}>
          <input
            type="search"
            className="rail-search-input"
            placeholder={t('rail.search_placeholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </form>
        {searching && <p className="rail-empty">{t('rail.searching')}</p>}
        {searchError && <p className="rail-empty">{t('rail.search_error')}</p>}
        {results && results.length === 0 && !searching && (
          <p className="rail-empty">{t('rail.search_empty')}</p>
        )}
        {results && results.length > 0 && (
          <div className="rail-list rail-search-results">
            {results.map((r) => (
              <button
                key={r.iri}
                type="button"
                className="rail-item rail-search-result"
                onClick={() => openSearchResult(r)}
              >
                <span className="rail-dot rail-dot--link" aria-hidden="true" />
                <span className="rail-item-label">{r.label}</span>
              </button>
            ))}
          </div>
        )}
        {resultClasses.map((c) => (
          <button
            key={c.iri}
            type="button"
            className="rail-condition-link"
            onClick={() => setFilterClass(c)}
          >
            {t('rail.filter_by_class', { label: c.label })}
          </button>
        ))}
        {filterClass && (
          <SetForm
            classIri={filterClass.iri}
            onSubmit={onSetSubmit}
            onCancel={() => setFilterClass(null)}
          />
        )}
        {tree.open.length > 0 && (
          <div className="rail-datasets">
            {tree.open.map((node) => (
              <DatasetRow
                key={node.datasetId}
                node={node}
                isCurrent={node.datasetId === currentId}
                onToggle={() => toggleExpanded(node.datasetId, node.expanded)}
                onOpenDataset={() => navigate({ tab: 'cards', datasetPageId: node.datasetId })}
                onOpenChild={openChild}
                onCollect={() =>
                  navigate({ tab: 'cards', setNew: true, setDatasetId: node.datasetId })
                }
              />
            ))}
          </div>
        )}
      </div>

      {tree.other.length > 0 && (
        <div className="rail-section">
          <h4 className="rail-section-title">{t('rail.other_section', { defaultValue: 'その他' })}</h4>
          <div className="rail-list">
            {tree.other.map((child) => (
              <RailChildButton key={child.subjectKey} child={child} onClick={() => openChild(child)} />
            ))}
          </div>
        </div>
      )}

      <div className="rail-legend">
        <span className="rail-legend-item">
          <span className="rail-dot rail-dot--link" aria-hidden="true" />
          {t('rail.legend_linked')}
        </span>
        <span className="rail-legend-item">
          <span className="rail-dot rail-dot--own" aria-hidden="true" />
          {t('rail.legend_own_only')}
        </span>
        <span className="rail-legend-item">
          <span className="rail-dot rail-dot--amb" aria-hidden="true" />
          {t('rail.legend_ambiguous')}
        </span>
        <span className="rail-legend-item">
          <span className="rail-dot rail-dot--set" aria-hidden="true" />
          {t('rail.legend_set')}
        </span>
      </div>
    </div>
  )
}

function dotVariantForChild(child: RailChild): 'link' | 'own' | 'amb' | 'set' {
  if (child.kind === 'set') return 'set'
  if (child.match === 'ambiguous') return 'amb'
  if (child.match === 'own_only') return 'own'
  return 'link'
}

function RailChildButton({ child, onClick }: { child: RailChild; onClick: () => void }) {
  const { t } = useTranslation('cards')
  const kindLabel = child.kind === 'set' ? t('rail.kindSet', { defaultValue: '一覧' }) : child.kindLabel
  return (
    <button type="button" className="rail-item" onClick={onClick}>
      <span className={`rail-dot rail-dot--${dotVariantForChild(child)}`} aria-hidden="true" />
      <span className="rail-item-label">{child.label || t('rail.unlabeled')}</span>
      {kindLabel && <span className="rail-item-kind">・{kindLabel}</span>}
    </button>
  )
}

/** 1 データセット行 + 展開時の子（契約メモ §2.1）。行クリック＝データセットの
 *  ページ、先頭のトグルは開閉だけ（ナビゲーションしない）。 */
function DatasetRow({
  node,
  isCurrent,
  onToggle,
  onOpenDataset,
  onOpenChild,
  onCollect,
}: {
  node: RailDatasetNode
  /** いま開いているページのデータセットか（契約メモ contract_pr_f5.md §1.4:
   *  `/define`・`/details` のあいだもレールで選択状態を保つ）。rail.css は
   *  担当外のため見た目の強調は加えず、属性のみ付与する。 */
  isCurrent: boolean
  onToggle: () => void
  onOpenDataset: () => void
  onOpenChild: (child: RailChild) => void
  onCollect: () => void
}) {
  const { t } = useTranslation('cards')
  const stateLabel =
    node.state === 'draft'
      ? t('rail.state_draft', { defaultValue: 'まだ意味を定義していません' })
      : node.state === 'ingesting'
        ? t('rail.state_ingesting', { defaultValue: '取り込み中' })
        : null
  const hasChildren = node.children.length > 0
  return (
    <div className="rail-dataset">
      <div className="rail-dataset-row">
        {hasChildren ? (
          <button
            type="button"
            className="rail-dataset-toggle"
            onClick={onToggle}
            aria-label={node.expanded ? t('rail.collapse', { defaultValue: 'たたむ' }) : t('rail.expand', { defaultValue: 'ひらく' })}
          >
            {node.expanded ? '▾' : '▸'}
          </button>
        ) : (
          <span className="rail-dataset-toggle rail-dataset-toggle--empty" aria-hidden="true" />
        )}
        <button
          type="button"
          className="rail-dataset-name"
          onClick={onOpenDataset}
          aria-current={isCurrent ? 'page' : undefined}
        >
          {node.label}
        </button>
        {node.isSample && (
          <span className="rail-item-sample">{t('rail.sampleBadge', { defaultValue: '見本' })}</span>
        )}
        {stateLabel && <span className="rail-dataset-state">{stateLabel}</span>}
      </div>
      {hasChildren && node.expanded && (
        <div className="rail-list rail-dataset-children">
          {node.children.map((child) => (
            <RailChildButton key={child.subjectKey} child={child} onClick={() => onOpenChild(child)} />
          ))}
        </div>
      )}
      {node.expanded && (
        <button type="button" className="rail-condition-link rail-collect-link" onClick={onCollect}>
          {t('rail.collect', { defaultValue: '＋ 条件で集める' })}
        </button>
      )}
    </div>
  )
}
