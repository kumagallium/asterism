// 返事 1 枚（PR 11a）。text → sentences → questions（チップ）→ notes → 下の行（状態の印・✓・戻す）。
// notes.kind の全部（ai_unset・ai_failed・dropped・no_question・commit_error・info）と questions を今から描ける（11b が使う）。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { BindingDecision, Decided, Note, Turn } from './chatApi'
import { bindingDecisions, keptName, settleAsk } from './chatState'

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
  onConfirm: (turnId: string, decisions?: BindingDecision[]) => void
  onRevert: (turnId: string) => void
  /** チップを押した（その文を送る）。 */
  /** 送れたら true（失敗なら false で、チップの印を戻す）。 */
  onAsk: (question: string) => Promise<boolean> | boolean | void
  /** 残した語の slug → 名前（引けなければ slug のまま）。 */
  names?: ReadonlyMap<string, string>
  /** 読んでいる手番の進みの 1 行（`state: running`）。 */
  progress?: string
  /** 返事の中の小さな選択を決めた（手番・注記の番号・値）。 */
  onChoose?: (turnId: string, noteIndex: number, value: string) => void
}

function stamp(iso: string, lang: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(lang, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function NoteLine({ note, name, busy, onDecide }: { note: Note; name: string; busy: boolean; onDecide?: (value: string) => void }) {
  const { t } = useTranslation('chat')
  const [picked, setPicked] = useState(note.choice?.default ?? '')
  const cls = note.kind === 'commit_error' ? 'promote-err' : `chat-note chat-note--${note.kind}`
  const choice = note.choice
  const chosen = choice?.chosen
  return (
    <div className={cls} data-note-kind={note.kind} role={note.kind === 'commit_error' ? 'alert' : undefined}>
      <span>{note.text}</span>
      {choice && chosen === undefined && (
        <fieldset className="chat-choice">
          <legend>{choice.label}</legend>
          {choice.options.map((o) => (
            <label key={o.value}>
              <input type="radio" name={name} value={o.value} checked={picked === o.value} disabled={busy} onChange={() => setPicked(o.value)} />
              {o.label}
            </label>
          ))}
          {onDecide && (
            <button type="button" className="btn btn--sm" data-testid="choice-decide" disabled={busy || picked === ''} onClick={() => onDecide(picked)}>
              {t('attach.decide')}
            </button>
          )}
        </fieldset>
      )}
      {choice && chosen !== undefined && (
        <span className="chat-mark chat-mark--confirmed" data-testid="choice-chosen">
          {t('attach.chosen', { label: choice.options.find((o) => o.value === chosen)?.label ?? chosen })}
        </span>
      )}
    </div>
  )
}

/** 表を読んでいる最中の手番（返事の場所に 1 行）。 */
function Reading({ progress }: { progress?: string }) {
  const { t } = useTranslation('chat')
  return (
    <div className="consult-msg consult-msg--assistant" data-testid="reply-reading">
      <div className="consult-bubble consult-bubble--pending">
        <span className="consult-spinner" aria-hidden="true" />
        {progress ? t('attach.readingWith', { message: progress }) : t('attach.reading')}
      </div>
    </div>
  )
}

export function ReplyCard(p: ReplyCardProps) {
  return p.turn.state === 'running' ? <Reading progress={p.progress} /> : <ReplyBody {...p} />
}

function ReplyBody({ turn, canConfirm, count, busy, onConfirm, onRevert, onAsk, names, onChoose }: ReplyCardProps) {
  const { t, i18n } = useTranslation('chat')
  const r = turn.reply
  // 結ぶ／結ばないの選び（変えたものだけ持つ。✓ のときに既定へ重ねて送る）
  const [picked, setPicked] = useState<Readonly<Record<string, Decided>>>({})
  // 列の一致は 1 行ずつラジオで出すので、同じ文の箇条書きは省く
  const bindingSentence = (sn: string) => r.bindings.some((b) => sn.startsWith(`列「${b.column}」`) || sn.startsWith(`Column "${b.column}"`))
  const sentences = r.sentences.filter((sn) => !bindingSentence(sn))
  const head = sentences.slice(0, SENTENCE_LIMIT)
  const rest = sentences.slice(SENTENCE_LIMIT)
  const open = turn.state === 'proposed'
  const proposed = turn.state === 'proposed'
  const [sent, setSent] = useState<ReadonlySet<string>>(new Set())
  const unsend = (q: string) =>
    setSent((cur) => {
      const n = new Set(cur)
      n.delete(q)
      return n
    })
  // 機械の手番は text と注記の先頭が同じ文になる。二重に出さない
  const notes = turn.by === 'machine' && r.notes[0] && r.notes[0].text.trim() === r.text.trim() ? r.notes.slice(1) : r.notes
  // text と箇条書きが同じ内容なら片方だけ。人の手番（user が null）は箇条書き 1 行だけ
  const sameAsOnly = sentences.length === 1 && r.text.trim() === sentences[0].trim()
  const showText = !!r.text && !sameAsOnly && !(turn.user === null && sentences.length > 0)
  return (
    <div className="consult-msg consult-msg--assistant">
      <div className="chat-card" data-testid="reply-card" data-state={turn.state}>
        {turn.attachment && (
          <p className="chat-attach-head" data-testid="reply-attachment">
            📎 {turn.attachment.name}・{t('attach.rows', { n: turn.attachment.rows })}
          </p>
        )}
        {showText && <p className="chat-card-text">{r.text}</p>}
        {sentences.length > 0 && (
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
        {r.suggestions.length > 0 && sentences.length === 0 && (
          <ul className="chat-sentences">
            {r.suggestions.map((sg, i) => (
              <li key={i}>{t('attach.suggestionLine', { subject: sg.subject, term: sg.term })}</li>
            ))}
          </ul>
        )}
        {r.bindings.length > 0 && (
          <ul className="chat-bindings" data-testid="reply-bindings">
            {r.bindings.map((b) => {
              const now = picked[b.column] ?? b.decided
              const name = `bind-${turn.id}-${b.column}`
              return (
                <li key={b.column} className="chat-binding" data-column={b.column}>
                  <span>{t('attach.bindLine', { column: b.column, term: b.term_label || b.label || b.term })}</span>
                  <span className="chat-binding-radios">
                    {(['bind', 'skip'] as const).map((d) => (
                      <label key={d}>
                        <input
                          type="radio"
                          name={name}
                          value={d}
                          checked={now === d}
                          disabled={busy || !open}
                          onChange={() => setPicked((cur) => ({ ...cur, [b.column]: d }))}
                        />
                        {t(d === 'bind' ? 'attach.bind' : 'attach.skip')}
                      </label>
                    ))}
                  </span>
                </li>
              )
            })}
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
                  setSent((cur) => new Set([...cur, q]))
                  void settleAsk(() => onAsk(q)).then((ok) => {
                    if (!ok) unsend(q)
                  })
                }}
              >
                {q}
              </button>
            ))}
          </div>
        )}
        {notes.map((n, i) => (
          <NoteLine
            key={i}
            note={n}
            name={`choice-${turn.id}-${i}`}
            busy={busy}
            onDecide={onChoose ? (value) => onChoose(turn.id, r.notes.indexOf(n), value) : undefined}
          />
        ))}
        {turn.state === 'confirmed' && turn.commit?.removed && (turn.commit.removed.terms > 0 || turn.commit.removed.lines > 0) && (
          <p className="chat-note-line" data-testid="reply-removed">
            {t('reply.removed', { terms: turn.commit.removed.terms, lines: turn.commit.removed.lines })}
          </p>
        )}
        {turn.state === 'confirmed' &&
          (turn.commit?.kept ?? []).map((k) => (
            <p key={k.slug} className="chat-note-line" data-testid="reply-kept">
              {t('reply.kept', { slug: keptName(k.slug, names), reason: k.reason })}
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
              <button type="button" className="btn btn--sm" data-testid="reply-confirm" disabled={busy} onClick={() => onConfirm(turn.id, r.bindings.length > 0 ? bindingDecisions(r.bindings, picked) : undefined)}>
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
