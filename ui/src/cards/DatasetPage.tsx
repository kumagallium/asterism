// データセットのページ（決定 2・契約メモ §2.2・担当 ui-page）。「作る」と「使う」
// の合流点: 見出し（App の cardsLabel と同じ経路 — `onLabel` で上げる）／
// 「データの意味を定義する」の帯／「この中のもの」。
import { type FormEvent, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Route } from '../App'
import {
  datasetSummary,
  searchSubjects,
  type DatasetSummary,
  type SubjectItem,
  type SubjectSearchItem,
} from './cardsApi'
import './dataset.css'
import { addSubjectAndPersist, useSubjects } from './subjectStore'

export type Translate = (key: string, options?: Record<string, unknown>) => string

// ---------------------------------------------------------------------------
// 純関数（datasetPage.test.ts で検証）
// ---------------------------------------------------------------------------

/** 帯の文言（決定 2）。`stage` が `promoted` なら数字つきの要約、それ以外は
 *  「まだ取り込んでいません」。件数は `classes[]` から合算する（新しい SPARQL を
 *  ui 側では書かない — 数字の出どころはサーバの summary だけ）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（FirstScreen.tsx と同じ理由）
export function meaningBandText(summary: DatasetSummary, t: Translate): string {
  if (summary.stage !== 'promoted') return t('dataset.meaning_pending')
  const properties = summary.classes.reduce((n, c) => n + c.properties, 0)
  const withLabel = summary.classes.reduce((n, c) => n + c.with_label, 0)
  const withUnit = summary.classes.reduce((n, c) => n + c.with_unit, 0)
  return t('dataset.meaning_ok', {
    classes: summary.classes.length,
    properties,
    withLabel,
    withUnit,
  })
}

/** 出どころの行（決定 2）: 出どころ（＋短い説明）・ライセンス・版を「・」で
 *  つなぐ。無い項目は省く（K4: 生の IRI・dataset_id・snapshot 名は出さない —
 *  版は短い名前だけ）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（FirstScreen.tsx と同じ理由）
export function originLine(summary: DatasetSummary, t: Translate): string {
  let origin = t(`dataset.origin_${summary.origin}`)
  if (summary.source_note) origin = t('dataset.origin_with_note', { origin, note: summary.source_note })
  const parts = [origin]
  if (summary.license) parts.push(t('dataset.license', { license: summary.license }))
  if (summary.snapshot) parts.push(t('dataset.version', { snapshot: summary.snapshot }))
  return parts.join(t('dataset.join'))
}

export interface KindRow {
  classIri: string
  label: string
  count: number
}

/** 「種類ごと」の行（決定論: summary が返す順のまま）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（FirstScreen.tsx と同じ理由）
export function kindRows(summary: DatasetSummary): KindRow[] {
  return summary.classes.map((c) => ({ classIri: c.class_iri, label: c.label, count: c.count }))
}

/** 「この中のもの」— appdata の主語のうち、この `dataset_id` に属するものだけ
 *  （契約メモ §2.2）。並びは呼び出し側の `subjects`（`sortSubjects` 済み）の順を
 *  保つ。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（FirstScreen.tsx と同じ理由）
export function subjectsForDataset(subjects: SubjectItem[], datasetId: string): SubjectItem[] {
  return subjects.filter((s) => s.dataset_id === datasetId)
}

// ---------------------------------------------------------------------------
// 画面
// ---------------------------------------------------------------------------

export interface DatasetPageProps {
  datasetId: string
  navigate: (route: Route, opts?: { replace?: boolean }) => void
  /** 「定義を直す」「続きから」（App が returnTo つきで workbench を開く）。 */
  onDefine: (datasetId: string) => void
  /** topbar の見出しへ（`App` の `cardsLabel` と同じ経路）。 */
  onLabel?: (label: string) => void
}

interface SummaryLoadState {
  datasetId: string
  summary: DatasetSummary | null
  error: boolean
}

const EMPTY_SUMMARY_LOAD: SummaryLoadState = { datasetId: '', summary: null, error: false }

export function DatasetPage({ datasetId, navigate, onDefine, onLabel }: DatasetPageProps) {
  const { t } = useTranslation('cards')
  const subjects = useSubjects()
  const [loaded, setLoaded] = useState<SummaryLoadState>(EMPTY_SUMMARY_LOAD)
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SubjectSearchItem[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(false)

  useEffect(() => {
    let cancelled = false
    datasetSummary(datasetId)
      .then((summary) => {
        if (!cancelled) setLoaded({ datasetId, summary, error: false })
      })
      .catch(() => {
        if (!cancelled) setLoaded({ datasetId, summary: null, error: true })
      })
    return () => {
      cancelled = true
    }
  }, [datasetId])

  const isCurrent = loaded.datasetId === datasetId
  const summary = isCurrent ? loaded.summary : null
  const loadError = isCurrent && loaded.error

  // 見出し（topbar）は summary が届いてから — 効果の本体は then/catch でだけ
  // 書き込む流儀（SubjectPage.tsx と同じ）なので、ここは「届いたら上げる」だけ。
  useEffect(() => {
    if (summary) onLabel?.(summary.label)
  }, [summary, onLabel])

  // datasetId が変わったら検索欄を閉じる（React の「prop が変わったら state を
  // 調整する」パターン — effect を使わない）。
  const [searchFor, setSearchFor] = useState(datasetId)
  if (searchFor !== datasetId) {
    setSearchFor(datasetId)
    setSearchOpen(false)
    setQuery('')
    setResults(null)
    setSearchError(false)
  }

  if (loadError) return <p className="ds-empty-note">{t('render_error')}</p>
  if (!summary) return <p className="ds-empty-note">{t('page.loading')}</p>

  const isOk = summary.stage === 'promoted'
  const rows = kindRows(summary)
  const contents = subjectsForDataset(subjects, datasetId)

  function goCollect(classIri?: string) {
    navigate({ tab: 'cards', setNew: true, setDatasetId: datasetId, setClassIri: classIri })
  }

  async function onSearchSubmit(e: FormEvent) {
    e.preventDefault()
    const q = query.trim()
    if (!q) return
    setSearching(true)
    setSearchError(false)
    try {
      setResults(await searchSubjects(q, 20, datasetId))
    } catch {
      setResults(null)
      setSearchError(true)
    } finally {
      setSearching(false)
    }
  }

  function openSearchResult(result: SubjectSearchItem) {
    // ネストした関数宣言の中では `summary` の非 null 絞り込みが tsc に伝わらない
    // ため、ここで改めて確かめる（呼び出し元は summary が届いてから描画される）。
    if (!summary) return
    const item: SubjectItem = {
      kind: 'individual',
      id: result.iri,
      label: result.label,
      class_label: result.class_label,
      source: summary.origin === 'own' ? 'own' : 'open',
      card_count: null,
      match: null,
      subject_key: `i:${result.iri}`,
      created_at: new Date().toISOString(),
      dataset_id: datasetId,
      dataset_label: summary.label,
    }
    addSubjectAndPersist(item)
    setSearchOpen(false)
    setResults(null)
    setQuery('')
    navigate({ tab: 'cards', subjectKey: item.subject_key })
  }

  return (
    <div className="cardpage-body dataset-page">
      <p className="dataset-origin">{originLine(summary, t)}</p>

      <div className="dataset-band">
        <div className="dataset-band-title">{t('dataset.section_meaning')}</div>
        <div className="dataset-band-row">
          <span className="dataset-band-text">
            {isOk ? '✓ ' : '▲ '}
            {meaningBandText(summary, t)}
          </span>
          <div className="dataset-band-actions">
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => onDefine(datasetId)}>
              {isOk ? t('dataset.define') : t('dataset.resume')}
            </button>
            {isOk && (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => navigate({ tab: 'gallery', datasetId })}
              >
                {t('dataset.details')}
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="cardpage-head">
        <h2 className="cardpage-title">{t('dataset.contents')}</h2>
        <div className="cardpage-head-actions dataset-contents-actions">
          <div className="dataset-search">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setSearchOpen((v) => !v)}
            >
              {t('dataset.open_one')} ▾
            </button>
            {searchOpen && (
              <form className="dataset-search-form" onSubmit={onSearchSubmit}>
                <input
                  type="search"
                  className="dataset-search-input"
                  value={query}
                  placeholder={t('rail.search_placeholder')}
                  onChange={(e) => setQuery(e.target.value)}
                />
                {searching && <p className="ds-empty-note">{t('rail.searching')}</p>}
                {searchError && <p className="ds-empty-note">{t('rail.search_error')}</p>}
                {results && results.length === 0 && !searching && (
                  <p className="ds-empty-note">{t('rail.search_empty')}</p>
                )}
                {results && results.length > 0 && (
                  <div className="dataset-search-results">
                    {results.map((r) => (
                      <button
                        key={r.iri}
                        type="button"
                        className="dataset-search-result"
                        onClick={() => openSearchResult(r)}
                      >
                        {r.label}
                      </button>
                    ))}
                  </div>
                )}
              </form>
            )}
          </div>
          <button type="button" className="btn btn--soft btn--sm" onClick={() => goCollect()}>
            {t('dataset.collect')}
          </button>
        </div>
      </div>

      {rows.length > 0 && (
        <div className="dataset-kinds">
          <span className="dataset-kinds-label">{t('dataset.by_kind')}: </span>
          {rows.map((row) => (
            <button
              key={row.classIri}
              type="button"
              className="dataset-kind-link"
              onClick={() => goCollect(row.classIri)}
            >
              {t('dataset.kind_row', { label: row.label, count: row.count })}
            </button>
          ))}
        </div>
      )}

      {contents.length === 0 ? (
        <p className="ds-empty-note">{t('dataset.empty')}</p>
      ) : (
        <div className="dataset-contents-list">
          {contents.map((item) => (
            <button
              key={item.subject_key}
              type="button"
              className="dataset-content-item"
              onClick={() => navigate({ tab: 'cards', subjectKey: item.subject_key })}
            >
              <span
                className={`dataset-dot dataset-dot--${item.kind === 'set' ? 'set' : 'individual'}`}
                aria-hidden="true"
              />
              <span className="dataset-content-label">{item.label ?? t('rail.unlabeled')}</span>
              <span className="dataset-content-kind">
                {item.kind === 'set' ? t('rail.kindSet') : item.class_label}
              </span>
              {item.card_count != null && <span className="dataset-content-count">{item.card_count}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
