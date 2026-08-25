import { Send, Square } from 'lucide-react'
import { useEffect, useReducer, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './ConsultDrawer.css'
import { isAbortError } from '../demoApi'
import { CloseIcon, ThreadsIcon, TrashIcon } from '../icons'
import { useLlmSettings } from '../settings/context'
import { LlmGate } from '../settings/LlmGate'
import { consult, type ConsultMessage } from './consultApi'
import { useConsultContext } from './consultContext'
import { ConsultMarkdown } from './ConsultMarkdown'
import {
  appendConsultMessage,
  deleteConsultThread,
  failConsultAnswer,
  GENERAL_SLOT,
  rebindSlot,
  resolveConsultAnswer,
  sendToSlot,
  startNewInSlot,
  stopConsultAnswer,
  threadForSlot,
  unbindThreadEverywhere,
  useConsultThreads,
  type ConsultThread,
  type ConsultTurn,
} from './consultThreads'

// The right-hand consult drawer (ADR design-consult-chat.md). D5: it never
// writes to the wizard's forms — the reply is text in a bubble, the user
// decides what (if anything) to do with it.
//
// UI conforms to Graphium's AI chat panel (2026-08-25 review):
// `~/Graphium/src/features/ai-assistant/panel.tsx` — send/stop icon toggle,
// a chat-history list (title + date + message count, newest first), Cmd+Enter
// to send, spinner "thinking" state, destructive-toned errors. Ported as
// STRUCTURE only — styling uses Asterism's own CSS (this file), not Tailwind.

const MAX_HISTORY_TURNS = 20

function slotOf(dataset: string | undefined): string {
  return dataset && dataset.trim() ? dataset.trim() : 'draft'
}

/** Completed user/assistant turn pairs, oldest first, as the wire shape the
 *  consult endpoint takes (mirrors askThreads.historyFor, minus citations —
 *  the consult reply is plain text). This is what guarantees a follow-up
 *  question is answered WITH the earlier turns, not from a blank slate. */
function historyOf(thread: ConsultThread | undefined): ConsultMessage[] {
  if (!thread) return []
  const out: ConsultMessage[] = []
  const turns = thread.turns
  for (let i = 0; i < turns.length; i++) {
    const t = turns[i]
    if (t.role !== 'user') continue
    const a = turns[i + 1]
    if (!a || a.role !== 'assistant' || !a.result) continue
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
  const [tab, setTab] = useState<'session' | 'general'>('session')
  const sessionSlot = slotOf(ctx.dataset)
  const slot = tab === 'general' ? GENERAL_SLOT : sessionSlot

  // A thread the user picked explicitly (list selection, or "新しいチャット")
  // pins the conversation regardless of what the tab's slot binding says.
  // null = follow the current tab's slot binding (the normal, default mode).
  const [pinnedThreadId, setPinnedThreadId] = useState<string | null>(null)
  const [forcingNew, setForcingNew] = useState(false)

  const threads = useConsultThreads()

  // Keep a design session's conversation attached to the same thread when its
  // slug changes mid-wizard (`draft` -> the dataset's real name). Reacting to
  // an external system (the slot index) with a state bump, per the effect
  // rules — see rebindSlot's own doc for why this exists.
  const prevSessionSlot = useRef(sessionSlot)
  const [, bumpAfterRebind] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    if (prevSessionSlot.current !== sessionSlot) {
      rebindSlot(prevSessionSlot.current, sessionSlot)
      prevSessionSlot.current = sessionSlot
      bumpAfterRebind()
    }
  }, [sessionSlot])

  const boundThreadId = forcingNew ? undefined : threadForSlot(slot)?.id
  const activeThreadId = pinnedThreadId ?? boundThreadId ?? null
  const thread = threads.find((th) => th.id === activeThreadId)

  const [draft, setDraft] = useState('')
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
    let sent: ReturnType<typeof sendToSlot>
    if (activeThreadId) {
      const appended = appendConsultMessage(activeThreadId, text)
      sent = appended ? { threadId: activeThreadId, assistantTurnId: appended.assistantTurnId } : null
    } else if (forcingNew) {
      sent = startNewInSlot(slot, text)
    } else {
      sent = sendToSlot(slot, text)
    }
    if (!sent) return
    setForcingNew(false)
    setPinnedThreadId(sent.threadId)
    void runTurn(sent.threadId, sent.assistantTurnId, [...before, { role: 'user', content: text }])
  }

  function stop() {
    const inflight = inflightRef.current
    if (!inflight || !thread) return
    inflight.controller.abort()
    stopConsultAnswer(thread.id, inflight.assistantTurnId)
  }

  function selectTab(next: 'session' | 'general') {
    setTab(next)
    setPinnedThreadId(null)
    setForcingNew(false)
    setView('chat')
  }

  function selectThreadFromList(id: string) {
    setPinnedThreadId(id)
    setForcingNew(false)
    setView('chat')
  }

  function startNewChat() {
    setPinnedThreadId(null)
    setForcingNew(true)
    setView('chat')
  }

  function removeThread(id: string) {
    deleteConsultThread(id)
    unbindThreadEverywhere(id)
    if (pinnedThreadId === id) setPinnedThreadId(null)
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

            {view === 'chat' && (
              <div className="consult-tabs">
                <button
                  type="button"
                  className={`consult-tab${tab === 'session' ? ' active' : ''}`}
                  onClick={() => selectTab('session')}
                >
                  {ctx.dataset ? ctx.dataset : t('tabs.draft')}
                </button>
                <button
                  type="button"
                  className={`consult-tab${tab === 'general' ? ' active' : ''}`}
                  onClick={() => selectTab('general')}
                >
                  {t('tabs.general')}
                </button>
              </div>
            )}

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
                    thread.turns.map((tn) => <ConsultBubble key={tn.id} turn={tn} />)
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
                          <Square size={12} className="consult-send-icon-fill" />
                        </button>
                      ) : (
                        <button
                          type="submit"
                          className="consult-send"
                          disabled={!canSend}
                          aria-label={t('send')}
                          title={t('send')}
                        >
                          <Send size={12} />
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

function ConsultBubble({ turn }: { turn: ConsultTurn }) {
  const { t } = useTranslation('consult')
  if (turn.role === 'user') {
    return (
      <div className="consult-msg consult-msg--user">
        <div className="consult-bubble">{turn.text}</div>
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
    </div>
  )
}
