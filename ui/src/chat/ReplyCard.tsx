// 返事 1 枚（PR 11a）。text → sentences → questions（チップ）→ notes → 下の行（状態の印・✓・戻す）。
// notes.kind の全部（ai_unset・ai_failed・dropped・no_question・commit_error・info）と questions を今から描ける（11b が使う）。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Note, Turn } from './chatApi'

/** 箇条書きで出す文の数（それ以上は「ほか N 件」の折りたたみ）。 */
export const SENTENCE_LIMIT = 6

export interface ReplyCardProps {
  turn: Turn
  /** この手番が「最後の未確認の返事」か（`chatState.confirmTarget`）。✓ はここにだけ出す。 */
  canConfirm: boolean
  /** ✓ で置く差分の数（`chatState.proposedCount`）。 */
  count: number
  /** 送信中・✓ 中・戻す中はボタンを止める。 */
  busy: boolean
  onConfirm: (turnId: string) => void
  onRevert: (turnId: string) => void
  /** チップを押した（その文を送る）。 */
  onAsk: (question: string) => void
}

function stamp(iso: string, lang: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(lang, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function NoteLine({ note }: { note: Note }) {
  const [picked, setPicked] = useState(note.choice?.default ?? '')
  const cls = note.kind === 'commit_error' ? 'promote-err' : `chat-note chat-note--${note.kind}`
  return (
    <div className={cls} data-note-kind={note.kind} role={note.kind === 'commit_error' ? 'alert' : undefined}>
      <span>{note.text}</span>
      {note.choice && (
        <fieldset className="chat-choice">
          <legend>{note.choice.label}</legend>
          {note.choice.options.map((o) => (
            <label key={o.value}>
              <input type="radio" name={`choice-${note.kind}-${note.choice?.label}`} value={o.value} checked={picked === o.value} onChange={() => setPicked(o.value)} />
              {o.label}
            </label>
          ))}
        </fieldset>
      )}
    </div>
  )
}

export function ReplyCard({ turn, canConfirm, count, busy, onConfirm, onRevert, onAsk }: ReplyCardProps) {
  const { t, i18n } = useTranslation('chat')
  const r = turn.reply
  const head = r.sentences.slice(0, SENTENCE_LIMIT)
  const rest = r.sentences.slice(SENTENCE_LIMIT)
  const proposed = turn.state === 'proposed'
  const [sent, setSent] = useState<ReadonlySet<string>>(new Set())
  // text と箇条書きが同じ内容なら片方だけ。人の手番（user が null）は箇条書き 1 行だけ
  const sameAsOnly = r.sentences.length === 1 && r.text.trim() === r.sentences[0].trim()
  const showText = !!r.text && !sameAsOnly && !(turn.user === null && r.sentences.length > 0)
  return (
    <div className="consult-msg consult-msg--assistant">
      <div className="chat-card" data-testid="reply-card" data-state={turn.state}>
        {showText && <p className="chat-card-text">{r.text}</p>}
        {r.sentences.length > 0 && (
          <ul className="chat-sentences">
            {head.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
            {rest.length > 0 && (
              <li className="chat-sentences-more">
                <details>
                  <summary>{t('reply.more', { n: rest.length })}</summary>
                  <ul>
                    {rest.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </details>
              </li>
            )}
          </ul>
        )}
        {r.questions.length > 0 && (
          <div className="chat-chips">
            {r.questions.map((q) => (
              <button
                key={q}
                type="button"
                className={`chat-chip${sent.has(q) ? ' is-sent' : ''}`}
                disabled={busy || sent.has(q)}
                onClick={() => {
                  setSent(new Set([...sent, q]))
                  onAsk(q)
                }}
              >
                {q}
              </button>
            ))}
          </div>
        )}
        {r.notes.map((n, i) => (
          <NoteLine key={i} note={n} />
        ))}
        {turn.state === 'confirmed' && turn.commit?.removed && (turn.commit.removed.terms > 0 || turn.commit.removed.lines > 0) && (
          <p className="chat-note-line" data-testid="reply-removed">
            {t('reply.removed', { terms: turn.commit.removed.terms, lines: turn.commit.removed.lines })}
          </p>
        )}
        {turn.state === 'confirmed' &&
          (turn.commit?.kept ?? []).map((k) => (
            <p key={k.slug} className="chat-note-line" data-testid="reply-kept">
              {t('reply.kept', { slug: k.slug, reason: k.reason })}
            </p>
          ))}
        {(turn.state !== 'none' || turn.by === 'machine') && (
          <div className="chat-card-foot">
            {turn.by === 'machine' && <span className="chat-by-machine">{t('reply.byMachine')}</span>}
            {turn.state === 'proposed' && <span className="chat-mark chat-mark--proposed">{t('reply.proposed')}</span>}
            {turn.state === 'confirmed' && (
              <span className="chat-mark chat-mark--confirmed">{t('reply.confirmed', { at: stamp(turn.commit?.at || turn.at, i18n.language) })}</span>
            )}
            {turn.state === 'reverted' && <span className="chat-mark chat-mark--reverted">{t('reply.reverted')}</span>}
            {canConfirm && turn.state !== 'none' && (
              <button type="button" className="btn btn--sm" data-testid="reply-confirm" disabled={busy} onClick={() => onConfirm(turn.id)}>
                {t('reply.confirm')}
                {count > 0 && ` ${t('reply.count', { n: count })}`}
              </button>
            )}
            {proposed && (
              <button type="button" className="link-btn" data-testid="reply-revert" disabled={busy} onClick={() => onRevert(turn.id)}>
                {t('reply.revert')}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
