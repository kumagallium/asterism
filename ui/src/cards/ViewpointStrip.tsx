// 「同じデータで使われている観点」の帯（契約メモ contract_pr_f6.md §1-2・
// §1-4）。既定カードと足したカードのあいだに置く（呼び出し側 —
// SubjectPage.tsx/SetPage.tsx）。観点は `viewpoints.ts` の純関数で毎回
// 派生させる（保存しない）。チップを押すと、このページの条件を付けて
// `set_measure` を 1 回実行（F4 の `NewCardForm` と同じ `runCard`）→
// 成功したら `cardStore.addCard`（同じ `CardSpec`・同じ `card_id` 規則）。
// 成功後は `cardStore` の更新が `existingViewpointIds` に伝わり、そのチップは
// 帯から自然に消える（別に「消した」状態を持たない）。
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { CardRunSubject, CardSpec } from './cardsApi'
import { runCard } from './cardsApi'
import type { LinkingKind, MeasureWhereClause, SetSpec } from './cardsApi'
import { addCard, useAllCards, useCards } from './cardStore'
import { cardId } from './measureCardFields'
import {
  applicableViewpoints,
  paramsForPage,
  viewpointId,
  viewpointsFrom,
  viewpointTitle,
  type ViewpointPage,
} from './viewpoints'
import './viewpoints.css'

export interface ViewpointStripProps {
  /** カードを実行する主語（`runCard`/`CardSpec.subject_key` の両方に使う —
   *  `NewCardForm` と同じ）。 */
  subject: CardRunSubject
  subjectKey: string
  kind: 'individual' | 'set'
  /** 一覧のページ: `spec.class`。1 件のページ: この 1 件自身の種類（わかって
   *  いなければ `null`）。 */
  classIri: string | null
  /** 一覧のページだけ。 */
  where?: MeasureWhereClause[]
  sourceScope?: SetSpec['source_scope']
  /** 1 件のページだけ。呼び出し側が 1 回だけ `linkingKinds` を取って渡す。 */
  iri?: string
  linkingKinds?: LinkingKind[]
}

export function ViewpointStrip({ subject, subjectKey, kind, classIri, where, sourceScope, iri, linkingKinds }: ViewpointStripProps) {
  const { t } = useTranslation('cards')
  const allCards = useAllCards()
  const ownCards = useCards(subjectKey)
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [failedFor, setFailedFor] = useState<{ subjectKey: string; failed: boolean }>({
    subjectKey,
    failed: false,
  })
  // subjectKey が変わったら失敗表示を忘れる（React の「prop が変わったら
  // state を調整する」パターン — effect を使わない。SubjectPage.tsx などと
  // 同じ流儀）。
  if (failedFor.subjectKey !== subjectKey) {
    setFailedFor({ subjectKey, failed: false })
  }

  const viewpoints = useMemo(() => viewpointsFrom(allCards), [allCards])
  // このページに既にある観点の id（契約メモ §1-2「このページに既にある観点は
  // 出さない」）。`ownCards`（このページの足したカード）はすべて
  // `tool: 'set_measure'` だが、将来の型の広がりに備えて明示的に絞る。
  const existingViewpointIds = useMemo(
    () => ownCards.filter((c) => c.tool === 'set_measure').map((c) => viewpointId(c.params)),
    [ownCards],
  )
  const page: ViewpointPage = useMemo(() => {
    return {
      kind,
      classIri: classIri ?? undefined,
      linkingKinds,
      where,
      sourceScope,
      iri,
      existingViewpointIds,
    }
  }, [kind, classIri, linkingKinds, where, sourceScope, iri, existingViewpointIds])

  const chips = useMemo(() => applicableViewpoints(viewpoints, page), [viewpoints, page])

  if (chips.length === 0) return null

  async function handleClick(v: (typeof chips)[number]) {
    const params = paramsForPage(v, page)
    if (!params) {
      // このページでは条件が組めない（例: 観点の class が 1 件自身の種類と
      // 一致するのに `linkingKinds` に候補が無い）— 無条件で足さず、失敗と
      // 同じ帰結の 1 文で知らせる（黙って何もしないとチップが壊れて見える）。
      setFailedFor({ subjectKey, failed: true })
      return
    }
    setPendingId(v.id)
    setFailedFor({ subjectKey, failed: false })
    try {
      await runCard(subject, 'set_measure', params)
      const spec: CardSpec = {
        card_id: cardId(params),
        subject_key: subjectKey,
        tool: 'set_measure',
        params,
        title: viewpointTitle(v, t),
        output_kind: v.shape,
        created_at: new Date().toISOString(),
      }
      addCard(spec)
    } catch {
      setFailedFor({ subjectKey, failed: true })
    } finally {
      setPendingId(null)
    }
  }

  return (
    <div className="viewpoint-strip">
      <div className="viewpoint-strip-title">{t('viewpoints.strip_title')}</div>
      <div className="viewpoint-strip-chips">
        {chips.map((v) => (
          <button
            key={v.id}
            type="button"
            className="viewpoint-chip"
            title={t('viewpoints.add_hint')}
            disabled={pendingId === v.id}
            onClick={() => void handleClick(v)}
          >
            {pendingId === v.id ? t('viewpoints.adding') : viewpointTitle(v, t)}
          </button>
        ))}
      </div>
      {failedFor.subjectKey === subjectKey && failedFor.failed && <p className="ds-empty-note">{t('viewpoints.failed')}</p>}
    </div>
  )
}
