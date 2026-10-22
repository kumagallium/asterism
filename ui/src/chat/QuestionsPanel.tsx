// 「問い」のタブ（PR 11l）: 承認済みの問いの一覧。1 行＝印（✓ 答えられる／– まだ）＋問いの文＋小さな 1 行。
// 文を押すと知識グラフで要る用語が光り、「答えて」はチャットに送って必ず Ask へ。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isAnswerable, type SkyData, type SkyQuestion } from '../sky/skyApi'
import { settleAsk } from './chatState'
import { datasetNamesOf } from './questionsState'

export interface QuestionsPanelProps {
  questions: SkyQuestion[]
  sky: SkyData
  /** まだ承認していない提案の問い（この話の下書き）。 */
  proposed: string[]
  onPick: (q: SkyQuestion) => void
  onAsk: (text: string) => Promise<boolean>
  busy: boolean
}

const NAME_LIMIT = 3

export function QuestionsPanel({ questions, sky, proposed, onPick, onAsk, busy }: QuestionsPanelProps) {
  const { t } = useTranslation('questions')
  const [sent, setSent] = useState<ReadonlySet<string>>(new Set())
  const unsend = (q: string) =>
    setSent((cur) => {
      const next = new Set(cur)
      next.delete(q)
      return next
    })
  const ask = (q: string) => {
    setSent((cur) => new Set([...cur, q]))
    void settleAsk(() => onAsk(q)).then((ok) => {
      if (!ok) unsend(q)
    })
  }
  const askButton = (q: string) => (
    <button type="button" className="btn btn--sm" data-testid="question-ask" disabled={busy || sent.has(q)} onClick={() => ask(q)}>
      {sent.has(q) ? t('row.sent') : t('row.ask')}
    </button>
  )
  const noteOf = (q: SkyQuestion): string => {
    if (q.mode === 'knowledge') return isAnswerable(q) ? t('row.byKnowledge', { n: q.knowledge ?? 0 }) : t('row.noData')
    if (q.answering < 1) return t('row.noData')
    const names = datasetNamesOf(q, sky)
    if (names.length === 0) return ''
    const shown = names.slice(0, NAME_LIMIT).join('・')
    const rest = names.length - NAME_LIMIT
    return t('row.answeredBy', { names: rest > 0 ? `${shown}・${t('more', { n: rest })}` : shown })
  }
  return (
    <div className="chat-questions" data-testid="questions-panel">
      {questions.length === 0 && proposed.length === 0 && <p className="chat-note-line">{t('empty')}</p>}
      {questions.length > 0 && (
        <ul className="chat-questions-list">
          {questions.map((q) => {
            const ok = isAnswerable(q)
            const note = noteOf(q)
            return (
              <li key={q.tool_name} className="chat-question" data-testid="question-row">
                <span className={`chat-question-mark${ok ? ' is-ok' : ''}`} role="img" aria-label={ok ? t('row.answerable') : t('row.notYet')}>
                  {ok ? '✓' : '–'}
                </span>
                <div className="chat-question-body">
                  <button type="button" className="chat-question-title" onClick={() => onPick(q)}>
                    {q.title}
                  </button>
                  {note && <span className="chat-question-note">{note}</span>}
                </div>
                {askButton(q.title)}
              </li>
            )
          })}
        </ul>
      )}
      {proposed.length > 0 && (
        <ul className="chat-questions-list is-proposed">
          {proposed.map((q) => (
            <li key={q} className="chat-question is-proposed" data-testid="question-row">
              <span className="chat-question-mark" aria-hidden="true">
                –
              </span>
              <div className="chat-question-body">
                <span className="chat-question-title">{q}</span>
                <span className="chat-question-note">{t('row.proposed')}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
