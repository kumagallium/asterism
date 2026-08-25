import { Send, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './ConsultDrawer.css'
import { isAbortError } from '../demoApi'
import { CloseIcon, PencilIcon, RetryIcon, ThreadsIcon, TrashIcon } from '../icons'
import { useLlmSettings } from '../settings/context'
import { LlmGate } from '../settings/LlmGate'
import { consult, type ConsultMessage } from './consultApi'
import { useConsultContext } from './consultContext'
import { ConsultMarkdown } from './ConsultMarkdown'
import {
  appendConsultMessage,
  deleteConsultThread,
  editConsultUserTurn,
  failConsultAnswer,
  latestConsultThreadId,
  regenerateConsultAnswer,
  resolveConsultAnswer,
  startConsultThread,
  stopConsultAnswer,
  useConsultThreads,
  type ConsultThread,
  type ConsultTurn,
} from './consultThreads'

// The right-hand consult drawer (ADR design-consult-chat.md). D5: it never
// writes to the wizard's forms — the reply is text in a bubble, the user
// decides what (if anything) to do with it.
//
// UI conforms to Graphium's AI chat panel (2026-08-25 review, revised same
// day per user ruling on thread scoping — ADR D2/D6/D7):
// `~/Graphium/src/features/ai-assistant/panel.tsx` — send/stop icon toggle, a
// FLAT chat-history list (no automatic topic binding — the user decides what
// a thread is about), Cmd+Enter to send, spinner "thinking" state,
// destructive-toned errors, user-message edit-and-resend + assistant-answer
// regenerate. Ported as STRUCTURE only — styling uses Asterism's own CSS
// (this file), not Tailwind.

const MAX_HISTORY_TURNS = 20

/** Completed user/assistant turn pairs, oldest first, as the wire shape the
 *  consult endpoint takes (mirrors askThreads.historyFor, minus citations —
 *  the consult reply is plain text). `beforeTurnId`, when given, stops before
 *  that turn — the cutoff an edit/regenerate re-sends from. This is what
 *  guarantees a follow-up question is answered WITH the earlier turns, not
 *  from a blank slate. */
function historyOf(thread: ConsultThread | undefined, beforeTurnId?: string): ConsultMessage[] {
  if (!thread) return []
  const out: ConsultMessage[] = []
  const turns = thread.turns
  for (let i = 0; i < turns.length; i++) {
    const t = turns[i]
    if (t.id === beforeTurnId) break
    if (t.role !== 'user') continue
    const a = turns[i + 1]
    if (!a || a.role !== 'assistant' || a.id === beforeTurnId || !a.result) continue
    out.push({ role: 'user', content: t.text })
    out.push({ role: 'assistant', content: a.result })
  }
  return out.slice(-MAX_HISTORY_TURNS)
}

export function ConsultDrawer() {
  const { t, i18n } = useTranslation('consult')
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<'chat' | 'list'>('chat')
  const { isReady, getActiveCredentials } = useLlmSettings()
  const ctx = useConsultContext()

  const threads = useConsultThreads()
  // Which thread is open — the user's own choice (D2 revised): whatever was
  // last touched when the drawer first mounted, thereafter only changed by
  // explicit selection ("+新しいチャット" / picking one from the list / a
  // send that creates the first-ever thread).
  const [activeThreadId, setActiveThreadId] = useState<string | null>(() => latestConsultThreadId())
  const thread = threads.find((th) => th.id === activeThreadId)

  const [draft, setDraft] = useState('')
  const [editingTurnId, setEditingTurnId] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const inflightRef = useRef<{ controller: AbortController; assistantTurnId: string } | null>(
    null,
  )

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [thread?.turns.length, open, view])

  const busy = !!thread?.turns.some((tn) => tn.role === 'assistant' && tn.pending)

  async function runTurn(threadId: string, assistantTurnId: string, messages: ConsultMessage[]) {
    const controller = new AbortController()
    inflightRef.current = { controller, assistantTurnId }
    try {
      const reply = await consult(messages, getActiveCredentials(), ctx, controller.signal)
      resolveConsultAnswer(threadId, assistantTurnId, reply)
    } catch (e) {
      if (isAbortError(e)) return // stopAnswer already settled the slot
      failConsultAnswer(threadId, assistantTurnId, e instanceof Error ? e.message : String(e))
    } finally {
      inflightRef.current = null
    }
  }

  function send() {
    const text = draft.trim()
    if (!text || busy || !isReady) return
    setDraft('')
    // The FULL prior history of the thread this message is about to join —
    // computed BEFORE the new turn is appended, from the thread as it stands
    // right now, so the server always sees the whole conversation so far.
    const before = historyOf(thread)
    const sent = activeThreadId
      ? (() => {
          const appended = appendConsultMessage(activeThreadId, text)
          return appended
            ? { threadId: activeThreadId, assistantTurnId: appended.assistantTurnId }
            : null
        })()
      : (() => {
          const started = startConsultThread(text)
          return { threadId: started.thread.id, assistantTurnId: started.assistantTurnId }
        })()
    if (!sent) return
    setActiveThreadId(sent.threadId)
    void runTurn(sent.threadId, sent.assistantTurnId, [...before, { role: 'user', content: text }])
  }

  function stop() {
    const inflight = inflightRef.current
    if (!inflight || !thread) return
    inflight.controller.abort()
    stopConsultAnswer(thread.id, inflight.assistantTurnId)
  }

  function selectThreadFromList(id: string) {
    setActiveThreadId(id)
    setEditingTurnId(null)
    setView('chat')
  }

  function startNewChat() {
    setActiveThreadId(null)
    setEditingTurnId(null)
    setView('chat')
  }

  function removeThread(id: string) {
    deleteConsultThread(id)
    if (activeThreadId === id) setActiveThreadId(null)
  }

  function editResend(userTurnId: string, newText: string) {
    const text = newText.trim()
    if (!text || busy || !thread) return
    const before = historyOf(thread, userTurnId)
    const attempt = editConsultUserTurn(thread.id, userTurnId, text)
    if (!attempt) return
    setEditingTurnId(null)
    void runTurn(thread.id, attempt.assistantTurnId, [...before, { role: 'user', content: text }])
  }

  function regenerate(assistantTurnId: string) {
    if (busy || !thread) return
    const idx = thread.turns.findIndex((tn) => tn.id === assistantTurnId)
    const precedingUser = thread.turns[idx - 1]
    if (!precedingUser || precedingUser.role !== 'user') return
    const before = historyOf(thread, precedingUser.id)
    const attempt = regenerateConsultAnswer(thread.id, assistantTurnId)
    if (!attempt) return
    void runTurn(thread.id, attempt.assistantTurnId, [
      ...before,
      { role: 'user', content: precedingUser.text },
    ])
  }

  const canSend = !busy && isReady && draft.trim().length > 0

  return (
    <>
      <button
        type="button"
        className="consult-fab"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={t('open')}
        title={t('open')}
      >
        {t('open')}
      </button>
      {open && (
        <>
          <div className="consult-backdrop" onClick={() => setOpen(false)} />
          <aside className="consult-drawer" role="dialog" aria-label={t('open')}>
            <div className="consult-head">
              <span className="consult-head-title">{t('open')}</span>
              <div className="consult-head-actions">
                <button
                  type="button"
                  className={`consult-icon-btn${view === 'list' ? ' active' : ''}`}
                  onClick={() => setView((v) => (v === 'list' ? 'chat' : 'list'))}
                  aria-label={t('history')}
                  title={t('history')}
                >
                  <ThreadsIcon size={15} />
                </button>
                <button
                  type="button"
                  className="consult-icon-btn"
                  onClick={() => setOpen(false)}
                  aria-label={t('close')}
                  title={t('close')}
                >
                  <CloseIcon size={16} />
                </button>
              </div>
            </div>

            {view === 'list' ? (
              <ConsultChatList
                threads={threads}
                lang={i18n.language}
                onSelect={selectThreadFromList}
                onNewChat={startNewChat}
                onDelete={removeThread}
              />
            ) : (
              <>
                <div className="consult-scroll" ref={scrollRef}>
                  {!thread || thread.turns.length === 0 ? (
                    <p className="consult-empty">{t('empty')}</p>
                  ) : (
                    thread.turns.map((tn) => (
                      <ConsultBubble
                        key={tn.id}
                        turn={tn}
                        busy={busy}
                        editing={editingTurnId === tn.id}
                        onStartEdit={() => setEditingTurnId(tn.id)}
                        onCancelEdit={() => setEditingTurnId(null)}
                        onEditResend={(text) => editResend(tn.id, text)}
                        onRegenerate={() => regenerate(tn.id)}
                      />
                    ))
                  )}
                </div>

                <div className="consult-foot">
                  {!isReady ? (
                    <div className="consult-gate">
                      <LlmGate />
                    </div>
                  ) : (
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
                        value={draft}
                        placeholder={t('inputPlaceholder')}
                        aria-label={t('inputPlaceholder')}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          // Cmd/Ctrl+Enter で送信。素の Enter は改行のまま
                          // （Graphium 準拠）。IME 変換確定の Enter は無視。
                          if (
                            e.key === 'Enter' &&
                            (e.metaKey || e.ctrlKey) &&
                            !e.nativeEvent.isComposing
                          ) {
                            e.preventDefault()
                            send()
                          }
                        }}
                      />
                      {busy ? (
                        <button
                          type="button"
                          className="consult-send consult-send--stop"
                          onClick={stop}
                          aria-label={t('stop')}
                          title={t('stop')}
                        >
                          <Square size={16} className="consult-send-icon-fill" />
                        </button>
                      ) : (
                        <button
                          type="submit"
                          className="consult-send"
                          disabled={!canSend}
                          aria-label={t('send')}
                          title={t('send')}
                        >
                          <Send size={16} />
                        </button>
                      )}
                    </form>
                  )}
                  <p className="consult-hint">{t('hint')}</p>
                </div>
              </>
            )}
          </aside>
        </>
      )}
    </>
  )
}

function ConsultChatList({
  threads,
  lang,
  onSelect,
  onNewChat,
  onDelete,
}: {
  threads: ConsultThread[]
  lang: string
  onSelect: (id: string) => void
  onNewChat: () => void
  onDelete: (id: string) => void
}) {
  const { t } = useTranslation('consult')
  const locale = lang.startsWith('en') ? 'en-US' : 'ja-JP'
  const sorted = [...threads].sort((a, b) => b.updatedAt - a.updatedAt)
  return (
    <div className="consult-list">
      <button type="button" className="consult-list-new" onClick={onNewChat}>
        {t('list.newChat')}
      </button>
      {sorted.length === 0 ? (
        <p className="consult-empty">{t('list.empty')}</p>
      ) : (
        <ul className="consult-list-items">
          {sorted.map((th) => {
            const firstUser = th.turns.find((tn) => tn.role === 'user')
            const preview = firstUser && firstUser.role === 'user' ? firstUser.text.slice(0, 40) : ''
            const date = new Date(th.updatedAt).toLocaleString(locale, {
              month: 'short',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            })
            return (
              <li key={th.id} className="consult-list-item">
                <button type="button" className="consult-list-item-main" onClick={() => onSelect(th.id)}>
                  <span className="consult-list-item-title">{preview || t('list.emptyChat')}</span>
                  <span className="consult-list-item-meta">
                    {date} · {t('list.messageCount', { count: th.turns.length })}
                  </span>
                </button>
                <button
                  type="button"
                  className="consult-list-item-delete"
                  onClick={() => onDelete(th.id)}
                  aria-label={t('list.delete')}
                  title={t('list.delete')}
                >
                  <TrashIcon size={13} />
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function ConsultBubble({
  turn,
  busy,
  editing,
  onStartEdit,
  onCancelEdit,
  onEditResend,
  onRegenerate,
}: {
  turn: ConsultTurn
  /** An answer is pending somewhere in this thread — edit/regenerate disabled. */
  busy: boolean
  editing: boolean
  onStartEdit: () => void
  onCancelEdit: () => void
  onEditResend: (text: string) => void
  onRegenerate: () => void
}) {
  const { t } = useTranslation('consult')
  const [draft, setDraft] = useState('')

  if (turn.role === 'user') {
    if (editing) {
      return (
        <div className="consult-msg consult-msg--user">
          <div className="consult-edit">
            <textarea
              className="consult-edit-input"
              rows={3}
              autoFocus
              defaultValue={turn.text}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  onEditResend(draft || turn.text)
                }
                if (e.key === 'Escape') {
                  e.preventDefault()
                  onCancelEdit()
                }
              }}
            />
            <div className="consult-edit-actions">
              <span className="consult-edit-note">{t('editDiscardNote')}</span>
              <button type="button" className="consult-edit-cancel" onClick={onCancelEdit}>
                {t('cancelEdit')}
              </button>
              <button
                type="button"
                className="consult-edit-confirm"
                disabled={!(draft || turn.text).trim()}
                onClick={() => onEditResend(draft || turn.text)}
              >
                {t('editAndResend')}
              </button>
            </div>
          </div>
        </div>
      )
    }
    return (
      <div className="consult-msg consult-msg--user">
        <div className="consult-bubble">{turn.text}</div>
        <button
          type="button"
          className="consult-turn-action consult-turn-action--user"
          onClick={onStartEdit}
          disabled={busy}
          aria-label={t('editMessage')}
          title={t('editMessage')}
        >
          <PencilIcon size={11} />
        </button>
      </div>
    )
  }
  if (turn.pending) {
    return (
      <div className="consult-msg consult-msg--assistant">
        <div className="consult-bubble consult-bubble--pending">
          <span className="consult-spinner" aria-hidden="true" />
          {t('thinking')}
        </div>
      </div>
    )
  }
  if (turn.error) {
    return (
      <div className="consult-msg consult-msg--assistant">
        <div className="consult-bubble consult-bubble--error">{turn.error}</div>
      </div>
    )
  }
  if (turn.stopped || turn.interrupted) {
    return (
      <div className="consult-msg consult-msg--assistant">
        <div className="consult-bubble consult-bubble--error">
          {t(turn.stopped ? 'stoppedNote' : 'interruptedNote')}
        </div>
      </div>
    )
  }
  return (
    <div className="consult-msg consult-msg--assistant">
      <div className="consult-bubble consult-bubble--markdown">
        <ConsultMarkdown text={turn.result ?? ''} />
      </div>
      <button
        type="button"
        className="consult-turn-action consult-turn-action--assistant"
        onClick={onRegenerate}
        disabled={busy}
        aria-label={t('regenerate')}
        title={t('regenerate')}
      >
        <RetryIcon size={11} />
      </button>
    </div>
  )
}
