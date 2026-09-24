// 絞り込みのページ（試作 画面 2 の絞り込み）。契約メモ §6.3。`sets/resolve` の
// title を日本語の文に組み立てて見出しにし、`set_members`/`set_breakdown`/
// `set_count` を既定カードとして並べる。「条件を変える」で `SetForm` を同じ
// 画面に開く。
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { defaultCardsForSet, resolveSet, runCard } from './cardsApi'
import type { CardRef, CardToolResult, SetResolveResult, SetSpec } from './cardsApi'
import { CardDetail } from './CardDetail'
import { CardTile } from './CardTile'
import { ExportDialog } from './ExportDialog'
import './pages.css'
import { SetForm } from './SetForm'
import { formatSetSubtitle, formatSetTitle } from './setTitle'

export interface SetPageProps {
  setId: string
  spec: SetSpec
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
  cardId,
  onSelectCard,
  onCloseCard,
  onOpenSubject,
  onFiltersChanged,
  onAsk,
  onEditDefinition,
}: SetPageProps) {
  const { t } = useTranslation('cards')
  const [loaded, setLoaded] = useState<SetLoadState>(EMPTY_SET_LOAD)
  const [totalState, setTotalState] = useState<{ specKey: string; total: number | null }>({
    specKey: '',
    total: null,
  })
  const [askText, setAskText] = useState('')
  const [exporting, setExporting] = useState(false)
  const specKey = JSON.stringify(spec)

  // specKey が変わったら「条件を変える」フォームを閉じる（React の「prop が
  // 変わったら state を調整する」パターン — effect を使わない）。
  const [editingFor, setEditingFor] = useState(specKey)
  const [editing, setEditing] = useState(false)
  if (editingFor !== specKey) {
    setEditingFor(specKey)
    setEditing(false)
  }

  useEffect(() => {
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
    if (!cards) return
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const subjectKey = useMemo(() => ({ kind: 'set' as const, set_id: setId, spec }), [setId, specKey])
  const title = useMemo(() => (resolved ? formatSetTitle(resolved.title, t) : null), [resolved, t])

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
        subject={subjectKey}
        breadcrumbLabel={title ?? resolved?.title.class_label ?? setId}
        card={selectedCard}
        onBack={onCloseCard}
        onAsk={onAsk}
        onEditDefinition={onEditDefinition}
      />
    )
  }

  if (loadError) return <p className="ds-empty-note">{t('render_error')}</p>
  if (!resolved || !cards) return <p className="ds-empty-note">{t('page.loading')}</p>

  const label = title ?? resolved.title.class_label

  return (
    <div className="cardpage-body">
      <div className="cardpage-head">
        <div>
          <h2 className="cardpage-title">
            {label}
            <small className="cardpage-subhead">{formatSetSubtitle(resolved.title.class_label, total, t)}</small>
          </h2>
        </div>
        <div className="cardpage-head-actions">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={cards.length === 0}
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
        {cards.map((card) => (
          <CardTile
            key={card.card_id}
            subject={subjectKey}
            card={card}
            onOpenDetail={onSelectCard}
            onOpenSubject={onOpenSubject}
          />
        ))}
      </div>
      {exporting && <ExportDialog subject={subjectKey} cards={cards} onClose={() => setExporting(false)} />}
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
