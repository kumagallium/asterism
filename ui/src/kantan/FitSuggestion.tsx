import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ColumnFit } from '../api'
import { fit as fetchFit, type FitCandidate } from '../vocabApi'
import { fitSentence, itemFitCandidates } from './kindFit'

// ③「意味をつける」の行の隣に出す、決定論の**当てはめ提案**（表示のみ）。
// ADR upper-structure-shared-terms §2.5.3: 列の意味と列名が、既にある語・標準語・
// 他データの項目と**完全一致**したときだけ出す（照合は API 側）。候補が無ければ
// 何も出さない。☑ を自動で付けない — 受けるかどうかは人の指で決まる。

/** 打ち終わりを待つ時間。意味の欄は 1 文字ごとに変わるので、毎回は引かない。 */
const DEBOUNCE_MS = 400
const MAX_SHOWN = 3

export function FitSuggestion({
  label,
  column,
  accepted,
  canTick,
  isTicked,
  reviewOnly = false,
  colSpan,
  onAccept,
  onClear,
  onTick,
}: {
  /** ③で書いた意味。 */
  label: string
  /** 列名（機械が仮につけた名前のときは空にして渡す）。 */
  column: string
  /** 人が既に受けた当てはめ（`column-meanings.json` の fit）。 */
  accepted: ColumnFit | null | undefined
  /** 「値でもつなぐ」を出せるか（測定値・番号の列・外した列では出さない）。 */
  canTick: boolean
  isTicked: boolean
  /** 設計後の意味の見直し。この経路では当てはめは線にならない（下書きを作り直す
   *  ときだけ upper が効く）ので、受ける・外すを出さず、1 行の案内にする。 */
  reviewOnly?: boolean
  /** 表の行の下に、表の幅いっぱいの補助行（`<tr><td colSpan>`）として出すときの列数。
   *  ☑ のセルの中に出すと、文とボタンが狭い列からあふれて右端で切れた（実機 2026-10-09）。 */
  colSpan?: number
  onAccept: (candidate: FitCandidate) => void
  onClear: () => void
  onTick: (candidate: FitCandidate) => void
}) {
  const { t } = useTranslation()
  // 候補は、引いたときの入力（key）といっしょに持つ。入力が変わった瞬間に key が合わなく
  // なり、新しい応答が届くまで前の入力の候補は出ない（打ち直しの間、前の語の「受ける」が
  // 残って、別の語への当てはめとして押せてしまうのを防ぐ）。
  const inputKey = `${label.trim()}\u0000${column.trim()}`
  const [found, setFound] = useState<{ key: string; list: FitCandidate[] }>({ key: '', list: [] })
  const candidates = found.key === inputKey ? found.list : []

  useEffect(() => {
    const l = label.trim()
    const c = column.trim()
    // 入力が空なら引かない（出す側も hasInput で止める）。
    if (!l && !c) return
    const key = `${l}\u0000${c}`
    // 取り消し: 次の入力・離脱で古い応答を捨てる（fetch 自体は止めない）。
    let stale = false
    const timer = setTimeout(() => {
      void fetchFit(l, c)
        .then((list) => {
          // 項目（property）の候補だけ。種類（class）は⑤で出す。
          if (!stale) setFound({ key, list: itemFitCandidates(list) })
        })
        .catch(() => {
          // 提案は補助。引けなければ何も出さない（行き止まりにしない）。
          if (!stale) setFound({ key, list: [] })
        })
    }, DEBOUNCE_MS)
    return () => {
      stale = true
      clearTimeout(timer)
    }
  }, [label, column])

  const hasInput = label.trim() !== '' || column.trim() !== ''
  if (!hasInput || candidates.length === 0) return null
  const body = (
    <div className="kz-note kz-fit-note" data-testid="fit-suggestion">
      {candidates.slice(0, MAX_SHOWN).map((c) => {
        const same = accepted?.term === c.term
        return (
          <div key={`${c.kind}:${c.term}`}>
            <span>{fitSentence(t, c)}</span>{' '}
            {same ? (
              <>
                <span role="status">{t('kantan:meanings.fit.accepted')}</span>{' '}
                {!reviewOnly && (
                  <button type="button" className="btn btn--ghost btn--sm" onClick={onClear}>
                    {t('kantan:meanings.fit.clear')}
                  </button>
                )}
              </>
            ) : reviewOnly ? null : (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => onAccept(c)}
              >
                {t('kantan:meanings.fit.accept')}
              </button>
            )}{' '}
            {canTick && !isTicked && (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => onTick(c)}
              >
                {t('kantan:meanings.fit.tick')}
              </button>
            )}
          </div>
        )
      })}
      {reviewOnly && <p data-testid="fit-review-note">{t('kantan:meanings.fit.reviewNote')}</p>}
    </div>
  )
  if (!colSpan) return body
  return (
    <tr className="kz-fit-row">
      <td colSpan={colSpan}>{body}</td>
    </tr>
  )
}
