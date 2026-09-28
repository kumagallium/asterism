// 1 件のページ（試作 画面 2 の 1 件）。契約メモ §6.3。既定カードを決定論で
// 生成し（`GET /api/subjects/default-cards`）、`cards/run` を並列に呼んで
// `defaultViewFor` → PR B の描画器で並べる。カード押下でカード詳細
// （`CardDetail.tsx`）へ、最下部の 1 行は「聞く」でこのページの主語つきで
// Ask へ渡す（新規の会話機構は作らない）。
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { defaultCardsForSubject, resolveSubject, runCard } from './cardsApi'
import type { CardRef, CardToolResult, SubjectResolveResult } from './cardsApi'
import { CardDetail } from './CardDetail'
import { CardTile } from './CardTile'
import { ExportDialog } from './ExportDialog'
import { subjectDisplayLabel } from './subjectLabel'
import './pages.css'


/** `subject_sources`（output_kind: breakdown）の `category`（= データセット名
 *  ＋版）を出どころのラベルとして拾う。件数は使わない — 見出しの「出典 N 報」
 *  は出どころの**数**（items 件数）であって三つ組数ではない（§1(b)）。 */
function sourceLabelsFrom(result: CardToolResult): string[] {
  const categoryVar = Object.values(result.item).find((i) => i.role === 'category')?.var
  if (!categoryVar) return []
  return result.items
    .map((row) => row[categoryVar])
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

  // iri が変わったら「隠したカード」を描画時に忘れる（React の「prop が変わった
  // ら state を調整する」パターン — effect を使わない）。
  const [hiddenFor, setHiddenFor] = useState(iri)
  if (hiddenFor !== iri) {
    setHiddenFor(iri)
    setHiddenCardIds(new Set())
  }

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

  const visibleCards = useMemo(() => (cards ?? []).filter((c) => !hiddenCardIds.has(c.card_id)), [cards, hiddenCardIds])

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
    if (!cards) return <p className="ds-empty-note">{t('page.loading')}</p>
    const selectedCard = cards.find((c) => c.card_id === cardId)
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
    return (
      <CardDetail
        subject={{ kind: 'individual', iri }}
        breadcrumbLabel={label}
        card={selectedCard}
        onBack={onCloseCard}
        onAsk={onAsk}
        onEditDefinition={onEditDefinition}
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
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={visibleCards.length === 0}
          onClick={() => setExporting(true)}
        >
          {t('page.export_button')}
        </button>
      </div>
      <div className="cardpage-grid">
        {visibleCards.map((card) => (
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
