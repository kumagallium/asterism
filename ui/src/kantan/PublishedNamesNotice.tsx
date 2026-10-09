import { useTranslation } from 'react-i18next'
import type { PublishedNameChange, UpperQuestionsReport } from '../api'
import { describeUpperQuestions } from './questions'
import { UpperQuestionsResult } from './UpperQuestionsResult'

/**
 * K10: 公開を押す前に、その帰結を見せる。ここで公開されるのは項目の名前だけ —
 * 保存した名前のうち、公開済みの ID を開いたページにまだ出ていないものを 1 件ずつ
 * 並べ、その下にボタンを置く（データは取り込み直さない）。見た目は「ID の引っ越し」
 * の知らせ（IdMoveNotice）と同じ枠: どちらも「この公開で、画面の外の何が変わるか」。
 *
 * 名前の違いが無くても、公開済みのデータセットに**まだ書いていない線**（受けた当てはめ）
 * か**問い**があれば、同じ場所に「線 N 本・問い M 件を公開に書き込む」を出す
 * （ADR upper-structure-shared-terms §2.3 — 名前だけの公開も線と問いを書く）。
 */
export function PublishedNamesNotice({
  changes,
  writes,
  report,
  titleOf,
  busy,
  done,
  error,
  onPublish,
}: {
  /** 公開側にまだ出ていない名前（namesToPublish の結果）。 */
  changes: PublishedNameChange[]
  /** 公開に書き込む線と問いの件数。無ければ null（出さない）。 */
  writes: { lines: number; questions: number } | null
  /** 直前の公開の応答（`upper_questions`）。まだ公開していなければ null。 */
  report: UpperQuestionsReport | null
  /** 問いの id → 題。 */
  titleOf?: (id: string) => string | undefined
  busy: boolean
  /** 直前に公開した件数。まだ公開していなければ null。 */
  done: number | null
  /** 公開に失敗したときの技術的な詳細（たたんで見せる）。 */
  error: string
  onPublish: () => void
}) {
  const { t } = useTranslation()
  if (changes.length === 0 && !writes) {
    const noted = !describeUpperQuestions(report).empty
    if (done === null && !noted) return null
    return (
      <div className="kz-idmove">
        {done !== null && (
          <p role="status">{t('kantan:s7.namesPublished', { n: done })}</p>
        )}
        <UpperQuestionsResult report={report} titleOf={titleOf} />
      </div>
    )
  }
  return (
    <div className="kz-idmove">
      <p className="kz-idmove-head">
        {t(changes.length > 0 ? 'kantan:s7.namesTitle' : 'kantan:s7.writesTitle')}
      </p>
      {changes.length > 0 && (
        <>
          <p>{t('kantan:s7.namesLead')}</p>
          <ul className="kz-idmove-list">
            {changes.map((change) => (
              <li key={change.iri}>
                {t('kantan:s7.namesChange', {
                  published: change.published,
                  design: change.design,
                })}
              </li>
            ))}
          </ul>
        </>
      )}
      {writes && (
        <p data-testid="names-notice-writes">
          {t(
            writes.lines > 0 && writes.questions > 0
              ? 'kantan:s7.writesBoth'
              : writes.lines > 0
                ? 'kantan:s7.writesLines'
                : 'kantan:s7.writesQuestions',
            { lines: writes.lines, questions: writes.questions },
          )}
        </p>
      )}
      <div className="kz-actions">
        <button type="button" className="btn btn--ghost" onClick={onPublish} disabled={busy}>
          {t(
            busy
              ? 'kantan:s7.namesPublishing'
              : changes.length > 0
                ? 'kantan:s7.namesPublish'
                : 'kantan:s7.writesPublish',
          )}
        </button>
      </div>
      <p>{t(changes.length > 0 ? 'kantan:s7.namesNote' : 'kantan:s7.writesNote')}</p>
      <UpperQuestionsResult report={report} titleOf={titleOf} />
      {error && (
        <div role="alert">
          <p>{t('kantan:s7.namesFailed')}</p>
          <details className="kz-stop-detail">
            <summary>{t('kantan:s5.stop.detailSummary')}</summary>
            <pre className="error">{error}</pre>
          </details>
        </div>
      )}
    </div>
  )
}
