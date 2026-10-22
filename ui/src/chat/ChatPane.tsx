// 中央の領域＝チャット（PR 11a）。人の文（右寄せ）と返事（ReplyCard）を時系列に並べ、下に入力欄。
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { BindingDecision, Session } from './chatApi'
import { confirmTargets, proposedCount } from './chatState'
import { Composer } from './Composer'
import { ReplyCard } from './ReplyCard'

export interface ChatPaneProps {
  /** 無ければ新しい話（まだ保存していない）。 */
  session: Session | null
  loading: boolean
  /** 読み込みの失敗（話が開けない）。 */
  loadError: string | null
  busy: boolean
  /** 手番を送っている最中（返事を待っている）。 */
  sending?: boolean
  /** 送った瞬間の人の文（返りが来るまで会話の末尾に出す）。 */
  pendingUser?: string | null
  /** 送ったのは表（返事の場所に「読んでいます…」を出す）。 */
  pendingReading?: boolean
  /** 手番・✓・戻すの失敗の 1 行（入力欄の上）。 */
  error: string | null
  noKey: boolean
  onSend: (text: string) => Promise<boolean>
  /** 表を添える。 */
  onAttach?: (files: File[]) => Promise<boolean> | boolean | void
  /** 読んでいる手番の id → 進みの 1 行。 */
  progress?: Readonly<Record<string, string>>
  /** 読み取りが途中で止まった手番の id。 */
  interrupted?: Readonly<Record<string, boolean>>
  /** 返事の中の小さな選択を決めた。 */
  onChoose?: (turnId: string, noteIndex: number, value: string) => void
  /** 残した語の slug → 名前。 */
  names?: ReadonlyMap<string, string>
  onConfirm: (turnId: string, decisions?: BindingDecision[]) => void
  onRevert: (turnId: string) => void
  onOpenSettings: () => void
}

export function ChatPane(p: ChatPaneProps) {
  const { t } = useTranslation('chat')
  const turns = p.session?.turns ?? []
  const targets = confirmTargets(turns)
  const count = proposedCount(turns)
  const endRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [turns.length, p.busy, p.sending, p.pendingUser])

  return (
    <div className="chat-pane">
      <div className="chat-scroll" role="log" aria-live="polite">
        {p.loadError ? (
          <p className="promote-err" role="alert">
            {p.loadError}
          </p>
        ) : p.loading ? (
          <p className="chat-note-line">{t('list.loading')}</p>
        ) : turns.length === 0 && !p.sending && !p.pendingUser ? (
          <p className="chat-empty">{t('empty.first')}</p>
        ) : (
          turns.map((turn) => (
            <div key={turn.id} className="chat-turn">
              {turn.user && (
                <div className="consult-msg consult-msg--user">
                  <div className="consult-bubble">
                    {turn.user.text}
                    {turn.user.attachments.map((a, i) => (
                      <div key={i} data-testid="user-attachment">
                        📎 {a.name}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <ReplyCard
                turn={turn}
                canConfirm={targets.has(turn.id)}
                count={count}
                busy={p.busy}
                onConfirm={p.onConfirm}
                onRevert={p.onRevert}
                onAsk={(q) => p.onSend(q)}
                names={p.names}
                progress={p.progress?.[turn.id]}
                interrupted={p.interrupted?.[turn.id]}
                onChoose={p.onChoose}
              />
            </div>
          ))
        )}
        {p.pendingUser && (
          <div className="consult-msg consult-msg--user" data-testid="pending-user">
            <div className="consult-bubble">{p.pendingUser}</div>
          </div>
        )}
        {p.sending && !!p.pendingUser && (
          <div className="consult-msg consult-msg--assistant" data-testid="reply-thinking">
            <div className="consult-bubble consult-bubble--pending">
              <span className="consult-spinner" aria-hidden="true" />
              {p.pendingReading ? t('attach.reading') : t('reply.thinking')}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div className="chat-foot">
        {p.error && (
          <p className="promote-err" role="alert">
            {p.error}
          </p>
        )}
        <Composer onSend={p.onSend} onAttach={p.onAttach} busy={p.busy} noKey={p.noKey} onOpenSettings={p.onOpenSettings} />
      </div>
    </div>
  )
}
