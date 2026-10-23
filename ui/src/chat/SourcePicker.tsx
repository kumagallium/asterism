// 取り込み元のファイルの一覧のダイアログ（📎 →「〈サーバ名〉のファイル」）。開いたら MCP サーバの表のファイルを取り、
// 選んだ id を `onAttach` に渡す。Escape で閉じる。
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { TABLE_EXTS, formatSize } from './sourcePickerUtil'
import { fetchSourceItems, type SourceItem } from './chatApi'
import { ThemesApiError } from '../themes/themesApi'

export type PickerState = { phase: 'loading' } | { phase: 'error'; message: string } | { phase: 'ready'; items: SourceItem[] }

function dateOf(s: string, lang: string): string {
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(lang)
}

export interface SourcePickerViewProps {
  server: string
  state: PickerState
  selected: string[]
  busy: boolean
  onToggle: (id: string) => void
  onAttach: () => void
  onClose: () => void
}

/** 見た目だけ（状態は持たない）。 */
export function SourcePickerView({ server, state, selected, busy, onToggle, onAttach, onClose }: SourcePickerViewProps) {
  const { t, i18n } = useTranslation('chat')
  return (
    <div className="source-picker-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="source-picker" role="dialog" aria-modal="true" aria-label={t('sourcePicker.title')} data-testid="source-picker">
        <div className="source-picker-head">
          <h2>{t('sourcePicker.title')}</h2>
          <span className="source-picker-from">{t('sourcePicker.from', { server })}</span>
        </div>
        <div className="source-picker-body">
          {state.phase === 'loading' && <p className="chat-note-line" data-testid="source-picker-loading">{t('sourcePicker.loading')}</p>}
          {state.phase === 'error' && (
            <p className="promote-err" role="alert" data-testid="source-picker-error">
              {state.message}
            </p>
          )}
          {state.phase === 'ready' && state.items.length === 0 && <p className="chat-note-line" data-testid="source-picker-empty">{t('sourcePicker.empty')}</p>}
          {state.phase === 'ready' && state.items.length > 0 && (
            <ul className="source-picker-list">
              {state.items.map((it) => (
                <li key={it.id}>
                  <label>
                    <input type="checkbox" checked={selected.includes(it.id)} disabled={busy} onChange={() => onToggle(it.id)} />
                    <span className="source-picker-name">{it.name}</span>
                    <span className="source-picker-meta">
                      {it.size !== null && formatSize(it.size)}
                      {it.uploadedAt && ` ${dateOf(it.uploadedAt, i18n.language)}`}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="source-picker-foot">
          <button type="button" className="btn btn--sm" onClick={onClose}>
            {t('sourcePicker.cancel')}
          </button>
          <button type="button" className="btn btn--sm btn--primary" data-testid="source-picker-attach" disabled={busy || selected.length === 0} onClick={onAttach}>
            {t('sourcePicker.attach')}
          </button>
        </div>
      </div>
    </div>
  )
}

export function SourcePicker({ server, onAttach, onClose }: { server: string; onAttach: (ids: string[]) => Promise<boolean>; onClose: () => void }) {
  const { t } = useTranslation('chat')
  const [state, setState] = useState<PickerState>({ phase: 'loading' })
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    fetchSourceItems(server, TABLE_EXTS).then(
      (items) => live && setState({ phase: 'ready', items }),
      (e: unknown) => live && setState({ phase: 'error', message: e instanceof ThemesApiError && e.detail ? e.detail : t('sourcePicker.failed') }),
    )
    return () => {
      live = false
    }
  }, [server, t])
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose])

  return (
    <SourcePickerView
      server={server}
      state={state}
      selected={selected}
      busy={busy}
      onToggle={(id) => setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))}
      onClose={onClose}
      onAttach={() => {
        setBusy(true)
        void onAttach(selected).then((ok) => {
          setBusy(false)
          if (ok) onClose()
        })
      }}
    />
  )
}
