import { Send, Sparkles } from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import '../consult/ConsultDrawer.css'

// 作業場の右（会話）の枠。人の一言・AI の散文（薄い）・提案カード（ProposalCard）の並びと入力欄。
// 見た目（吹き出し・入力欄）は consult/ の部品の CSS を流用する（ConsultDrawer は使わない）。
// 何を送るか・何を当てるかは持たない — ThemeWorkspace が決める（ここは描くだけ）。
//
// 並び: 見出し（相談・たたむ）／履歴（吹き出しと提案カード）／選んだ札・線の欄（focus）／
// 文の一覧と手で足す（tools）／入力欄。AI が未設定なら入力欄の代わりに 1 行と「設定を開く」。

export function GraphChat({
  children,
  focus,
  tools,
  aiReady,
  thinking,
  locked,
  onSend,
  onOpenSettings,
  onFold,
}: {
  /** 履歴（吹き出し・提案カード）。呼ぶ側が並べる。 */
  children: ReactNode
  /** 選んだ札・線の欄。無ければ出さない。 */
  focus: ReactNode
  /** 文の一覧・手で足す。 */
  tools: ReactNode
  /** AI が使える（設定済みで、503 でもない）。 */
  aiReady: boolean
  /** AI の返事を待っている。 */
  thinking: boolean
  /** 反映の最中などで入力を止める。 */
  locked: boolean
  onSend: (text: string) => void
  onOpenSettings: () => void
  onFold: () => void
}) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const count = Array.isArray(children) ? children.length : children ? 1 : 0

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [count, thinking])

  const busy = thinking || locked
  const send = () => {
    const v = text.trim()
    if (!v || busy || !aiReady) return
    setText('')
    onSend(v)
  }

  return (
    <aside className="thm-chat" aria-label={t('themes:chat.aria')} data-testid="graph-chat">
      <header className="thm-chat-head">
        <span className="consult-head-title">
          <Sparkles size={15} className="consult-fab-icon" aria-hidden="true" />
          {t('themes:chat.title')}
        </span>
        <button type="button" className="btn btn--ghost btn--sm thm-chat-fold" onClick={onFold} data-testid="chat-fold">
          {t('themes:chat.fold')}
        </button>
      </header>
      <div className="consult-scroll thm-chat-scroll" ref={scrollRef} data-testid="chat-scroll">
        {children}
        {thinking && (
          <div className="consult-msg consult-msg--assistant">
            <div className="consult-bubble consult-bubble--pending" role="status" aria-live="polite">
              <span className="consult-spinner" aria-hidden="true" />
              {t('themes:chat.thinking')}
            </div>
          </div>
        )}
      </div>
      {focus && <div className="thm-chat-focus">{focus}</div>}
      <div className="thm-chat-tools">{tools}</div>
      <div className="consult-foot thm-chat-foot">
        {aiReady ? (
          <form
            className="consult-composer"
            onSubmit={(e) => {
              e.preventDefault()
              send()
            }}
          >
            <textarea
              className="consult-input"
              rows={2}
              value={text}
              disabled={busy}
              aria-label={t('themes:chat.placeholder')}
              placeholder={t('themes:chat.placeholder')}
              data-testid="chat-input"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  send()
                }
              }}
            />
            <button
              type="submit"
              className="consult-send"
              disabled={busy || !text.trim()}
              aria-label={t('themes:chat.send')}
              title={t('themes:chat.send')}
              data-testid="chat-send"
            >
              <Send size={16} />
            </button>
          </form>
        ) : (
          <div className="thm-chat-unset" data-testid="chat-unset">
            <span>{t('themes:chat.unset')}</span>
            <button type="button" className="btn btn--sm" onClick={onOpenSettings}>
              {t('themes:chat.openSettings')}
            </button>
          </div>
        )}
      </div>
    </aside>
  )
}
