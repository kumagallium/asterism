// 絞り込みのページ（試作 画面 2 の絞り込み）。契約メモ §6.3。`sets/resolve` の
// title を日本語の文に組み立てて見出しにし、`set_members`/`set_breakdown`/
// `set_count` を既定カードとして並べる。「条件を変える」で `SetForm` を同じ
// 画面に開く。
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { classSchema, datasetSummary, defaultCardsForSet, resolveSet, runCard, subjectKeyToString } from './cardsApi'
import type {
  CardRef,
  ClassSchema,
  CardToolResult,
  DatasetSummary,
  SetResolveResult,
  SetSpec,
  SubjectItem,
  SubjectKey,
} from './cardsApi'
import { CardDetail } from './CardDetail'
import { CardTile } from './CardTile'
import { ExportDialog } from './ExportDialog'
import './pages.css'
import { SetForm } from './SetForm'
import { formatSetSubtitle, formatSetTitle } from './setTitle'
import { addSubjectAndPersist } from './subjectStore'
// PR F4（ui-form 担当）が新設するモジュール。まだ存在しない間は import だけ
// 書いておき、統合段で繋ぐ（契約メモ PR F4 §2「無い間は import だけ書いて
// 統合で繋ぐ」）。
import { NewCardForm } from './NewCardForm'
import { removeCard, useCards } from './cardStore'
import { appendAddedCards, cardSpecToCardRef } from './SubjectPage'
import { ViewpointStrip } from './ViewpointStrip'

/** `App.tsx`（ui-rail）の実 `navigate` を汎用に受ける（`PlaceView.tsx` の
 *  `PlaceNavigateFn` と同じ理由 — `Route` を直接 import すると循環になる）。 */
export type DatasetNavigateFn = (route: { tab: string; [key: string]: unknown }) => void

export interface SetPageProps {
  /** 既存の絞り込みを開くとき（`spec`/`setId` が揃っている通常モード）。 */
  setId?: string
  spec?: SetSpec | null
  /** 新規作成モード（契約メモ §2.3・§2.4「条件で集める」）。`spec` がまだ無い —
   *  `class` が無ければ `datasetSummary` の種類から選ばせる 1 段を先に出す。 */
  newFor?: { datasetId: string; classIri?: string }
  /** `newFor` のときの画面遷移。`App.tsx`（ui-rail）の実 `navigate` を汎用に
   *  受ける（`PlaceView.tsx`/`DatasetPage.tsx` と同じ理由）。 */
  navigate?: DatasetNavigateFn
  /** URL の `…/c/<card_id>` — 指定があればカード詳細を表示する。 */
  cardId?: string
  onSelectCard: (cardId: string) => void
  onCloseCard: () => void
  onOpenSubject: (iri: string) => void
  /** 「条件を変える」で新しい絞り込みが確定したとき（set_id が変わる）。
   *  棚への追加・画面遷移（新しい set_id への移動）は呼び出し側の責任。 */
  onFiltersChanged: (result: SetResolveResult) => void
  onAsk: (question: string) => void
  onEditDefinition: (datasetId: string) => void
  /** パンくずの「<データセットの名前>」から（契約メモ §2.3）。 */
  onOpenDataset?: (datasetId: string) => void
}

/** 絞り込みの読み込み結果。`specKey` で紐づけ、then/catch でだけ書き込む —
 *  effect の本体で同期的に setState しない（react-hooks/set-state-in-effect。
 *  ProvenanceTrace.tsx／CardTile.tsx と同じ流儀）。 */
interface SetLoadState {
  specKey: string
  resolved: SetResolveResult | null
  cards: CardRef[] | null
  error: boolean
}

const EMPTY_SET_LOAD: SetLoadState = { specKey: '', resolved: null, cards: null, error: false }

/** `set_count` の結果から件数（role: 'value'）を拾う。行のキーは `result.item`
 *  の**キー名**であって `ItemSpec.var` ではない（`defaultView.ts` の
 *  KeyedItem コメント参照）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（FirstScreen.tsx と同じ理由）
export function valueFromCountResult(result: CardToolResult): number | null {
  const valueKey = Object.entries(result.item).find(([, spec]) => spec.role === 'value')?.[0]
  const value = valueKey ? result.items[0]?.[valueKey] : undefined
  return typeof value === 'number' ? value : null
}

export function SetPage({
  setId,
  spec,
  newFor,
  navigate,
  cardId,
  onSelectCard,
  onCloseCard,
  onOpenSubject,
  onFiltersChanged,
  onAsk,
  onEditDefinition,
  onOpenDataset,
}: SetPageProps) {
  const { t } = useTranslation('cards')
  const [loaded, setLoaded] = useState<SetLoadState>(EMPTY_SET_LOAD)
  const [totalState, setTotalState] = useState<{ specKey: string; total: number | null }>({
    specKey: '',
    total: null,
  })
  const [askText, setAskText] = useState('')
  const [exporting, setExporting] = useState(false)
  const specKey = spec ? JSON.stringify(spec) : ''

  // specKey が変わったら「条件を変える」フォームを閉じる（React の「prop が
  // 変わったら state を調整する」パターン — effect を使わない）。
  const [editingFor, setEditingFor] = useState(specKey)
  const [editing, setEditing] = useState(false)
  if (editingFor !== specKey) {
    setEditingFor(specKey)
    setEditing(false)
  }

  // specKey が変わったら「グラフを足す」フォームも閉じる（同じ「prop が
  // 変わったら state を調整する」パターン）。
  const [addingCardFor, setAddingCardFor] = useState(specKey)
  const [addingCard, setAddingCard] = useState(false)
  if (addingCardFor !== specKey) {
    setAddingCardFor(specKey)
    setAddingCard(false)
  }

  // 新規作成モード（契約メモ §2.3・§2.4）専用の状態: class を選ぶ段・作成後の
  // dataset_label/origin 用にデータセット要約を読む。
  const [newSummaryState, setNewSummaryState] = useState<{ datasetId: string; summary: DatasetSummary | null }>({
    datasetId: '',
    summary: null,
  })
  const [newClassIri, setNewClassIri] = useState<string | undefined>(newFor?.classIri)
  // newFor.classIri が prop 側で変わったら追随する（同じく「prop が変わったら
  // state を調整する」パターン）。
  const [newClassFor, setNewClassFor] = useState(newFor?.classIri)
  if (newClassFor !== newFor?.classIri) {
    setNewClassFor(newFor?.classIri)
    setNewClassIri(newFor?.classIri)
  }

  useEffect(() => {
    if (!newFor) return
    let cancelled = false
    datasetSummary(newFor.datasetId)
      .then((s) => {
        if (!cancelled) setNewSummaryState({ datasetId: newFor.datasetId, summary: s })
      })
      .catch(() => {
        if (!cancelled) setNewSummaryState({ datasetId: newFor.datasetId, summary: null })
      })
    return () => {
      cancelled = true
    }
    // newFor.classIri は class 選択の結果でしかない — 要約の再取得は datasetId
    // が変わったときだけでよい。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newFor?.datasetId])

  // パンくず「<データセットの名前>」用（契約メモ §2.3）: class_schema から
  // dataset_id・dataset_label を引く。`dataset_label` はまだ cardsApi.ts の
  // 型に無いため、ローカルに拡張して読む（統合段で api が足す・契約メモ §3.3）。
  const [datasetRef, setDatasetRef] = useState<{
    classIri: string
    datasetId: string | null
    datasetLabel: string | null
  }>({ classIri: '', datasetId: null, datasetLabel: null })

  useEffect(() => {
    if (!spec) return
    let cancelled = false
    classSchema(spec.class)
      .then((schema) => {
        if (cancelled || !schema) return
        const withLabel = schema as ClassSchema & { dataset_label?: string | null }
        setDatasetRef({
          classIri: spec.class,
          datasetId: withLabel.dataset_id,
          datasetLabel: withLabel.dataset_label ?? null,
        })
      })
      .catch(() => {
        // パンくずの補助情報 — 読めなくても本文の表示は変えない。
      })
    return () => {
      cancelled = true
    }
    // spec の中身は spec.class（この effect が使う唯一の値）で表せる。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec?.class])

  useEffect(() => {
    if (!spec) return
    let cancelled = false
    Promise.all([resolveSet(spec), defaultCardsForSet(spec)])
      .then(([r, c]) => {
        if (cancelled) return
        setLoaded({ specKey, resolved: r, cards: c, error: false })
      })
      .catch(() => {
        if (!cancelled) setLoaded({ specKey, resolved: null, cards: null, error: true })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specKey])

  const isCurrent = loaded.specKey === specKey
  const resolved = isCurrent ? loaded.resolved : null
  const cards = isCurrent ? loaded.cards : null
  const loadError = isCurrent && loaded.error

  // 見出しの「N 件から」— set_count は必ず含まれる既定カード。
  useEffect(() => {
    if (!cards || !spec) return
    const countCard = cards.find((c) => c.tool === 'set_count')
    if (!countCard) return
    let cancelled = false
    runCard({ kind: 'set', spec }, countCard.tool, countCard.params)
      .then((r) => {
        if (cancelled) return
        setTotalState({ specKey, total: valueFromCountResult(r) })
      })
      .catch(() => {
        // 見出しの補助数値なのでエラーは静かに無視する。
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cards, specKey])

  const total = totalState.specKey === specKey ? totalState.total : null

  // spec の中身は specKey（JSON 文字列）で表せる — spec 自体はレンダリングの
  // たびに参照が変わりうるので依存に入れない（specKey が同じなら中身も同じ）。
  // `newFor` モード（spec がまだ無い）では subjectKey は描画に使わず下で
  // return するため、ここでの `as SetSpec` は安全（SubjectKey.spec は必須）。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const subjectKey: SubjectKey = useMemo(() => ({ kind: 'set' as const, set_id: setId ?? '', spec: (spec ?? undefined) as SetSpec }), [setId, specKey])
  const title = useMemo(() => (resolved ? formatSetTitle(resolved.title, t) : null), [resolved, t])

  // 足したカード（cardStore・PR F4）。既定カードとは独立に持ち、描画のたびに
  // 既定の後ろへ並べる（`appendAddedCards`）。newFor モード（spec がまだ無い）
  // では subjectKey の set_id が空文字のままだが、cards は使わない画面なので
  // 実害はない。
  const subjectKeyStr = subjectKeyToString(subjectKey)
  const addedCardSpecs = useCards(subjectKeyStr)
  const addedCardRefs = useMemo(() => addedCardSpecs.map(cardSpecToCardRef), [addedCardSpecs])
  const displayCards = useMemo(
    () => (cards ? appendAddedCards(cards, addedCardRefs) : null),
    [cards, addedCardRefs],
  )
  // ViewpointStrip（PR F6）を既定カードと足したカードのあいだに置くため、
  // `displayCards` を並び順のまま二分する（`appendAddedCards` は既定を先頭に、
  // 重複しない足したカードを後ろに並べる）。
  const defaultCardIds = useMemo(() => new Set((cards ?? []).map((c) => c.card_id)), [cards])
  const displayDefaultCards = useMemo(
    () => (displayCards ?? []).filter((c) => defaultCardIds.has(c.card_id)),
    [displayCards, defaultCardIds],
  )
  const displayAddedCards = useMemo(
    () => (displayCards ?? []).filter((c) => !defaultCardIds.has(c.card_id)),
    [displayCards, defaultCardIds],
  )

  // ---- 新規作成モード（契約メモ §2.3・§2.4）: spec がまだ無い ----------------
  if (newFor && !spec) {
    const summaryForNew = newSummaryState.datasetId === newFor.datasetId ? newSummaryState.summary : null
    if (!newClassIri) {
      if (!summaryForNew) return <p className="ds-empty-note">{t('page.loading')}</p>
      return (
        <div className="cardpage-body">
          <h2 className="cardpage-title">{t('dataset.collect')}</h2>
          <div className="cardpage-setform">
            <p className="cardpage-setform-label">{t('dataset.by_kind')}</p>
            <div className="cardpage-setform-choices">
              {summaryForNew.classes.map((c) => (
                <button
                  key={c.class_iri}
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => setNewClassIri(c.class_iri)}
                >
                  {c.label}
                </button>
              ))}
            </div>
            <div className="cardpage-setform-actions">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => navigate?.({ tab: 'cards', datasetPageId: newFor.datasetId })}
              >
                {t('setform.cancel')}
              </button>
            </div>
          </div>
        </div>
      )
    }
    return (
      <div className="cardpage-body">
        <h2 className="cardpage-title">{t('dataset.collect')}</h2>
        <SetForm
          classIri={newClassIri}
          onCancel={() => navigate?.({ tab: 'cards', datasetPageId: newFor.datasetId })}
          onSubmit={(result) => {
            const item: SubjectItem = {
              kind: 'set',
              id: result.set_id,
              label: formatSetTitle(result.title, t),
              class_label: result.title.class_label,
              source: summaryForNew?.origin === 'own' ? 'own' : 'open',
              card_count: null,
              match: null,
              subject_key: `s:${result.set_id}`,
              spec: result.spec,
              created_at: new Date().toISOString(),
              ...(summaryForNew ? { dataset_id: newFor.datasetId, dataset_label: summaryForNew.label } : {}),
            } as SubjectItem
            addSubjectAndPersist(item)
            navigate?.({ tab: 'cards', subjectKey: `s:${result.set_id}` })
          }}
        />
      </div>
    )
  }

  const breadcrumbDatasetLabel = spec && datasetRef.classIri === spec.class ? datasetRef.datasetLabel : null
  const breadcrumbDatasetId = spec && datasetRef.classIri === spec.class ? datasetRef.datasetId : null

  if (cardId) {
    if (!displayCards) return <p className="ds-empty-note">{t('page.loading')}</p>
    const selectedCard = displayCards.find((c) => c.card_id === cardId)
    if (!selectedCard) {
      return (
        <div className="cardpage-body">
          <p className="ds-empty-note">{t('detail.not_found')}</p>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCloseCard}>
            {t('detail.back')}
          </button>
        </div>
      )
    }
    const isAddedCard = addedCardRefs.some((c) => c.card_id === selectedCard.card_id)
    return (
      <CardDetail
        subject={subjectKey}
        breadcrumbLabel={title ?? resolved?.title.class_label ?? setId ?? ''}
        card={selectedCard}
        onBack={onCloseCard}
        onAsk={onAsk}
        onEditDefinition={onEditDefinition}
        isAddedCard={isAddedCard}
        onRemoveCard={isAddedCard ? () => removeCard(subjectKeyStr, selectedCard.card_id) : undefined}
      />
    )
  }

  if (loadError) return <p className="ds-empty-note">{t('render_error')}</p>
  if (!resolved || !cards || !spec) return <p className="ds-empty-note">{t('page.loading')}</p>

  const label = title ?? resolved.title.class_label

  return (
    <div className="cardpage-body">
      <div className="cardpage-head">
        <div>
          {breadcrumbDatasetLabel && breadcrumbDatasetId && (
            <div className="cardpage-crumb">
              <button
                type="button"
                className="link-btn"
                onClick={() => onOpenDataset?.(breadcrumbDatasetId)}
              >
                {breadcrumbDatasetLabel}
              </button>
              {' › '}
              {label}
            </div>
          )}
          <h2 className="cardpage-title">
            {label}
            <small className="cardpage-subhead">{formatSetSubtitle(resolved.title.class_label, total, t)}</small>
          </h2>
        </div>
        <div className="cardpage-head-actions">
          {breadcrumbDatasetId && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setAddingCard((v) => !v)}>
              {t('newcard.button')}
            </button>
          )}
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={(displayCards ?? cards).length === 0}
            onClick={() => setExporting(true)}
          >
            {t('page.export_button')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing((v) => !v)}>
            {t('set.edit_filters_button')}
          </button>
        </div>
      </div>
      {addingCard && breadcrumbDatasetId && (
        <NewCardForm
          subject={{ kind: 'set', spec }}
          subjectKey={subjectKeyStr}
          datasetId={breadcrumbDatasetId}
          onCreated={() => setAddingCard(false)}
          onCancel={() => setAddingCard(false)}
        />
      )}
      {editing && (
        <SetForm
          classIri={spec.class}
          initialSpec={spec}
          onCancel={() => setEditing(false)}
          onSubmit={(result) => {
            setEditing(false)
            onFiltersChanged(result)
          }}
        />
      )}
      <div className="cardpage-grid">
        {(displayCards ? displayDefaultCards : cards).map((card) => (
          <CardTile
            key={card.card_id}
            subject={subjectKey}
            card={card}
            onOpenDetail={onSelectCard}
            onOpenSubject={onOpenSubject}
          />
        ))}
      </div>
      <ViewpointStrip
        subject={{ kind: 'set', spec }}
        subjectKey={subjectKeyStr}
        kind="set"
        classIri={spec.class}
        where={spec.where}
        sourceScope={spec.source_scope}
      />
      {displayCards && displayAddedCards.length > 0 && (
        <div className="cardpage-grid">
          {displayAddedCards.map((card) => (
            <CardTile
              key={card.card_id}
              subject={subjectKey}
              card={card}
              onOpenDetail={onSelectCard}
              onOpenSubject={onOpenSubject}
            />
          ))}
        </div>
      )}
      {exporting && (
        <ExportDialog subject={subjectKey} cards={displayCards ?? cards} onClose={() => setExporting(false)} />
      )}
      <div className="cardpage-bar">
        <span className="cardpage-bar-who">{t('page.ask_who', { label })}</span>
        <input
          className="cardpage-bar-input"
          value={askText}
          onChange={(e) => setAskText(e.target.value)}
          placeholder={t('ask_placeholder')}
        />
        <button
          type="button"
          className="btn btn--soft btn--sm"
          disabled={!askText.trim()}
          onClick={() => {
            onAsk(t('ask.compose', { label, text: askText }))
            setAskText('')
          }}
        >
          {t('page.ask_submit')}
        </button>
      </div>
    </div>
  )
}
