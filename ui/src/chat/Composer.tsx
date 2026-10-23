// 入力欄（PR 11a・11g で見慣れたチャットの形に）。1 つの丸い枠の中に、1〜8 行に伸びる textarea と、
// 下の行（左＝表を添えるクリップ・右＝丸い送るボタン）。Enter で送る・Shift+Enter で改行・IME の変換中と確定の Enter は送らない
// （WebKit は確定の Enter が普通の Enter に見える→ `useImeEnterGuard`）。
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useImeEnterGuard } from './useImeEnterGuard'
import { SourcePicker } from './SourcePicker'
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
  /** 取り込み元の MCP サーバ名（設定済みのとき）。あれば 📎 は 2 択のメニューになる。 */
  sourceHome?: string | null
  /** 取り込み元のファイルを添えた（選んだ id）。送れたら true。 */
  onAttachFromSource?: (ids: string[], names: string[]) => Promise<boolean>
  /** 範囲の札（入力欄の上）。 */
  scopeBar?: React.ReactNode
  /** 既定の例文の代わりに出す一時の例文（「この文を直す」）。 */
  placeholder?: string
  /** 変わるたびに入力欄へフォーカスする（0 は何もしない）。 */
  focusKey?: number
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

export function Composer({ onSend, busy, noKey, onOpenSettings, onAttach, sourceHome, onAttachFromSource, scopeBar, placeholder, focusKey = 0 }: ComposerProps) {
  const { t } = useTranslation('chat')
  const { compositionHandlers, isImeKey } = useImeEnterGuard()
  const { t: ts } = useTranslation('settings')
  const [text, setText] = useState('')
  const area = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const withSource = !!sourceHome && !!onAttachFromSource
  const wasBusy = useRef(false)
  // 送信などが終わって入力欄が使えるようになったら、フォーカスを返す
  useEffect(() => {
    if (wasBusy.current && !busy) area.current?.focus()
    wasBusy.current = busy
  }, [busy])
  useEffect(() => {
    if (focusKey > 0) area.current?.focus()
  }, [focusKey])
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

  // メニューは外を押す・Escape で閉じる
  useEffect(() => {
    if (!menuOpen) return
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== 'Escape') return
      if (e instanceof MouseEvent && (e.target as HTMLElement | null)?.closest('.chat-attach-wrap')) return
      setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [menuOpen])

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
      {scopeBar}
      <div className={`chat-composer-box${busy ? ' is-busy' : ''}`}>
        <textarea
          ref={area}
          className="chat-input"
          data-testid="chat-input"
          rows={1}
          value={text}
          disabled={busy}
          placeholder={placeholder ?? t('compose.placeholder')}
          aria-label={placeholder ?? t('compose.placeholder')}
          maxLength={2000}
          onChange={(e) => setText(e.target.value)}
          {...compositionHandlers}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !isImeKey(e)) {
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
              <span className="chat-attach-wrap">
                <button
                  type="button"
                  className="chat-icon-btn chat-attach"
                  data-testid="chat-attach"
                  disabled={busy}
                  aria-haspopup={withSource ? 'menu' : undefined}
                  aria-expanded={withSource ? menuOpen : undefined}
                  onClick={() => (withSource ? setMenuOpen((v) => !v) : fileInput.current?.click())}
                  aria-label={t('compose.attach')}
                  title={t('compose.attach')}
                >
                  <PaperclipIcon size={18} />
                </button>
                {withSource && menuOpen && (
                  <div className="chat-attach-menu" role="menu" data-testid="chat-attach-menu">
                    <button
                      type="button"
                      role="menuitem"
                      data-testid="chat-attach-local"
                      onClick={() => {
                        setMenuOpen(false)
                        fileInput.current?.click()
                      }}
                    >
                      {t('compose.attachLocal')}
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      data-testid="chat-attach-source"
                      onClick={() => {
                        setMenuOpen(false)
                        setPickerOpen(true)
                      }}
                    >
                      {t('compose.attachFrom', { server: sourceHome })}
                    </button>
                  </div>
                )}
              </span>
              {withSource && pickerOpen && sourceHome && onAttachFromSource && (
                <SourcePicker server={sourceHome} onAttach={onAttachFromSource} onClose={() => setPickerOpen(false)} />
              )}
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
