import { type FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Route } from '../App'
import {
  type SetResolveResult,
  type SubjectItem,
  type SubjectSearchItem,
  searchSubjects,
} from './cardsApi'
import './rail.css'
import { SetForm } from './SetForm'
import { subjectDisplayLabel } from './subjectLabel'
import { addSubjectAndPersist, sortSubjects, useSubjects } from './subjectStore'

export interface SubjectRailProps {
  navigate: (route: Route, opts?: { replace?: boolean }) => void
}

/** 左の一覧（契約メモ §6.3）。自分のデータ（置いたファイル由来）とオープンデータ
 *  （さがして開いたもの）の 2 セクション。1 件は ●、絞り込みは ■（凡例参照）。 */
export function SubjectRail({ navigate }: SubjectRailProps) {
  const { t } = useTranslation('cards')
  const all = useSubjects()
  const own = sortSubjects(all.filter((i) => i.source === 'own'))
  const open = sortSubjects(all.filter((i) => i.source === 'open'))

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
    }
    addSubjectAndPersist(item)
    setResults(null)
    setQuery('')
    openSubject(item)
  }

  function onSetSubmit(result: SetResolveResult) {
    const item: SubjectItem = {
      kind: 'set',
      id: result.set_id,
      label: result.title.class_label,
      class_label: result.title.class_label,
      source: 'open',
      card_count: null,
      match: null,
      subject_key: `s:${result.set_id}`,
      spec: result.spec,
      created_at: new Date().toISOString(),
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
        {own.length === 0 ? (
          <p className="rail-empty">{t('rail.own_empty')}</p>
        ) : (
          <div className="rail-list">
            {own.map((item) => (
              <RailButton key={item.subject_key} item={item} onClick={() => openSubject(item)} />
            ))}
          </div>
        )}
        <button
          type="button"
          className="rail-add"
          onClick={() => navigate({ tab: 'cards', place: true })}
        >
          {t('rail.add')}
        </button>
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
        {open.length > 0 && (
          <div className="rail-list">
            {open.map((item) => (
              <RailButton key={item.subject_key} item={item} onClick={() => openSubject(item)} />
            ))}
          </div>
        )}
      </div>

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

function dotVariant(item: SubjectItem): 'link' | 'own' | 'amb' | 'set' {
  if (item.kind === 'set') return 'set'
  if (item.match === 'ambiguous') return 'amb'
  if (item.match === 'own_only') return 'own'
  return 'link'
}

function RailButton({ item, onClick }: { item: SubjectItem; onClick: () => void }) {
  const { t } = useTranslation('cards')
  return (
    <button type="button" className="rail-item" onClick={onClick}>
      <span className={`rail-dot rail-dot--${dotVariant(item)}`} aria-hidden="true" />
      <span className="rail-item-label">{item.label ?? t('rail.unlabeled')}</span>
      {item.card_count != null && <span className="rail-item-count">{item.card_count}</span>}
    </button>
  )
}
