// 種類のページ（契約メモ contract_pr_f9.md §1 決定 4・§5 実装順(2)・担当
// ui-page）。出どころ／この種類の観点（同梱＋使用中）／この種類のオブジェクト
// （＋「＋ 追加」）／「データの定義を見る」（実態は details へ — goDefinition
// のコメント参照。文言を契約メモの「見る・直す」から変えている）。
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { classSchema, listClasses } from './cardsApi'
import type { ClassEntry, ClassSchema, SubjectItem } from './cardsApi'
import { type AddObjectNavigateFn } from './AddObjectView'
import { useAllCards } from './cardStore'
import type { CardSpec } from './cardStore'
import './dataset.css'
import './classPage.css'
import './pages.css'
import { useSubjects } from './subjectStore'
import './viewpoints.css'
import { declaredViewpoints, viewpointsFrom } from './viewpoints'
// PR F12（ui-drawer 担当）が新設するモジュール。まだ存在しない間は import
// だけ書いておき、統合段で繋ぐ（契約メモ PR F12 §2「並列中の仮置き」）。
import { PageChatDrawer } from './PageChatDrawer'
import type { PageChatSummary } from './SubjectPage'

// ---------------------------------------------------------------------------
// 純関数（classPage.test.ts で検証）
// ---------------------------------------------------------------------------

/** `SubjectItem.class_iri`（individual）優先、無ければ一覧（kind: 'set'）の
 *  `spec.class` から（契約メモ contract_pr_f9.md §1-2・cardsApi.ts の
 *  `SubjectItem.class_iri` コメント参照）。既存の保存済み項目には
 *  `class_iri` が無いことがある — `subjectStore.ts` が読み込み時に 1 回だけ
 *  埋め戻す（ui-rail 担当）ので、ここでは「無ければ諦める」だけでよい。 */
function classIriOf(item: SubjectItem): string | null {
  if (item.class_iri) return item.class_iri
  if (item.kind === 'set' && item.spec) return item.spec.class
  return null
}

/** この種類のオブジェクト（純関数）。並びは呼び出し側の `subjects`
 *  （`sortSubjects` 済み）の順を保つ（`DatasetPage.tsx` の `subjectsForDataset`
 *  と同じ流儀）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（DatasetPage.tsx の各純関数と同じ理由）。
export function subjectsForClass(subjects: SubjectItem[], classIri: string): SubjectItem[] {
  return subjects.filter((s) => classIriOf(s) === classIri)
}

export interface ClassViewpointRow {
  id: string
  title: string
  /** 同梱（宣言ツール・`classSchema.tools`）なら true。 */
  bundled: boolean
  /** 使用中の観点（カードから派生）だけが持つ使用ページ数。同梱のみの行は 0。 */
  usedOn: number
}

/** 「この種類の観点」の合流（契約メモ §1 決定 4「同梱の宣言ツール＝『同梱』の
 *  印、使われた観点＝『N ページで使用』」）。同梱（`declaredViewpoints`）を
 *  宣言順のまま先頭に並べ、そのあとに使用中の観点（`viewpointsFrom` — この
 *  種類のカードから毎回決定論に派生するもの）を使用数の多い順→題名順で続ける。
 *  同じ id が両方にあれば（宣言ツールの `name` と使用中の観点 `vp-<hash>` は
 *  名前空間が違うため実務上は起こらないが、念のため）同梱側だけを残す。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（DatasetPage.tsx の各純関数と同じ理由）。
export function classViewpointRows(schema: ClassSchema, cards: CardSpec[]): ClassViewpointRow[] {
  const bundled = declaredViewpoints(schema)
  const rows: ClassViewpointRow[] = bundled.map((v) => ({ id: v.id, title: v.title, bundled: true, usedOn: 0 }))
  const bundledIds = new Set(bundled.map((v) => v.id))
  const used = viewpointsFrom(cards).filter((v) => v.class === schema.class_iri && !bundledIds.has(v.id))
  for (const v of used) rows.push({ id: v.id, title: v.title, bundled: false, usedOn: v.usedOn })
  return rows
}

// ---------------------------------------------------------------------------
// 画面
// ---------------------------------------------------------------------------

export interface ClassPageProps {
  classIri: string
  navigate: AddObjectNavigateFn
  /** topbar の見出しへ（`DatasetPage.tsx` の `onLabel` と同じ経路）。 */
  onLabel?: (label: string) => void
  /** 「定義を直す」— App の onDefine（設計の下書きがあればウィザード、無ければ
   *  詳細の設計タブへ倒す判定を持つ）。 */
  onDefine?: (datasetId: string) => void
}

interface EntryLoadState {
  classIri: string
  entry: ClassEntry | null
  error: boolean
}

interface SchemaLoadState {
  classIri: string
  schema: ClassSchema | null
  error: boolean
}

const EMPTY_ENTRY: EntryLoadState = { classIri: '', entry: null, error: false }
const EMPTY_SCHEMA: SchemaLoadState = { classIri: '', schema: null, error: false }

export function ClassPage({ classIri, navigate, onLabel, onDefine }: ClassPageProps) {
  const { t } = useTranslation('cards')
  const subjects = useSubjects()
  const allCards = useAllCards()
  const [entryState, setEntryState] = useState<EntryLoadState>(EMPTY_ENTRY)
  const [schemaState, setSchemaState] = useState<SchemaLoadState>(EMPTY_SCHEMA)
  // 下の入力欄・「＋ 観点を足す」はどちらも会話ドロワー（PageChatDrawer・
  // PR F12）を開く（契約メモ §1 決定 1・6）。
  const [askText, setAskText] = useState('')
  const [chatOpen, setChatOpen] = useState(false)
  const [chatInitialMessage, setChatInitialMessage] = useState<string | undefined>(undefined)
  // classIri が変わったらドロワーを閉じる（SubjectPage.tsx と同じ「prop が
  // 変わったら state を調整する」パターン）。
  const [chatFor, setChatFor] = useState(classIri)
  if (chatFor !== classIri) {
    setChatFor(classIri)
    setChatOpen(false)
    setChatInitialMessage(undefined)
    setAskText('')
  }

  useEffect(() => {
    let cancelled = false
    listClasses()
      .then((items) => {
        if (cancelled) return
        setEntryState({ classIri, entry: items.find((c) => c.class_iri === classIri) ?? null, error: false })
      })
      .catch(() => {
        if (!cancelled) setEntryState({ classIri, entry: null, error: true })
      })
    return () => {
      cancelled = true
    }
  }, [classIri])

  useEffect(() => {
    let cancelled = false
    classSchema(classIri)
      .then((s) => {
        if (!cancelled) setSchemaState({ classIri, schema: s, error: !s })
      })
      .catch(() => {
        if (!cancelled) setSchemaState({ classIri, schema: null, error: true })
      })
    return () => {
      cancelled = true
    }
  }, [classIri])

  const entry = entryState.classIri === classIri ? entryState.entry : null
  const entryError = entryState.classIri === classIri && entryState.error
  const schema = schemaState.classIri === classIri ? schemaState.schema : null

  // 見出し（topbar）は entry が届いてから — 効果の本体は then/catch でだけ
  // 書き込む流儀（DatasetPage.tsx と同じ）なので、ここは「届いたら上げる」だけ。
  useEffect(() => {
    if (entry) onLabel?.(entry.label)
  }, [entry, onLabel])

  const objects = useMemo(() => subjectsForClass(subjects, classIri), [subjects, classIri])
  const viewpointRows = useMemo(() => (schema ? classViewpointRows(schema, allCards) : []), [schema, allCards])

  // ドロワー（PageChatDrawer）へ渡す要約。この画面は個々のカードを実行
  // していない（出どころ・観点一覧・オブジェクト一覧という「見出し」だけの
  // 画面）ため、`cards` は空のまま — `facts` だけで答える（deviations 参照）。
  const pageSummary: PageChatSummary = useMemo(() => {
    if (!entry) return { facts: [], cards: [] }
    return {
      facts: [
        { label: t('pagechat.summary_dataset'), value: entry.dataset_label },
        { label: t('pagechat.summary_total'), value: String(entry.count) },
      ],
      cards: [],
    }
  }, [entry, t])

  function goAdd() {
    // `#/cards/add?kind=<class_iri>` — この種類を選んだ状態で追加画面へ（PR F9）。
    navigate({ tab: 'cards', add: true, addClassIri: classIri })
  }

  function goFix() {
    if (!entry) return
    onDefine?.(entry.dataset_id)
  }

  function goDefinition() {
    // データセットの詳細（`details`）へ「見る」だけ（契約メモ §1 決定 4「デ
    // ータの定義を見る・直す」とは文言を変えている — deviations 参照）。
    // 「直す」に当たる define ウィザードへの遷移は、この種類が単一の
    // データセットに属するとは限らない一方、`onDefine`（fetchProposal の
    // 有無で define/details のどちらに倒すか決める判定）は `App.tsx` が持ち
    // `CardsView.tsx`（ui-rail 担当）経由でしか渡せないため、ここではその
    // 配線を待たずに「見る」だけへ倒す。details 画面の「見直す」からは引き
    // 続き 1 手間で定義を直せる。
    if (!entry) return
    navigate({ tab: 'cards', datasetPageId: entry.dataset_id, datasetSub: 'details' })
  }

  if (entryError) return <p className="ds-empty-note">{t('render_error')}</p>
  if (!entry) return <p className="ds-empty-note">{t('page.loading')}</p>

  return (
    <div className="cardpage-body classpage">
      <h2 className="cardpage-title">{entry.label}</h2>
      <p className="dataset-origin">
        {t('classpage.source', { dataset: entry.dataset_label })}
        {entry.is_demo && <span className="rail-item-sample classpage-sample">{t('rail.sampleBadge')}</span>}
        {' · '}
        {t('add.kind_count', { count: entry.count })}
      </p>

      <div className="cardpage-head">
        <h3 className="cardpage-title classpage-subtitle">{t('classpage.viewpoints')}</h3>
        <div className="cardpage-head-actions">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => {
              setChatInitialMessage(undefined)
              setChatOpen(true)
            }}
          >
            {t('newcard.button')}
          </button>
        </div>
      </div>
      {viewpointRows.length === 0 ? (
        <p className="ds-empty-note">{t('empty')}</p>
      ) : (
        <div className="dataset-viewpoints-list">
          {viewpointRows.map((row) => (
            // 見た目だけ（押しても何もしない — K30: 押せる見た目にしない）。
            <div key={row.id} className="dataset-viewpoint-item">
              <span className="dataset-viewpoint-title">{row.title}</span>
              <span className="dataset-viewpoint-kind">
                {row.bundled ? t('classpage.viewpoint_bundled') : t('classpage.viewpoint_used', { count: row.usedOn })}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="cardpage-head">
        <h3 className="cardpage-title classpage-subtitle">{t('classpage.objects')}</h3>
        <div className="cardpage-head-actions">
          <button type="button" className="btn btn--soft btn--sm" onClick={goAdd}>
            {t('classpage.add_object')}
          </button>
        </div>
      </div>
      {objects.length === 0 ? (
        <p className="ds-empty-note">{t('rail.objects_empty')}</p>
      ) : (
        <div className="dataset-contents-list">
          {objects.map((item) => (
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
            </button>
          ))}
        </div>
      )}

      <div className="classpage-definition">
        <button type="button" className="btn btn--ghost btn--sm" onClick={goDefinition}>
          {t('classpage.definition')}
        </button>
        {onDefine && entry && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={goFix}>
            {t('classpage.define', { defaultValue: '定義を直す' })}
          </button>
        )}
      </div>
      <div className="cardpage-bar">
        <span className="cardpage-bar-who">{t('page.ask_who', { label: entry.label })}</span>
        <input
          className="cardpage-bar-input"
          value={askText}
          onChange={(e) => setAskText(e.target.value)}
          placeholder={t('page.ask_placeholder')}
        />
        <button
          type="button"
          className="btn btn--soft btn--sm"
          disabled={!askText.trim()}
          onClick={() => {
            // 契約メモ PR F12 §1 決定 1: 下の入力欄はページを離れずドロワーを開く。
            setChatInitialMessage(askText)
            setChatOpen(true)
            setAskText('')
          }}
        >
          {t('page.ask_submit')}
        </button>
      </div>
      <PageChatDrawer
        subject={{ kind: 'class', class_iri: classIri }}
        subjectKey={`k:${classIri}`}
        classIri={classIri}
        datasetId={entry.dataset_id}
        pageSummary={pageSummary}
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        initialMessage={chatInitialMessage}
        onCardAdded={() => {}}
      />
    </div>
  )
}
