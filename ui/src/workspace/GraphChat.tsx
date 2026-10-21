import { Send, Sparkles } from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import '../consult/ConsultDrawer.css'
import './workspace.css'

// 作業場の右（会話）の枠。人の一言・AI の散文（薄い）・提案カード（ProposalCard）の並びと入力欄。
// 見た目（吹き出し・入力欄）は consult/ の部品の CSS を流用する（ConsultDrawer は使わない）。
// 何を送るか・何を当てるかは持たない — ThemeWorkspace が決める（ここは描くだけ）。
//
// 並び: 見出し（相談・たたむ）／履歴（吹き出しと提案カード）／選んだ札・線の欄（focus）／
// 文の一覧と手で足す（tools）／入力欄。AI が未設定なら入力欄の代わりに 1 行と「設定を開く」。

export function GraphChat({
  children,
  pinned,
  bar,
  prefill,
  focus,
  tools,
  aiReady,
  thinking,
  locked,
  onSend,
  onOpenSettings,
  onFold,
  headAction,
  placeholder,
  hideEmptyHistory = false,
  focusSignal = 0,
  compactHead = false,
}: {
  /** 履歴（吹き出し・提案カード）。呼ぶ側が並べる。 */
  children: ReactNode
  /** 会話の先頭に差し込む札（星を選んだときの「この星について」・線を選んだときの A・関係・B など）。無ければ出さない。 */
  pinned?: ReactNode
  /** 入力欄の上に固定する操作（「N 件を置く」）。無ければ出さない。 */
  bar?: ReactNode
  /** 入力欄に文を入れて、フォーカスする（`id` が変わるたびに 1 回）。 */
  prefill?: { id: number; text: string } | null
  /** 選んだ札・線の欄。無ければ出さない。 */
  focus?: ReactNode
  /** 文の一覧・手で足す。 */
  tools?: ReactNode
  /** AI が使える（設定済みで、503 でもない）。 */
  aiReady: boolean
  /** AI の返事を待っている。 */
  thinking: boolean
  /** 反映の最中などで入力を止める。 */
  locked: boolean
  onSend: (text: string) => void
  onOpenSettings: () => void
  onFold: () => void
  /** 見出し（相談）の右に置く小さな操作（「AI に結ばせる」など）。 */
  headAction?: ReactNode
  /** 入力欄の placeholder（無ければ既定の一言）。 */
  placeholder?: string
  /** 履歴が空のあいだは履歴の枠を出さない（大きな空白を出さない）。作業場は従来どおり出す。 */
  hideEmptyHistory?: boolean
  /** 値が増えるたびに入力欄へフォーカスする（外の「相談」ボタンが、開いたままの会話を指したとき）。 */
  focusSignal?: number
  /** 見出し（相談・たたむ）を出さない。外の欄が自分の見出しを持っているとき（左の会話の欄に置いたとき）。 */
  compactHead?: boolean
}) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const count = Array.isArray(children) ? children.length : children ? 1 : 0

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [count, thinking])

  useEffect(() => {
    if (focusSignal > 0) inputRef.current?.focus()
  }, [focusSignal])

  // 外から文を入れる（質問するから持ち込まれた題など）。送るのは人。同じ id では 1 回だけ。
  const prefillId = prefill?.id ?? 0
  const [seenPrefill, setSeenPrefill] = useState(0)
  if (prefillId !== seenPrefill) {
    setSeenPrefill(prefillId)
    if (prefill) setText(prefill.text)
  }
  useEffect(() => {
    if (prefillId > 0) inputRef.current?.focus()
  }, [prefillId])

  const busy = thinking || locked
  const showHistory = !(hideEmptyHistory && count === 0 && !thinking)
  const hint = placeholder ?? t('themes:chat.placeholder')
  const send = () => {
    const v = text.trim()
    if (!v || busy || !aiReady) return
    setText('')
    onSend(v)
  }

  return (
    <aside className="thm-chat" aria-label={t('themes:chat.aria')} data-testid="graph-chat">
      {!compactHead && (
        <header className="thm-chat-head">
          <span className="consult-head-title">
            <Sparkles size={15} className="consult-fab-icon" aria-hidden="true" />
            {t('themes:chat.title')}
          </span>
          <span className="thm-chat-head-actions">
            {headAction}
            <button type="button" className="btn btn--ghost btn--sm thm-chat-fold" onClick={onFold} data-testid="chat-fold">
              {t('themes:chat.fold')}
            </button>
          </span>
        </header>
      )}
      {pinned && (
        <div className="thm-chat-pinned" data-testid="chat-pinned">
          {pinned}
        </div>
      )}
      {showHistory && (
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
      )}
      {focus && <div className="thm-chat-focus">{focus}</div>}
      {tools && <div className="thm-chat-tools">{tools}</div>}
      {bar && <div className="thm-chat-bar">{bar}</div>}
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
              ref={inputRef}
              aria-label={hint}
              placeholder={hint}
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
