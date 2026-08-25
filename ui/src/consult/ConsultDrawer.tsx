import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './ConsultDrawer.css'
import { isAbortError } from '../demoApi'
import { CloseIcon, SendIcon, StopIcon } from '../icons'
import { useLlmSettings } from '../settings/context'
import { LlmGate } from '../settings/LlmGate'
import { consult, type ConsultMessage } from './consultApi'
import { useConsultContext } from './consultContext'
import {
  failConsultAnswer,
  GENERAL_SLOT,
  resolveConsultAnswer,
  sendToSlot,
  stopConsultAnswer,
  threadForSlot,
  useConsultThreads,
  type ConsultThread,
  type ConsultTurn,
} from './consultThreads'

// The right-hand consult drawer (ADR design-consult-chat.md): a floating
// "相談する" button on every screen, sliding in a chat about "what does this
// column mean" / "how do I use this screen". D5: it never writes to the
// wizard's forms — the reply is text in a bubble, the user decides what (if
// anything) to do with it.

const MAX_HISTORY_TURNS = 20

function slotOf(dataset: string | undefined): string {
  return dataset && dataset.trim() ? dataset.trim() : 'draft'
}

/** Completed user/assistant turn pairs, oldest first, as the wire shape the
 *  consult endpoint takes (mirrors askThreads.historyFor, minus citations —
 *  the consult reply is plain text). */
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
  const { t } = useTranslation('consult')
  const [open, setOpen] = useState(false)
  const { isReady, getActiveCredentials } = useLlmSettings()
  const ctx = useConsultContext()
  const [tab, setTab] = useState<'session' | 'general'>('session')
  const sessionSlot = slotOf(ctx.dataset)
  const slot = tab === 'general' ? GENERAL_SLOT : sessionSlot

  const threads = useConsultThreads()
  // The slot -> thread-id binding: read once per slot from the persisted
  // index, then updated locally the moment a message starts a fresh thread
  // (sendToSlot already wrote the index; this just keeps this render in sync
  // without re-reading localStorage on every keystroke).
  const [threadIds, setThreadIds] = useState<{ session?: string; general?: string }>(() => ({
    session: threadForSlot(sessionSlot)?.id,
    general: threadForSlot(GENERAL_SLOT)?.id,
  }))
  // "state derived from a changed prop", adjusted during render (not an
  // effect — same pattern AskView uses for its prefill chip): the session
  // slot changes when the design's dataset name changes, and the bound
  // thread id must catch up before this render paints.
  const [seenSessionSlot, setSeenSessionSlot] = useState(sessionSlot)
  if (seenSessionSlot !== sessionSlot) {
    setSeenSessionSlot(sessionSlot)
    setThreadIds((ids) => ({ ...ids, session: threadForSlot(sessionSlot)?.id }))
  }

  const activeThreadId = tab === 'general' ? threadIds.general : threadIds.session
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
  }, [thread?.turns.length, open, tab])

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
    const before = historyOf(thread)
    const sent = sendToSlot(slot, text)
    if (!sent) return
    setThreadIds((ids) =>
      tab === 'general' ? { ...ids, general: sent.threadId } : { ...ids, session: sent.threadId },
    )
    void runTurn(sent.threadId, sent.assistantTurnId, [...before, { role: 'user', content: text }])
  }

  function stop() {
    const inflight = inflightRef.current
    if (!inflight || !thread) return
    inflight.controller.abort()
    stopConsultAnswer(thread.id, inflight.assistantTurnId)
  }

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
              <div className="consult-tabs">
                <button
                  type="button"
                  className={`consult-tab${tab === 'session' ? ' active' : ''}`}
                  onClick={() => setTab('session')}
                >
                  {ctx.dataset ? ctx.dataset : t('tabs.draft')}
                </button>
                <button
                  type="button"
                  className={`consult-tab${tab === 'general' ? ' active' : ''}`}
                  onClick={() => setTab('general')}
                >
                  {t('tabs.general')}
                </button>
              </div>
              <button
                type="button"
                className="consult-close"
                onClick={() => setOpen(false)}
                aria-label={t('close')}
                title={t('close')}
              >
                <CloseIcon size={16} />
              </button>
            </div>

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
                      // IME 変換確定の Enter で送らない（SkeletonGate/AskView 同様のガード）
                      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
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
                      <StopIcon size={16} />
                    </button>
                  ) : (
                    <button
                      type="submit"
                      className="consult-send"
                      disabled={!draft.trim()}
                      aria-label={t('send')}
                      title={t('send')}
                    >
                      <SendIcon size={16} />
                    </button>
                  )}
                </form>
              )}
              <p className="consult-hint">{t('hint')}</p>
            </div>
          </aside>
        </>
      )}
    </>
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
        <div className="consult-bubble consult-bubble--pending">{t('thinking')}</div>
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
      <div className="consult-bubble">{turn.result}</div>
    </div>
  )
}
