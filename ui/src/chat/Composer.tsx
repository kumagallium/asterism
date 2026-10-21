// 入力欄（PR 11a）。Enter で送る・Shift+Enter で改行・IME 変換中は送らない。📎 は 11c。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { SendIcon } from '../icons'

export interface ComposerProps {
  /** 送れたら true（入力を消す）。失敗なら false（書いた文字は残す）。 */
  onSend: (text: string) => Promise<boolean>
  /** 送信中・✓ 中・戻す中は止める。 */
  busy: boolean
  /** 鍵が無い（話を保存できない）。入力欄の代わりに 1 行。 */
  noKey: boolean
  onOpenSettings: () => void
}

export function Composer({ onSend, busy, noKey, onOpenSettings }: ComposerProps) {
  const { t } = useTranslation('chat')
  const { t: ts } = useTranslation('settings')
  const [text, setText] = useState('')

  if (noKey) {
    return (
      <div className="chat-composer chat-composer--nokey">
        <span className="chat-note-line">{t('compose.noKey')}</span>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onOpenSettings}>
          {ts('open')}
        </button>
      </div>
    )
  }

  async function submit() {
    const v = text.trim()
    if (!v || busy) return
    setText('')
    const ok = await onSend(v)
    if (!ok) setText((cur) => (cur === '' ? v : cur))
  }

  return (
    <div className="consult-composer chat-composer">
      <textarea
        className="consult-input"
        data-testid="chat-input"
        rows={2}
        value={text}
        disabled={busy}
        placeholder={t('compose.placeholder')}
        aria-label={t('compose.placeholder')}
        maxLength={2000}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            void submit()
          }
        }}
      />
      <button
        type="button"
        className="consult-send"
        data-testid="chat-send"
        disabled={busy || text.trim() === ''}
        onClick={() => void submit()}
        aria-label={t('compose.send')}
        title={t('compose.send')}
      >
        {busy ? <span className="consult-spinner" aria-hidden="true" /> : <SendIcon size={16} />}
      </button>
    </div>
  )
}
