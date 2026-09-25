// 1 件のページ（試作 画面 2 の 1 件）。契約メモ §6.3。既定カードを決定論で
// 生成し（`GET /api/subjects/default-cards`）、`cards/run` を並列に呼んで
// `defaultViewFor` → PR B の描画器で並べる。カード押下でカード詳細
// （`CardDetail.tsx`）へ、最下部の 1 行は「聞く」でこのページの主語つきで
// Ask へ渡す（新規の会話機構は作らない）。
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { defaultCardsForSubject, linkingKinds, resolveSubject, runCard, subjectKeyToString } from './cardsApi'
import type { CardRef, CardToolResult, LinkingKind, SubjectResolveResult } from './cardsApi'
import { CardDetail } from './CardDetail'
import { CardTile } from './CardTile'
import { ExportDialog } from './ExportDialog'
// PR F12（ui-drawer 担当）が新設するモジュール。まだ存在しない間は import
// だけ書いておき、統合段で繋ぐ（契約メモ PR F12 §2「並列中の仮置き」）。
import { PageChatDrawer } from './PageChatDrawer'
import { removeCard, useCards } from './cardStore'
import type { CardSpec } from './cardStore'
import { subjectDisplayLabel } from './subjectLabel'
import { ViewpointStrip } from './ViewpointStrip'
import './pages.css'

/** ドロワー（`PageChatDrawer`）へ渡す「このページで読める値」の 1 行
 *  （契約メモ PR F12 §3 API `page.facts`）。K4: ラベルは人向けの文言、値は
 *  文字列に整形済み。 */
export interface PageChatFact {
  label: string
  value: string
}

/** ドロワーへ渡す 1 カードぶんの要約（契約メモ PR F12 §3 API `page.cards`）。
 *  `rows` は「先頭 20 行・series は先頭と末尾」（契約メモ PR F12 §5 実装順(1)）。 */
export interface PageChatCardSummary {
  title: string
  output_kind: CardToolResult['output_kind']
  rows: CardToolResult['items']
}

export interface PageChatSummary {
  facts: PageChatFact[]
  cards: PageChatCardSummary[]
}

/** 1 枚のカード結果 → ドロワー用の要約（純関数）。series は行数が多いと
 *  折れ線の全点を送る意味が薄い一方、両端（開始/終了）は根拠として要る
 *  ことが多いため先頭と末尾の 2 行だけに絞る。それ以外の出口は先頭 20 行。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（sourceLabelsFrom と同じ理由）
export function summarizeCardForChat(title: string, result: CardToolResult): PageChatCardSummary {
  const rows =
    result.output_kind === 'series' && result.items.length > 2
      ? [result.items[0], result.items[result.items.length - 1]]
      : result.items.slice(0, 20)
  return { title, output_kind: result.output_kind, rows }
}

/** ui-form の `cardStore.useCards` が返す 1 件（O19 CardSpec）を、既定カードと
 *  同じ並び物（`CardRef`）に変換する。契約メモ PR F4 §1-5「card_id / title /
 *  tool / params / output_kind をそのまま」。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（sourceLabelsFrom と同じ理由）
export function cardSpecToCardRef(spec: CardSpec): CardRef {
  return {
    card_id: spec.card_id,
    title: spec.title,
    tool: spec.tool,
    params: spec.params,
    output_kind: spec.output_kind,
  }
}

/** 既定カードの後ろに、足したカードを並べる（契約メモ PR F4 §1-5）。同じ
 *  card_id が既定側にすでにあれば足した方を捨てる（決定論の card_id が衝突
 *  するのは既定と同じ操作を再現したときだけなので、既定を優先して二重表示を
 *  防ぐ）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（sourceLabelsFrom と同じ理由）
export function appendAddedCards(defaultCards: CardRef[], addedCards: CardRef[]): CardRef[] {
  const defaultIds = new Set(defaultCards.map((c) => c.card_id))
  return [...defaultCards, ...addedCards.filter((c) => !defaultIds.has(c.card_id))]
}

/** `subject_sources`（output_kind: breakdown）の `category`（= データセット名
 *  ＋版）を出どころのラベルとして拾う。件数は使わない — 見出しの「出典 N 報」
 *  は出どころの**数**（items 件数）であって三つ組数ではない（§1(b)）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（FirstScreen.tsx と同じ理由）
export function sourceLabelsFrom(result: CardToolResult): string[] {
  // 行（`result.items` の 1 件）のキーは `result.item` の**キー名**であって
  // `ItemSpec.var`（SPARQL 側の変数名）ではない — 宣言ツールでは両者が
  // 異なりうる（`defaultView.ts` の KeyedItem コメント参照）。
  const categoryKey = Object.entries(result.item).find(([, spec]) => spec.role === 'category')?.[0]
  if (!categoryKey) return []
  return result.items
    .map((row) => row[categoryKey])
    .filter((v): v is string => typeof v === 'string' && v.length > 0)
}

export interface SubjectPageProps {
  iri: string
  /** URL の `…/c/<card_id>` — 指定があればカード詳細を表示する。 */
  cardId?: string
  onSelectCard: (cardId: string) => void
  onCloseCard: () => void
  onOpenSubject: (iri: string) => void
  onAsk: (question: string) => void
  onEditDefinition: (datasetId: string) => void
  /** パンくずの「<種類の名前>」から（契約メモ contract_pr_f9.md §1 決定 5・
   *  §5 実装順(3)「パンくずを『種類 › 名前』に」）。`class_label` が無ければ
   *  パンくず自体を出さない（K4: class_iri からは組み立てない）ので呼ばれない。
   *  旧「<データセットの名前>」パンくず（`onOpenDataset`）はこの画面からは
   *  撤去した（データセットのページは子ナビから外れ、種類のページの「データの
   *  定義を見る・直す」からだけ入る枠になったため — 決定 9）。 */
  onOpenClass?: (classIri: string) => void
}

interface FactsSummary {
  factsCount: number | null
  /** 出どころの数（`subject_sources` の items 件数。三つ組数ではない）。 */
  sourcesCount: number | null
  /** `resolve` に `dataset_label` が無いときのフォールバック（出どころのラベル）。 */
  sourceLabels: string[]
}

/** 1 件の読み込み結果。`iri` で紐づけ、then/catch でだけ書き込む — effect の
 *  本体で同期的に setState しない（react-hooks/set-state-in-effect。
 *  ProvenanceTrace.tsx／CardTile.tsx と同じ流儀）。 */
interface SubjectLoadState {
  iri: string
  resolved: SubjectResolveResult | null
  cards: CardRef[] | null
  error: boolean
}

const EMPTY_LOAD: SubjectLoadState = { iri: '', resolved: null, cards: null, error: false }
const EMPTY_SUMMARY: FactsSummary = { factsCount: null, sourcesCount: null, sourceLabels: [] }

export function SubjectPage({
  iri,
  cardId,
  onSelectCard,
  onCloseCard,
  onOpenSubject,
  onAsk,
  onEditDefinition,
  onOpenClass,
}: SubjectPageProps) {
  const { t } = useTranslation('cards')
  const [loaded, setLoaded] = useState<SubjectLoadState>(EMPTY_LOAD)
  const [hiddenCardIds, setHiddenCardIds] = useState<Set<string>>(new Set())
  const [summaryState, setSummaryState] = useState<{ iri: string; summary: FactsSummary }>({
    iri: '',
    summary: EMPTY_SUMMARY,
  })
  const [askText, setAskText] = useState('')
  const [exporting, setExporting] = useState(false)
  // 「＋ 観点を足す」・下の入力欄はどちらもドロワー（PageChatDrawer・PR F12）を
  // 開く（契約メモ §1 決定 1・6）。フォーム単体（NewCardForm）はドロワーの中に
  // 埋め込む（ui-drawer 担当）ので、このページ自身はもう開閉を持たない。
  const [chatOpen, setChatOpen] = useState(false)
  const [chatInitialMessage, setChatInitialMessage] = useState<string | undefined>(undefined)
  const [cardResultsState, setCardResultsState] = useState<{ key: string; results: Record<string, CardToolResult> }>({
    key: '',
    results: {},
  })

  // 足したカード（cardStore・PR F4）。既定カードとは独立に持ち、描画のたびに
  // 既定の後ろへ並べる（`appendAddedCards`）。
  const subjectRef = { kind: 'individual' as const, iri }
  const subjectKeyStr = subjectKeyToString(subjectRef)
  const addedCardSpecs = useCards(subjectKeyStr)
  const addedCardRefs = useMemo(() => addedCardSpecs.map(cardSpecToCardRef), [addedCardSpecs])

  // iri が変わったら「隠したカード」を描画時に忘れる（React の「prop が変わった
  // ら state を調整する」パターン — effect を使わない）。
  const [hiddenFor, setHiddenFor] = useState(iri)
  if (hiddenFor !== iri) {
    setHiddenFor(iri)
    setHiddenCardIds(new Set())
    setChatOpen(false)
    setChatInitialMessage(undefined)
  }

  // 「観点」の帯（PR F6・ViewpointStrip）が使う `linkingKinds`（この 1 件を
  // 指す種類の候補）— 1 回だけ取る（契約メモ §5 実装順(4)「1 件のページは
  // linkingKinds を 1 回取って渡す」）。`NewCardForm` も同じ API を独自に
  // 叩くが、`NewCardForm` は F4（担当外・変更しない）のため、ここでは共有せず
  // 独立に取る — deviations 参照。
  const [linkingKindsState, setLinkingKindsState] = useState<{ iri: string; items: LinkingKind[] }>({
    iri: '',
    items: [],
  })
  useEffect(() => {
    let cancelled = false
    linkingKinds(iri)
      .then((items) => {
        if (!cancelled) setLinkingKindsState({ iri, items })
      })
      .catch(() => {
        if (!cancelled) setLinkingKindsState({ iri, items: [] })
      })
    return () => {
      cancelled = true
    }
  }, [iri])
  const linkingKindsForIri = linkingKindsState.iri === iri ? linkingKindsState.items : []

  useEffect(() => {
    let cancelled = false
    Promise.all([resolveSubject(iri), defaultCardsForSubject(iri)])
      .then(([subjectResult, cardList]) => {
        if (cancelled) return
        setLoaded({ iri, resolved: subjectResult, cards: cardList, error: false })
      })
      .catch(() => {
        if (!cancelled) setLoaded({ iri, resolved: null, cards: null, error: true })
      })
    return () => {
      cancelled = true
    }
  }, [iri])

  const isCurrent = loaded.iri === iri
  const resolved = isCurrent ? loaded.resolved : null
  const cards = isCurrent ? loaded.cards : null
  const loadError = isCurrent && loaded.error

  // 見出しの「事実 N 件・出典 M 報」— facts/sources は必ず含まれる既定カード
  // （決定 7）なので、その 2 本だけ個別に実行して数だけ拾う。カード本体の実行
  // 結果は CardTile 側が独立に持つ（ここでは二重管理しない）。
  useEffect(() => {
    if (!cards) return
    const facts = cards.find((c) => c.tool === 'subject_facts')
    const sources = cards.find((c) => c.tool === 'subject_sources')
    let cancelled = false
    Promise.all([
      facts ? runCard({ kind: 'individual', iri }, facts.tool, facts.params) : Promise.resolve(null),
      sources ? runCard({ kind: 'individual', iri }, sources.tool, sources.params) : Promise.resolve(null),
    ])
      .then(([factsResult, sourcesResult]) => {
        if (cancelled) return
        setSummaryState({
          iri,
          summary: {
            factsCount: factsResult ? factsResult.count : null,
            // 出どころの数 = items の件数（三つ組数の合計ではない・§1(b)）。
            sourcesCount: sourcesResult ? sourcesResult.items.length : null,
            sourceLabels: sourcesResult ? sourceLabelsFrom(sourcesResult) : [],
          },
        })
      })
      .catch(() => {
        // 見出しの補助数値なのでエラーは静かに無視する（カード本体側で個別に出る）。
      })
    return () => {
      cancelled = true
    }
  }, [cards, iri])

  const summary = summaryState.iri === iri ? summaryState.summary : EMPTY_SUMMARY

  const allCards = useMemo(
    () => (cards ? appendAddedCards(cards, addedCardRefs) : null),
    [cards, addedCardRefs],
  )

  // ドロワー（PageChatDrawer）が聞く／作るときの根拠にするカードの中身。
  // 開いているときだけ取りに行く（契約メモ PR F12 §1-2「並んでいるカードの
  // 結果」）— CardTile も同じ結果を独立に取るため二重に呼ぶことになるが、
  // CardTile（担当外）に結果を上げる経路が無いためここでは割り切る
  // （deviations 参照）。
  useEffect(() => {
    if (!chatOpen || !allCards) return
    const key = `${iri}\u0000${allCards.map((c) => c.card_id).join(',')}`
    if (cardResultsState.key === key) return
    let cancelled = false
    Promise.all(
      allCards.map((c) =>
        runCard({ kind: 'individual', iri }, c.tool, c.params)
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
  }, [chatOpen, allCards, iri, cardResultsState.key])

  const pageSummary: PageChatSummary = useMemo(() => {
    const facts: PageChatFact[] = []
    if (resolved?.class_label) facts.push({ label: t('pagechat.summary_class'), value: resolved.class_label })
    if (summary.factsCount !== null) facts.push({ label: t('pagechat.summary_facts'), value: String(summary.factsCount) })
    if (summary.sourcesCount !== null) facts.push({ label: t('pagechat.summary_sources'), value: String(summary.sourcesCount) })
    const cardsSummary = (allCards ?? []).flatMap((c) => {
      const r = cardResultsState.results[c.card_id]
      return r ? [summarizeCardForChat(c.title, r)] : []
    })
    return { facts, cards: cardsSummary }
  }, [resolved, summary, allCards, cardResultsState, t])

  const visibleCards = useMemo(
    () => (allCards ?? []).filter((c) => !hiddenCardIds.has(c.card_id)),
    [allCards, hiddenCardIds],
  )
  // ViewpointStrip（PR F6）を既定カードと足したカードのあいだに置くため、
  // `visibleCards` を並び順のまま二分する（`allCards` は `appendAddedCards`
  // が既定を先頭に、重複しない足したカードを後ろに並べたもの）。
  const defaultCardIds = useMemo(() => new Set((cards ?? []).map((c) => c.card_id)), [cards])
  const visibleDefaultCards = useMemo(
    () => visibleCards.filter((c) => defaultCardIds.has(c.card_id)),
    [visibleCards, defaultCardIds],
  )
  const visibleAddedCards = useMemo(
    () => visibleCards.filter((c) => !defaultCardIds.has(c.card_id)),
    [visibleCards, defaultCardIds],
  )

  function hideCard(id: string) {
    setHiddenCardIds((prev) => {
      if (prev.has(id)) return prev
      const next = new Set(prev)
      next.add(id)
      return next
    })
  }

  // K4: ラベルが無い IRI でも生の IRI を見せない（末尾の名前に落とす）。
  // カード詳細（パンくず・見出し・Ask）と一覧見出しの両方で同じ値を使う。
  const label = subjectDisplayLabel(resolved?.label, iri)

  if (cardId) {
    if (!allCards) return <p className="ds-empty-note">{t('page.loading')}</p>
    const selectedCard = allCards.find((c) => c.card_id === cardId)
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
        subject={{ kind: 'individual', iri }}
        breadcrumbLabel={label}
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
  if (!resolved || !cards) return <p className="ds-empty-note">{t('page.loading')}</p>
  if (!resolved.found) return <p className="ds-empty-note">{t('page.subject_not_found')}</p>

  // データセットの表示名 — id は決して出さない（K4）。resolve に
  // dataset_label があればそれ、無ければ subject_sources の出どころ
  // ラベル（= データセット名＋版）を使う（§1(a)）。
  const datasetLabel = resolved.dataset_label ?? summary.sourceLabels.join('・')

  return (
    <div className="cardpage-body">
      <div className="cardpage-head">
        <div>
          {resolved.class_label && resolved.class_iri && (
            <div className="cardpage-crumb">
              <button
                type="button"
                className="link-btn"
                onClick={() => onOpenClass?.(resolved.class_iri as string)}
              >
                {resolved.class_label}
              </button>
              {' › '}
              {label}
            </div>
          )}
          <h2 className="cardpage-title">
            {label}
            <small className="cardpage-subhead">
              {t('page.subject_meta', {
                dataset: datasetLabel,
                facts: summary.factsCount ?? 0,
                sources: summary.sourcesCount ?? 0,
              })}
            </small>
          </h2>
        </div>
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
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={visibleCards.length === 0}
            onClick={() => setExporting(true)}
          >
            {t('page.export_button')}
          </button>
        </div>
      </div>
      <div className="cardpage-grid">
        {visibleDefaultCards.map((card) => (
          <CardTile
            key={card.card_id}
            subject={{ kind: 'individual', iri }}
            card={card}
            onOpenDetail={onSelectCard}
            onOpenSubject={onOpenSubject}
            onFoundChange={(id, found) => {
              if (!found) hideCard(id)
            }}
          />
        ))}
      </div>
      <ViewpointStrip
        subject={subjectRef}
        subjectKey={subjectKeyStr}
        kind="individual"
        classIri={resolved.class_iri}
        iri={iri}
        linkingKinds={linkingKindsForIri}
      />
      {visibleAddedCards.length > 0 && (
        <div className="cardpage-grid">
          {visibleAddedCards.map((card) => (
            <CardTile
              key={card.card_id}
              subject={{ kind: 'individual', iri }}
              card={card}
              onOpenDetail={onSelectCard}
              onOpenSubject={onOpenSubject}
              onFoundChange={(id, found) => {
                if (!found) hideCard(id)
              }}
            />
          ))}
        </div>
      )}
      {exporting && (
        <ExportDialog
          subject={{ kind: 'individual', iri }}
          cards={visibleCards}
          onClose={() => setExporting(false)}
        />
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
        subject={subjectRef}
        subjectKey={subjectKeyStr}
        classIri={resolved.class_iri ?? undefined}
        datasetId={resolved.dataset_id ?? undefined}
        pageSummary={pageSummary}
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        initialMessage={chatInitialMessage}
        // `cardStore.useCards` は `useSyncExternalStore` 購読なので、ドロワーが
        // 内部で `addCard` を呼べば `addedCardRefs` は自動で更新される
        // （契約メモ PR F12 §5 実装順(1)「onCardAdded で足したカードの一覧を
        // 更新」は cardStore 側の購読で自動的に満たされる）。ここでは通知を
        // 受けるだけでよい。
        onCardAdded={() => {}}
      />
    </div>
  )
}
