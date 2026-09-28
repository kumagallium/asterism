// 追加画面（契約メモ contract_pr_f9.md §3・§5 実装順(1)・担当 ui-page）。
// 「あなたのデータを追加する」を撤去し、オープンデータ／自分のデータの区切りも
// やめる（ユーザー指摘①②）——左に Asterism にある「種類」の一覧、右に選んだ
// 種類の検索欄＋オブジェクト一覧（空の検索＝名前順の先頭 50 件）＋「条件で
// 集める」（既存の SetForm を newFor モードで再利用）。1 件を押すと
// subjectStore に保存して #/cards/i/<iri> へ（決定 3）。
//
// `listClasses()`/`ClassEntry`/`class_iri` 付きの `searchSubjects` は
// `cardsApi.ts`（ui-rail 担当・契約メモ §2.1・§2.2）が既に持つ — そちらを
// そのまま使う（並行実装のあいだ同名で薄く書いてここに置いていたが、統合が
// 済んだので一本化した。契約メモ §3「無い間は同じ名前で fetch を書き統合で
// 一本化」）。
import { type FormEvent, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  listClasses,
  searchSubjectsPage,
  type ClassEntry,
  type SubjectItem,
  type SubjectSearchItem,
} from './cardsApi'
import { addSubjectAndPersist } from './subjectStore'
import './addObject.css'

// 契約メモ contract_pr_f10.md §1.1: 一覧は「全部か一部か」を必ず言う＋カードの
// 格子＋「もっと見る」で続きを取る。1 ページの件数（先頭・「もっと見る」とも
// この件数）。

/** 種類の並び（純関数・addObject.test.ts）。サーバは「件数の多い順→名前順」
 *  で返す契約（契約メモ §2.1）だが、描画側でも同じ規則を掛け直して安全側に
 *  倒す（サーバの並びが崩れても画面は壊れない）。
 *  react-refresh: このファイルはコンポーネントに加えて純関数も export する
 *  （DatasetPage.tsx の各純関数と同じ理由 — テスト容易性のため意図して許容）。 */
// eslint-disable-next-line react-refresh/only-export-components
export function sortClassEntries(entries: ClassEntry[]): ClassEntry[] {
  return [...entries].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}

/** 検索欄の値 → `cardsApi.searchSubjects` に送る `q`（純関数・
 *  addObject.test.ts）。前後の空白を落とすだけ — 空になっても block しない
 *  （契約メモ §2.2「`q` が空でもよい（その種類の名前順の先頭 limit 件）」）。 */
// eslint-disable-next-line react-refresh/only-export-components -- 上のコメントと同じ理由
export function normalizeSearchQuery(query: string): string {
  return query.trim()
}

/** ui-rail の実 `navigate`（`App.tsx` の `Route`）を汎用に受ける
 *  （`DatasetPage.tsx`/`PlaceView.tsx` と同じ理由 — 並行実装の間は実 Route 型
 *  に依存しない。`#/cards/add`・`#/cards/i/<iri>`・`#/cards/s/new` のうち
 *  この画面が使うのは後ろ 2 つの既存フィールドだけ）。 */
export type AddObjectNavigateFn = (route: { tab: string; [key: string]: unknown }) => void

/** 種類のページ「＋ 追加」からこの画面を開くとき、選んでいた種類を引き継ぐ
 *  受け渡し口（契約メモ §3・§5 実装順(1)）。`Route`/`CardsView.tsx`（ui-rail
 *  担当）はまだ `route.add` に class を渡す経路を持たないため、`initialClassIri`
 *  prop の代わりに `sessionStorage` を 1 回だけ使う（タブを閉じれば消える・
 *  他画面には影響しない）。`ClassPage.tsx` の `goAdd()` が書き込み、この画面が
 *  起動時に 1 回だけ読んで消費する（読んだら即座に消す）。 */
export const PENDING_CLASS_IRI_KEY = 'asterism.cards.pendingAddClassIri'

/** 開いた瞬間に選んでおく種類（純関数・addObject.test.ts）。明示の
 *  `initialClassIri`（Route が対応すればそちらを優先）を優先し、無ければ
 *  `sessionStorage` から読んだ値を使う。 */
// eslint-disable-next-line react-refresh/only-export-components -- 上のコメントと同じ理由
export function resolveInitialClassIri(explicit: string | undefined, stored: string | null): string | undefined {
  return explicit ?? stored ?? undefined
}

/** 一覧の件数の行（純関数・addObject.test.ts）。3 態（契約メモ §1.1.1・§4）:
 *  検索中は必ず `count_search`。検索していないとき、表示中がすべてなら
 *  `count_all`、まだ隠れているものがあれば `count_partial`。 */
// eslint-disable-next-line react-refresh/only-export-components -- 上と同じ理由
export function countLineParams(
  query: string,
  shown: number,
  total: number,
): { key: 'add.count_search' | 'add.count_all' | 'add.count_partial'; params: Record<string, unknown> } {
  if (query) return { key: 'add.count_search', params: { q: query, shown } }
  if (shown >= total) return { key: 'add.count_all', params: { total } }
  return { key: 'add.count_partial', params: { total, shown } }
}

/** 「もっと見る」ボタンの表示（純関数・addObject.test.ts）。まだ隠れている
 *  ものが無ければ `null`（ボタンを出さない）。あれば、次に取ってくる件数
 *  `n`（ページ分か残りの小さいほう）と、残り総数 `rest` を返す。 */
// eslint-disable-next-line react-refresh/only-export-components -- 上と同じ理由
export function moreButtonParams(shown: number, total: number, pageSize: number): { n: number; rest: number } | null {
  const rest = total - shown
  if (rest <= 0) return null
  return { n: Math.min(pageSize, rest), rest }
}

/** 「もっと見る」で取ってきたページを既存の一覧に足す（純関数・
 *  addObject.test.ts）。同じ主語が既にあれば足さない（offset のずれで二重に
 *  出るのを防ぐ安全側）。 */
// eslint-disable-next-line react-refresh/only-export-components -- 上と同じ理由
export function appendResultsPage(existing: SubjectSearchItem[], page: SubjectSearchItem[]): SubjectSearchItem[] {
  const seen = new Set(existing.map((item) => item.iri))
  const appended = page.filter((item) => !seen.has(item.iri))
  return [...existing, ...appended]
}

export interface AddObjectViewProps {
  navigate: AddObjectNavigateFn
  /** 種類のページ「＋ 追加」から、その種類を開いた状態で入る（契約メモ §3・
   *  §5 実装順(1)「initialClassIri があればその種類を開いた状態で」）。
   *  `Route`/`CardsView.tsx`（ui-rail 担当）はまだこの prop を渡していない
   *  ため、実際の引き継ぎは `PENDING_CLASS_IRI_KEY`（`sessionStorage`）経由
   *  で行っている（下の初期化を参照）。 */
  initialClassIri?: string
}

interface ClassesLoadState {
  classes: ClassEntry[] | null
  error: boolean
}

interface ResultsLoadState {
  classIri: string
  query: string
  items: SubjectSearchItem[] | null
  total: number
  error: boolean
}

const EMPTY_RESULTS: ResultsLoadState = { classIri: '', query: '', items: null, total: 0, error: false }

/** 一覧の 1 ページの件数（先頭・「もっと見る」とも）契約メモ §1.1.3。 */
const PAGE_SIZE = 60

export function AddObjectView({ navigate, initialClassIri }: AddObjectViewProps) {
  const { t } = useTranslation('cards')
  const [classesState, setClassesState] = useState<ClassesLoadState>({ classes: null, error: false })
  const [selectedClassIri, setSelectedClassIri] = useState<string | undefined>(() => {
    // `ClassPage.tsx` の `goAdd()` が控えた種類を 1 回だけ読んで消費する
    // （`PENDING_CLASS_IRI_KEY` 参照）。
    let stored: string | null = null
    try {
      stored = sessionStorage.getItem(PENDING_CLASS_IRI_KEY)
      if (stored) sessionStorage.removeItem(PENDING_CLASS_IRI_KEY)
    } catch {
      // sessionStorage が使えない環境（プライベートモード等）では諦める。
    }
    return resolveInitialClassIri(initialClassIri, stored)
  })
  const [query, setQuery] = useState('')
  const [committedQuery, setCommittedQuery] = useState('')
  const [results, setResults] = useState<ResultsLoadState>(EMPTY_RESULTS)
  const [loadingMore, setLoadingMore] = useState(false)

  useEffect(() => {
    let cancelled = false
    listClasses()
      .then((items) => {
        if (!cancelled) setClassesState({ classes: sortClassEntries(items), error: false })
      })
      .catch(() => {
        if (!cancelled) setClassesState({ classes: null, error: true })
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 種類を切り替えたら検索欄を忘れる（React の「prop/選択が変わったら state を
  // 調整する」パターン — SubjectPage.tsx などと同じ流儀）。
  const [resetFor, setResetFor] = useState(selectedClassIri)
  if (resetFor !== selectedClassIri) {
    setResetFor(selectedClassIri)
    setQuery('')
    setCommittedQuery('')
    setLoadingMore(false)
  }

  // 種類を選んだ直後・検索を確定したときに読む。`committedQuery` が空でも
  // 読む（契約メモ §2.2・§3「空の検索＝先頭 PAGE_SIZE 件」）。「読み込み中」は
  // 専用の state を持たず、下の `showingResults`（結果が今の選択・確定検索語
  // と一致するか）だけで判定する（DatasetPage.tsx/SetPage.tsx と同じ流儀 —
  // set-state-in-effect を避ける）。
  useEffect(() => {
    if (!selectedClassIri) return
    let cancelled = false
    searchSubjectsPage(committedQuery, PAGE_SIZE, 0, selectedClassIri)
      .then((page) => {
        if (!cancelled) {
          setResults({ classIri: selectedClassIri, query: committedQuery, items: page.items, total: page.total, error: false })
        }
      })
      .catch(() => {
        if (!cancelled) {
          setResults({ classIri: selectedClassIri, query: committedQuery, items: null, total: 0, error: true })
        }
      })
    return () => {
      cancelled = true
    }
  }, [selectedClassIri, committedQuery])

  function onSearchSubmit(e: FormEvent) {
    e.preventDefault()
    setCommittedQuery(normalizeSearchQuery(query))
  }

  /** 「さらに N 件」（契約メモ §1.1.3）。今の一覧の末尾から `offset` で続きを
   *  取り、二重に出ないよう `appendResultsPage` で足す。 */
  async function loadMore() {
    if (!selectedClassIri || !results.items || loadingMore) return
    const classIri = selectedClassIri
    const query = committedQuery
    const offset = results.items.length
    setLoadingMore(true)
    try {
      const page = await searchSubjectsPage(query, PAGE_SIZE, offset, classIri)
      setResults((prev) =>
        prev.classIri === classIri && prev.query === query && prev.items
          ? { ...prev, items: appendResultsPage(prev.items, page.items), total: page.total }
          : prev,
      )
    } catch {
      // 一覧はそのまま残す — もう一度「さらに」を押せば再試行できる。
    } finally {
      setLoadingMore(false)
    }
  }

  /** 1 件を押す（契約メモ §3「1 件を押すと子ナビに加わり、そのページへ」）。 */
  function pickResult(entry: ClassEntry, item: SubjectSearchItem) {
    const stored: SubjectItem = {
      kind: 'individual',
      id: item.iri,
      label: item.label,
      class_label: item.class_label,
      source: entry.is_demo ? 'open' : 'own',
      card_count: null,
      match: null,
      subject_key: `i:${item.iri}`,
      created_at: new Date().toISOString(),
      dataset_id: entry.dataset_id,
      dataset_label: entry.dataset_label,
      class_iri: entry.class_iri,
    }
    addSubjectAndPersist(stored)
    navigate({ tab: 'cards', subjectKey: stored.subject_key })
  }

  function goCollect(entry: ClassEntry) {
    navigate({ tab: 'cards', setNew: true, setDatasetId: entry.dataset_id, setClassIri: entry.class_iri })
  }

  const selectedEntry = classesState.classes?.find((c) => c.class_iri === selectedClassIri) ?? null
  const showingResults = results.classIri === selectedClassIri && results.query === committedQuery

  return (
    <div className="cardpage-body addobject-page">
      <h2 className="cardpage-title">{t('add.title')}</h2>
      <div className="addobject-layout">
        <div className="addobject-kinds">
          {classesState.error && <p className="ds-empty-note">{t('render_error')}</p>}
          {!classesState.classes && !classesState.error && <p className="ds-empty-note">{t('page.loading')}</p>}
          {classesState.classes && classesState.classes.length === 0 && (
            <p className="ds-empty-note">{t('empty')}</p>
          )}
          {classesState.classes?.map((entry) => (
            <button
              key={entry.class_iri}
              type="button"
              className={
                entry.class_iri === selectedClassIri
                  ? 'addobject-kind-item addobject-kind-item--active'
                  : 'addobject-kind-item'
              }
              onClick={() => setSelectedClassIri(entry.class_iri)}
            >
              <span className="addobject-kind-head">
                <span className="addobject-kind-label">{entry.label}</span>
                <span className="addobject-kind-count">{t('add.kind_count', { count: entry.count })}</span>
                {entry.is_demo && <span className="rail-item-sample">{t('rail.sampleBadge')}</span>}
              </span>
              <span className="addobject-kind-source">{entry.dataset_label}</span>
            </button>
          ))}
        </div>

        <div className="addobject-body">
          {!selectedEntry && classesState.classes && <p className="ds-empty-note">{t('add.pick_kind_first')}</p>}
          {selectedEntry && (
            <>
              <div className="addobject-search-row">
                <form className="addobject-search-form" onSubmit={onSearchSubmit}>
                  <input
                    type="search"
                    className="addobject-search-input"
                    value={query}
                    placeholder={t('add.search_placeholder', { total: selectedEntry.count })}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </form>
                <button
                  type="button"
                  className="btn btn--soft btn--sm addobject-collect"
                  onClick={() => goCollect(selectedEntry)}
                >
                  {t('add.collect')}
                </button>
              </div>
              {!showingResults && <p className="ds-empty-note">{t('page.loading')}</p>}
              {showingResults && results.error && <p className="ds-empty-note">{t('render_error')}</p>}
              {showingResults && !results.error && results.items && results.items.length === 0 && (
                <p className="ds-empty-note">{t('add.empty')}</p>
              )}
              {showingResults && !results.error && results.items && results.items.length > 0 && (
                <>
                  {(() => {
                    const line = countLineParams(committedQuery, results.items.length, results.total)
                    return <p className="addobject-count">{t(line.key, line.params)}</p>
                  })()}
                  <div className="addobject-grid">
                    {results.items.map((item) => (
                      <button
                        key={item.iri}
                        type="button"
                        className="addobject-card"
                        onClick={() => pickResult(selectedEntry, item)}
                      >
                        <span className="dataset-dot dataset-dot--individual" aria-hidden="true" />
                        <span className="addobject-card-body">
                          <span className="addobject-card-label">{item.label}</span>
                          <span className="addobject-card-sub">{selectedEntry.dataset_label}</span>
                        </span>
                      </button>
                    ))}
                  </div>
                  {(() => {
                    const more = moreButtonParams(results.items.length, results.total, PAGE_SIZE)
                    if (!more) return null
                    return (
                      <button
                        type="button"
                        className="btn btn--soft btn--sm addobject-more"
                        onClick={loadMore}
                        disabled={loadingMore}
                      >
                        {t('add.more', { n: more.n, rest: more.rest })}
                      </button>
                    )
                  })()}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
