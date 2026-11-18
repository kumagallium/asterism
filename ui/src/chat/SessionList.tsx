// チャットの一覧（PR 14d-1 で縮めた）。「新しいチャット」と各チャットの 1 行（題＋提案の印＋「…」）だけ。
// ブランド・≡・⚙・たたんだ形は NavRail が持つ。鍵が無い（401/503）ときは一覧の代わりに 1 行。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useImeEnterGuard } from './useImeEnterGuard'
import { AddIcon } from '../icons'
import { MoreMenu } from '../MoreMenu'
import type { ThemesApiError } from '../themes/themesApi'
import { deleteSession, isKeyMissing, renameSession, type SessionSummary } from './chatApi'
import { sessionTitle } from './chatState'

export interface SessionListProps {
  /** 読み込み前は null。 */
  sessions: SessionSummary[] | null
  /** 一覧の読み込みの失敗（401/503＝鍵が無い）。 */
  error: ThemesApiError | Error | null
  activeId: string | null
  onNew: () => void
  onOpen: (id: string) => void
  /** 名前を直した・消したあと（一覧を取り直す）。 */
  onChanged: () => void
  onDeleted: (id: string) => void
}

export function SessionList(p: SessionListProps) {
  const { t } = useTranslation('chat')
  const { compositionHandlers, isImeKey } = useImeEnterGuard()
  const [renaming, setRenaming] = useState<string | null>(null)
  const [draftName, setDraftName] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function commitRename(id: string) {
    const title = draftName.trim().replace(/\s+/g, ' ').slice(0, 80)
    setRenaming(null)
    if (!title) return
    setBusy(true)
    setErr(null)
    try {
      await renameSession(id, title)
      p.onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  async function confirmDelete(id: string) {
    setBusy(true)
    setErr(null)
    try {
      await deleteSession(id)
      setDeleting(null)
      p.onDeleted(id)
      p.onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="chat-list-rows-wrap">
      <button type="button" className="btn btn--soft btn--sm chat-new" data-testid="session-new" onClick={p.onNew}>
        <AddIcon size={16} />
        {t('list.new')}
      </button>
      <div className="chat-rows">
        {p.error ? (
          isKeyMissing(p.error) ? (
            <p className="chat-note-line">{t('list.noKey')}</p>
          ) : (
            <p className="promote-err" role="alert">
              {p.error.message}
            </p>
          )
        ) : p.sessions === null ? (
          <p className="chat-note-line">{t('list.loading')}</p>
        ) : (
          p.sessions.map((s) => {
            const title = sessionTitle(s, t('list.untitled'))
            const active = s.id === p.activeId
            if (deleting === s.id) {
              return (
                <div key={s.id} className="chat-row chat-row--ask" data-testid="session-row">
                  <p className="chat-note-line">{t('list.deleteAsk')}</p>
                  <div className="chat-row-actions">
                    <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void confirmDelete(s.id)}>
                      {t('list.deleteYes')}
                    </button>
                    <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDeleting(null)}>
                      {t('list.deleteNo')}
                    </button>
                  </div>
                </div>
              )
            }
            if (renaming === s.id) {
              return (
                <div key={s.id} className="chat-row" data-testid="session-row">
                  <input
                    className="chat-row-input"
                    autoFocus
                    value={draftName}
                    maxLength={80}
                    aria-label={t('list.renameLabel')}
                    onChange={(e) => setDraftName(e.target.value)}
                    onBlur={() => setRenaming(null)}
                    {...compositionHandlers}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !isImeKey(e)) void commitRename(s.id)
                      else if (e.key === 'Escape') setRenaming(null)
                    }}
                  />
                </div>
              )
            }
            return (
              <div key={s.id} className={`chat-row${active ? ' is-active' : ''}`} data-testid="session-row">
                <button type="button" className="chat-row-main" aria-current={active ? 'page' : undefined} onClick={() => p.onOpen(s.id)}>
                  <span className="chat-row-title">{title}</span>
                  {s.proposed > 0 && <span className="chat-row-badge">{t('list.proposed', { n: s.proposed })}</span>}
                </button>
                <MoreMenu
                  className="chat-row-more"
                  label={t('list.menu')}
                  items={[
                    {
                      key: 'rename',
                      label: t('list.rename'),
                      onSelect: () => {
                        setDraftName(s.title)
                        setRenaming(s.id)
                      },
                    },
                    { key: 'delete', label: t('list.delete'), onSelect: () => setDeleting(s.id) },
                  ]}
                />
              </div>
            )
          })
        )}
        {err && (
          <p className="promote-err" role="alert">
            {err}
          </p>
        )}
      </div>
    </div>
  )
}
