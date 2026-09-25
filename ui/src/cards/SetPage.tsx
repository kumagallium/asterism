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
// PR F12（ui-drawer 担当）が新設するモジュール。まだ存在しない間は import
// だけ書いておき、統合段で繋ぐ（契約メモ PR F12 §2「並列中の仮置き」）。
import { PageChatDrawer } from './PageChatDrawer'
import { removeCard, useCards } from './cardStore'
import { appendAddedCards, cardSpecToCardRef, summarizeCardForChat } from './SubjectPage'
import type { PageChatFact, PageChatSummary } from './SubjectPage'
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
  /** パンくずの「<種類の名前>」から（契約メモ contract_pr_f9.md §1 決定 5・
   *  §5 実装順(3)）。旧「<データセットの名前>」パンくず（`onOpenDataset`）は
   *  この画面からは撤去した（SubjectPage.tsx と同じ理由 — 決定 9）。 */
  onOpenClass?: (classIri: string) => void
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
  onOpenClass,
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

  // specKey が変わったら会話ドロワー（PageChatDrawer・PR F12）も閉じる（同じ
  // 「prop が変わったら state を調整する」パターン）。「＋ 観点を足す」・下の
  // 入力欄はどちらもこのドロワーを開く（契約メモ §1 決定 1・6）。
  const [chatFor, setChatFor] = useState(specKey)
  const [chatOpen, setChatOpen] = useState(false)
  const [chatInitialMessage, setChatInitialMessage] = useState<string | undefined>(undefined)
  if (chatFor !== specKey) {
    setChatFor(specKey)
    setChatOpen(false)
    setChatInitialMessage(undefined)
  }
  const [cardResultsState, setCardResultsState] = useState<{ key: string; results: Record<string, CardToolResult> }>({
    key: '',
    results: {},
  })

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

  // パンくず「<種類の名前>」用（契約メモ contract_pr_f9.md §1 決定 5）:
  // class_schema から種類のラベル（`schema.label`）・dataset_id・dataset_label
  // を引く。`dataset_label` はまだ cardsApi.ts の型に無いため、ローカルに拡張
  // して読む（統合段で api が足す・契約メモ §3.3）。`datasetId`/`datasetLabel`
  // は「データの定義を見る・直す」を持つ画面が無いこのページでは今のところ
  // パンくず自体には使わないが、他の呼び出し元が要る可能性を潰さないため残す。
  const [datasetRef, setDatasetRef] = useState<{
    classIri: string
    classLabel: string | null
    datasetId: string | null
    datasetLabel: string | null
  }>({ classIri: '', classLabel: null, datasetId: null, datasetLabel: null })

  useEffect(() => {
    if (!spec) return
    let cancelled = false
    classSchema(spec.class)
      .then((schema) => {
        if (cancelled || !schema) return
        const withLabel = schema as ClassSchema & { dataset_label?: string | null }
        setDatasetRef({
          classIri: spec.class,
          classLabel: withLabel.label ?? null,
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

  // ドロワー（PageChatDrawer）が聞く／作るときの根拠にするカードの中身。
  // 開いているときだけ取りに行く（SubjectPage.tsx と同じ理由 — CardTile が
  // 独立に取る結果と二重になるが、CardTile（担当外）に結果を上げる経路が
  // 無いためここでは割り切る）。
  const cardResultsKey = useMemo(
    () => (displayCards && spec ? `${specKey}\u0000${displayCards.map((c) => c.card_id).join(',')}` : null),
    [displayCards, spec, specKey],
  )
  useEffect(() => {
    if (!chatOpen || !displayCards || !spec || !cardResultsKey) return
    const key = cardResultsKey
    if (cardResultsState.key === key) return
    let cancelled = false
    Promise.all(
      displayCards.map((c) =>
        runCard({ kind: 'set', spec }, c.tool, c.params)
          .then((r): [string, CardToolResult | null] => [c.card_id, r])
          .catch((): [string, CardToolResult | null] => [c.card_id, null]),
      ),
    ).then((entries) => {
      if (cancelled) return
      const results: Record<string, CardToolResult> = {}
      for (const [id, r] of entries) if (r) results[id] = r
      setCardResultsState({ key, results })
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatOpen, displayCards, specKey, cardResultsKey, cardResultsState.key])
  // 下の入力欄からの 1 通目は、並んでいるカードの結果を取り込んでから送る
  // （SubjectPage.tsx と同じ競合の対策）。
  // key が null なのはカード一覧が読めなかったとき（ページ自体が誤り表示）
  // だけなので、そのときは待たずに送る（永久に送れない穴を作らない）。
  const cardResultsReady = cardResultsKey === null || cardResultsState.key === cardResultsKey

  const pageSummary: PageChatSummary = useMemo(() => {
    const facts: PageChatFact[] = []
    if (resolved) facts.push({ label: t('pagechat.summary_class'), value: resolved.title.class_label })
    if (total !== null) facts.push({ label: t('pagechat.summary_total'), value: String(total) })
    const cardsSummary = (displayCards ?? []).flatMap((c) => {
      const r = cardResultsState.results[c.card_id]
      return r ? [summarizeCardForChat(c, r)] : []
    })
    return { facts, cards: cardsSummary }
  }, [resolved, total, displayCards, cardResultsState, t])

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

  // `breadcrumbDatasetId` は表示用ではなく、NewCardForm が要る `datasetId`
  // prop の出所（この 1 段下の「＋ 観点を足す」の描画ゲート）としてだけ残る
  // ——パンくずの表示自体は種類（`breadcrumbClassLabel`）に置き換えた
  // （契約メモ contract_pr_f9.md §1 決定 5）。
  const breadcrumbDatasetId = spec && datasetRef.classIri === spec.class ? datasetRef.datasetId : null
  const breadcrumbClassLabel = spec && datasetRef.classIri === spec.class ? datasetRef.classLabel : null

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
          {breadcrumbClassLabel && (
            <div className="cardpage-crumb">
              <button
                type="button"
                className="link-btn"
                onClick={() => onOpenClass?.(spec.class)}
              >
                {breadcrumbClassLabel}
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
          placeholder={t('page.ask_placeholder')}
        />
        <button
          type="button"
          className="btn btn--soft btn--sm"
          disabled={!askText.trim()}
          onClick={() => {
            // 契約メモ PR F12 §1 決定 1: 下の入力欄はページを離れずドロワーを
            // 開く（旧: `onAsk` で `#/ask` へ遷移）。
            setChatInitialMessage(askText)
            setChatOpen(true)
            setAskText('')
          }}
        >
          {t('page.ask_submit')}
        </button>
      </div>
      <PageChatDrawer
        subject={{ kind: 'set', spec }}
        subjectKey={subjectKeyStr}
        classIri={spec.class}
        datasetId={breadcrumbDatasetId ?? undefined}
        pageSummary={pageSummary}
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        initialMessage={cardResultsReady ? chatInitialMessage : undefined}
        // cardStore.useCards の購読で一覧は自動更新される（SubjectPage.tsx と
        // 同じ理由）。
        onCardAdded={() => {}}
      />
    </div>
  )
}
