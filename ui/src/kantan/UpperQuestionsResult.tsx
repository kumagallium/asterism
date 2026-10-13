import { useTranslation } from 'react-i18next'
import type { UpperQuestionsReport } from '../api'
import { describeUpperQuestions, shortRef, skipReasonKind } from './questions'

/**
 * 公開（promote／名前だけの公開）が書いた線と問いの結果（応答の `upper_questions`）。
 * 書けたぶんは 1 行、書けなかった線の理由・検査で落ちた問い・警告は 1 件ずつ見せる。
 * 言うことが無ければ何も出さない。
 */
export function UpperQuestionsResult({
  report,
  titleOf,
}: {
  report: UpperQuestionsReport | null
  /** 問いの id → 題（検査で落ちた問いを人の言葉で呼ぶため）。 */
  titleOf?: (id: string) => string | undefined
}) {
  const { t } = useTranslation()
  const notes = describeUpperQuestions(report)
  if (notes.empty) return null
  return (
    <div className="kz-note" role="status" data-testid="upper-questions-result">
      {notes.applied > 0 && <p>{t('kantan:publishResult.applied', { n: notes.applied })}</p>}
      {notes.written > 0 && <p>{t('kantan:publishResult.written', { n: notes.written })}</p>}
      {notes.skipped.length > 0 && (
        <>
          <p>{t('kantan:publishResult.skippedHead', { n: notes.skipped.length })}</p>
          <ul>
            {notes.skipped.map((x) => {
              const kind = skipReasonKind(x.reason)
              return (
                <li key={`${x.subject}\u0000${x.term}`}>
                  {t('kantan:publishResult.skippedItem', {
                    subject: shortRef(x.subject),
                    term: shortRef(x.term),
                    reason:
                      kind === 'other'
                        ? x.reason
                        : t(`kantan:publishResult.reason.${kind}`),
                  })}
                </li>
              )
            })}
          </ul>
        </>
      )}
      {notes.lintErrors.length > 0 && (
        <>
          <p>{t('kantan:publishResult.lintHead', { n: notes.lintErrors.length })}</p>
          <ul>
            {notes.lintErrors.map((e) => (
              <li key={e.id}>
                {t('kantan:publishResult.lintItem', { title: titleOf?.(e.id) || e.id })}
              </li>
            ))}
          </ul>
        </>
      )}
      {notes.warnings.length > 0 && (
        <ul>
          {notes.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
