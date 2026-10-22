// 入力欄（PR 11a・11g で見慣れたチャットの形に）。1 つの丸い枠の中に、1〜8 行に伸びる textarea と、
// 下の行（左＝表を添えるクリップ・右＝丸い送るボタン）。Enter で送る・Shift+Enter で改行・IME 変換中は送らない。
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { GearIcon, PaperclipIcon, SendIcon } from '../icons'

export interface ComposerProps {
  /** 送れたら true（入力を消す）。失敗なら false（書いた文字は残す）。 */
  onSend: (text: string) => Promise<boolean>
  /** 送信中・✓ 中・戻す中は止める。 */
  busy: boolean
  /** 鍵が無い（話を保存できない）。入力欄の代わりに 1 行。 */
  noKey: boolean
  onOpenSettings: () => void
  /** 表を添えた（選ぶとすぐ）。 */
  onAttach?: (files: File[]) => Promise<boolean> | boolean | void
}

/** 1 行の高さ（0.95rem × 1.6）・上下の余白・伸びる上限（8 行）。 */
const LINE_PX = 24
const PAD_PX = 16
const MAX_LINES = 8

/** 書いた量に合わせて高さを決める（隠れていて測れないときは触らない）。 */
function fitHeight(el: HTMLTextAreaElement): void {
  el.style.height = 'auto'
  const sh = el.scrollHeight
  if (sh <= 0) return
  el.style.height = `${Math.max(LINE_PX + PAD_PX, Math.min(sh, LINE_PX * MAX_LINES + PAD_PX))}px`
}

export function Composer({ onSend, busy, noKey, onOpenSettings, onAttach }: ComposerProps) {
  const { t } = useTranslation('chat')
  const { t: ts } = useTranslation('settings')
  const [text, setText] = useState('')
  const area = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const wasBusy = useRef(false)
  // 送信などが終わって入力欄が使えるようになったら、フォーカスを返す
  useEffect(() => {
    if (wasBusy.current && !busy) area.current?.focus()
    wasBusy.current = busy
  }, [busy])
  // 書いた量に合わせて伸びる（つまみでの手動の伸縮は無し）。幅が変わったとき・隠れていたのが見えたときも測り直す
  useEffect(() => {
    const el = area.current
    if (!el) return
    fitHeight(el)
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => fitHeight(el))
    ro.observe(el.parentElement ?? el)
    return () => ro.disconnect()
  }, [text, busy, noKey])

  if (noKey) {
    return (
      <div className="chat-composer chat-composer--nokey">
        <span className="chat-note-line">{t('compose.noKey')}</span>
        <button type="button" className="chat-icon-btn" onClick={onOpenSettings} aria-label={ts('open')} title={ts('open')}>
          <GearIcon size={16} />
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
    <div className="chat-composer">
      <div className={`chat-composer-box${busy ? ' is-busy' : ''}`}>
        <textarea
          ref={area}
          className="chat-input"
          data-testid="chat-input"
          rows={1}
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
        <div className="chat-composer-row">
          {onAttach && (
            <>
              <input
                ref={fileInput}
                type="file"
                multiple
                hidden
                data-testid="chat-attach-input"
                accept=".csv,.tsv,.xlsx,.xls,.json"
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? [])
                  e.target.value = ''
                  if (files.length > 0) void onAttach(files)
                }}
              />
              <button
                type="button"
                className="chat-icon-btn chat-attach"
                data-testid="chat-attach"
                disabled={busy}
                onClick={() => fileInput.current?.click()}
                aria-label={t('compose.attach')}
                title={t('compose.attach')}
              >
                <PaperclipIcon size={18} />
              </button>
            </>
          )}
          <span className="chat-composer-spacer" />
          <button
            type="button"
            className="chat-send"
            data-testid="chat-send"
            disabled={busy || text.trim() === ''}
            onClick={() => void submit()}
            aria-label={t('compose.send')}
            title={t('compose.send')}
          >
            {busy ? <span className="chat-spinner" aria-hidden="true" /> : <SendIcon size={16} />}
          </button>
        </div>
      </div>
      <p className="chat-composer-hint" aria-hidden="true">
        {t('compose.hint')}
      </p>
    </div>
  )
}
