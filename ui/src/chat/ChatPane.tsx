// 中央の領域＝チャット（PR 11a）。人の文（右寄せ）と返事（ReplyCard）を時系列に並べ、下に入力欄。
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { Session } from './chatApi'
import { confirmTarget, proposedCount } from './chatState'
import { Composer } from './Composer'
import { ReplyCard } from './ReplyCard'

export interface ChatPaneProps {
  /** 無ければ新しい話（まだ保存していない）。 */
  session: Session | null
  loading: boolean
  /** 読み込みの失敗（話が開けない）。 */
  loadError: string | null
  busy: boolean
  /** 手番・✓・戻すの失敗の 1 行（入力欄の上）。 */
  error: string | null
  noKey: boolean
  onSend: (text: string) => Promise<boolean>
  onConfirm: (turnId: string) => void
  onRevert: (turnId: string) => void
  onOpenSettings: () => void
}

export function ChatPane(p: ChatPaneProps) {
  const { t } = useTranslation('chat')
  const turns = p.session?.turns ?? []
  const target = confirmTarget(turns)
  const count = proposedCount(turns)
  const endRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [turns.length, p.busy])

  return (
    <div className="chat-pane">
      <div className="chat-scroll" role="log" aria-live="polite">
        {p.loadError ? (
          <p className="promote-err" role="alert">
            {p.loadError}
          </p>
        ) : p.loading ? (
          <p className="chat-note-line">{t('list.loading')}</p>
        ) : turns.length === 0 ? (
          <p className="chat-empty">{t('empty.first')}</p>
        ) : (
          turns.map((turn) => (
            <div key={turn.id} className="chat-turn">
              {turn.user && (
                <div className="consult-msg consult-msg--user">
                  <div className="consult-bubble">{turn.user.text}</div>
                </div>
              )}
              <ReplyCard
                turn={turn}
                canConfirm={turn.id === target}
                count={count}
                busy={p.busy}
                onConfirm={p.onConfirm}
                onRevert={p.onRevert}
                onAsk={(q) => void p.onSend(q)}
              />
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
      <div className="chat-foot">
        {p.error && (
          <p className="promote-err" role="alert">
            {p.error}
          </p>
        )}
        <Composer onSend={p.onSend} busy={p.busy} noKey={p.noKey} onOpenSettings={p.onOpenSettings} />
      </div>
    </div>
  )
}
