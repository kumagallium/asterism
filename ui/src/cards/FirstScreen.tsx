// 初回の入口（契約メモ §3）。#/cards で「1 件」も「絞り込み」も「データを置く」
// も選んでいないときに CardsView が出す。旧「左の一覧から選んでください」を
// 置き換える — 上に入口 2 つ（データを置く／公開データを名前でさがす）、下に
// 見本（あれば）の 1 件のページをそのまま埋め込む。
//
// 「データを置く」入口のドロップは実際のファイル読み込み（inspect/commit）を
// 行わない — その一式（`createStaging`/`inspectPlace`/`commitPlace`）は
// PlaceView.tsx（ui-words 担当・担当外につき不可触）が持つ。ここは「同じ画面
// （`#/cards/place`）へ渡す」までを担う（notes の deviations 参照）。
import { type FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Route } from '../App'
import { AddIcon, SearchIcon } from '../icons'
import { searchSubjects, type SubjectItem, type SubjectSearchItem } from './cardsApi'
import './first.css'
import { SubjectPage } from './SubjectPage'
import { addSubjectAndPersist, useSubjects } from './subjectStore'

/**
 * 見本の選びかた（純関数・テスト対象 `firstScreen.test.ts`）。boot
 * （`api/src/asterism_api/local.py`・契約メモ §2）が起動時に 1 回だけ
 * `source: 'open'` の個体 1 件・絞り込み 1 件を仕込む。この 2 件は他の何より
 * 先に作られるため、`source: 'open'` のうち作られた時刻が一番古いものを
 * 「見本」とみなせば、後からユーザーが検索して増やしたオープンデータと
 * 区別できる（専用フラグを持たない — SubjectItem の形は変えない）。
 * App.tsx の parseHash/routeToHash と同じ理由で react-refresh/only-export-components
 * の対象外にする（テスト容易性のため意図して許容）。
 */
// eslint-disable-next-line react-refresh/only-export-components
export function pickSampleSubjects(subjects: SubjectItem[]): {
  individual: SubjectItem | null
  set: SubjectItem | null
} {
  const open = subjects.filter((s) => s.source === 'open')
  const byAge = [...open].sort((a, b) => a.created_at.localeCompare(b.created_at))
  return {
    individual: byAge.find((s) => s.kind === 'individual') ?? null,
    set: byAge.find((s) => s.kind === 'set') ?? null,
  }
}

export interface FirstScreenProps {
  navigate: (route: Route, opts?: { replace?: boolean }) => void
  /** ページ最下部の 1 行「<label> に聞く」から Ask へ（埋め込んだ見本ページに
   *  そのまま渡す）。 */
  onAsk: (question: string) => void
}

export function FirstScreen({ navigate, onAsk }: FirstScreenProps) {
  const { t } = useTranslation('cards')
  const subjects = useSubjects()
  const { individual: sample } = pickSampleSubjects(subjects)

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SubjectSearchItem[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(false)
  const [dragOver, setDragOver] = useState(false)

  function goPlace() {
    navigate({ tab: 'cards', place: true })
  }

  async function onSearchSubmit(e: FormEvent) {
    e.preventDefault()
    const q = query.trim()
    if (!q) return
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
    navigate({ tab: 'cards', subjectKey: item.subject_key })
  }

  return (
    <div className="first-screen">
      <div className="first-entries">
        <button
          type="button"
          className={`first-entry kz-drop${dragOver ? ' drag' : ''}`}
          onClick={goPlace}
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragOver(false)
            goPlace()
          }}
        >
          <AddIcon className="first-entry-icon" />
          <span className="kz-drop-main">
            {t('firstScreen.placeTitle', { defaultValue: 'あなたのデータを置く' })}
          </span>
          <span className="kz-drop-sub">
            {t('firstScreen.placeHint', { defaultValue: 'ここに置くか、クリックして進みます' })}
          </span>
        </button>

        <form className="first-entry first-entry--search" onSubmit={onSearchSubmit}>
          <SearchIcon className="first-entry-icon" />
          <span className="first-entry-title">
            {t('firstScreen.searchTitle', { defaultValue: '公開データを名前でさがす' })}
          </span>
          <input
            type="search"
            className="first-search-input"
            placeholder={t('rail.search_placeholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" className="btn btn--sm">
            {t('firstScreen.searchButton', { defaultValue: 'さがす' })}
          </button>
          {searching && <p className="first-search-note">{t('rail.searching')}</p>}
          {searchError && <p className="first-search-note">{t('rail.search_error')}</p>}
          {results && results.length === 0 && !searching && (
            <p className="first-search-note">{t('rail.search_empty')}</p>
          )}
          {results && results.length > 0 && (
            <div className="first-search-results">
              {results.map((r) => (
                <button
                  key={r.iri}
                  type="button"
                  className="first-search-result"
                  onClick={() => openSearchResult(r)}
                >
                  {r.label}
                </button>
              ))}
            </div>
          )}
        </form>
      </div>

      {sample ? (
        <div className="first-sample">
          <p className="first-sample-note">
            {t('firstScreen.sampleIntro', {
              defaultValue: '見本: 世界の国（Gapminder）。あなたのファイルを置くと同じ形で並びます',
            })}
          </p>
          <SubjectPage
            iri={sample.id}
            onSelectCard={(cardId) =>
              navigate({ tab: 'cards', subjectKey: sample.subject_key, cardId })
            }
            onCloseCard={() => navigate({ tab: 'cards', subjectKey: sample.subject_key })}
            onOpenSubject={(nextIri) => navigate({ tab: 'cards', subjectKey: `i:${nextIri}` })}
            onAsk={onAsk}
            onEditDefinition={(datasetId) =>
              navigate({ tab: 'gallery', datasetId, detailTab: 'design' })
            }
          />
        </div>
      ) : (
        <div className="first-empty">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => navigate({ tab: 'cardsdemo' })}
          >
            {t('firstScreen.viewSampleGallery', { defaultValue: '見本を見る' })}
          </button>
        </div>
      )}
    </div>
  )
}
