import { useEffect, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { takeAskPrefill } from './askPrefill'
import {
  type AskAssistantTurn,
  type AskThread,
  appendQuestion,
  deleteThread,
  failAnswer,
  getThread,
  historyFor,
  isThreadBusy,
  registerInflight,
  renameThread,
  resolveAnswer,
  retryAnswer,
  startThread,
  stopAnswer,
  unregisterInflight,
  useAskThreads,
} from './askThreads'
import { CitationCard } from './CitationCard'
import { ask, isAbortError, isMockMode, type AskResponse, type Citation } from './demoApi'
import {
  AddIcon,
  BrandMark,
  CheckIcon,
  CloseIcon,
  PencilIcon,
  RetryIcon,
  SendIcon,
  StopIcon,
  ThreadsIcon,
  TrashIcon,
} from './icons'
import { ProvenanceTrace } from './ProvenanceTrace'
import { useLlmSettings } from './settings/context'
import { LlmGate } from './settings/LlmGate'

// Ask REQUIRES a configured model: the AI uses it to route the question to the
// verified tools (it only picks the tool + args; the facts/citations come from the
// deterministic tool, not the AI). The active model + its key come from Settings
// (shared across the app); for key-free deterministic tool execution, use the
// catalog's ツール tab instead.

/**
 * Ask view — a chat (ADR ask-chat-threads.md): a thread list on the left, the
 * conversation in the middle (question bubbles + grounded answer cards with
 * citations / data-quality notes / disclosed SPARQL), a composer at the bottom,
 * and the provenance trace as a right-hand panel that opens when a citation is
 * clicked. Threads persist per browser (askThreads.ts); every question is sent
 * with the thread's earlier turns as `history` so follow-ups resolve, while the
 * agent itself stays stateless. This view only calls the contract (ask /
 * provenance); the answer is produced by the demo agent (consumption layer).
 *
 * Routing: `#/ask` = a new chat (transient until the first message);
 * `#/ask/<threadId>` = an existing thread (reload / back / forward keep it).
 */
export function AskView({
  onShowVocab,
  threadId,
  onSelectThread,
}: {
  onShowVocab?: (className: string) => void
  /** Active thread id from the route (null = new chat). */
  threadId: string | null
  /** Change the active thread (null = new chat); the App writes the hash. */
  onSelectThread: (id: string | null, opts?: { replace?: boolean }) => void
}) {
  const { t } = useTranslation()
  const threads = useAskThreads()
  const thread = threadId ? threads.find((th) => th.id === threadId) : undefined
  const { isReady, getActiveCredentials } = useLlmSettings()
  const keyMissing = !isReady && !isMockMode

  // Provenance panel selection, tagged with the thread it belongs to so a
  // citation picked in one thread does not stay open after switching (back /
  // forward change the route without going through our click handlers).
  const [picked, setPicked] = useState<{ threadId: string | null; citation: Citation } | null>(null)
  const selected = picked && picked.threadId === (threadId ?? null) ? picked.citation : null

  // Narrow screens: the thread list becomes an overlay toggled from the topline.
  const [threadsOpen, setThreadsOpen] = useState(false)

  // Esc closes whichever panel is open (the provenance pane / the thread overlay).
  const panelOpen = !!selected || threadsOpen
  useEffect(() => {
    if (!panelOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setPicked(null)
      setThreadsOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [panelOpen])

  const busy = isThreadBusy(thread)

  // One ask() call = one attempt on one answer slot. The store only accepts the
  // outcome if the slot is still pending for THIS attempt (a stopped or retried
  // slot ignores late responses); the AbortController is registered so "stop"
  // can cut the wait from anywhere.
  async function runAsk(tid: string, assistantTurnId: string, question: string, attempt: number) {
    const th = getThread(tid)
    const history = th ? historyFor(th, assistantTurnId) : []
    const ctrl = new AbortController()
    registerInflight(assistantTurnId, ctrl)
    try {
      const res = await ask(question, getActiveCredentials(), history, ctrl.signal)
      resolveAnswer(tid, assistantTurnId, res, attempt)
    } catch (e) {
      // Aborted = the user pressed stop; the slot is already marked stopped.
      if (!isAbortError(e)) {
        failAnswer(tid, assistantTurnId, e instanceof Error ? e.message : String(e), attempt)
      }
    } finally {
      unregisterInflight(assistantTurnId, ctrl)
    }
  }

  function send(text: string) {
    const q = text.trim()
    if (!q || keyMissing) return
    if (thread) {
      if (isThreadBusy(thread)) return // one question at a time per thread
      const added = appendQuestion(thread.id, q)
      if (added) void runAsk(thread.id, added.assistantTurnId, q, added.attempt)
      return
    }
    // First message of a new chat: the thread is created now (not on "new
    // chat" — empty threads never pile up) and the URL is replaced so Back
    // returns to wherever the user came from, not to an empty new chat.
    const started = startThread(q)
    onSelectThread(started.thread.id, { replace: true })
    void runAsk(started.thread.id, started.assistantTurnId, q, started.attempt)
  }

  function retry(assistantTurnId: string) {
    if (!thread || isThreadBusy(thread)) return
    const again = retryAnswer(thread.id, assistantTurnId)
    if (again) void runAsk(thread.id, assistantTurnId, again.question, again.attempt)
  }

  // Stop waiting for the current thread's pending answer (the agent's work is
  // not cancelled server-side; the slot becomes retry-able).
  function stop() {
    const slot = thread?.turns.find((t) => t.role === 'assistant' && t.pending)
    if (thread && slot) stopAnswer(thread.id, slot.id)
  }

  function selectThread(id: string | null) {
    setThreadsOpen(false)
    if (id !== (threadId ?? null)) onSelectThread(id)
  }

  function removeThread(id: string) {
    deleteThread(id)
    if (id === threadId) onSelectThread(null, { replace: true })
  }

  const title = thread ? thread.title : t('ask:threads.newTitle')

  return (
    <div className={`chat${selected ? ' chat--trace' : ''}${threadsOpen ? ' chat--threads-open' : ''}`}>
      <ThreadList
        threads={threads}
        activeId={thread?.id ?? null}
        onSelect={selectThread}
        onDelete={removeThread}
        onRename={renameThread}
        onClose={() => setThreadsOpen(false)}
      />
      {/* Backdrop for the narrow-screen thread overlay (CSS shows it only there). */}
      <button
        type="button"
        className="chat-threads-backdrop"
        aria-label={t('ask:threads.close')}
        tabIndex={-1}
        onClick={() => setThreadsOpen(false)}
      />

      <section className="chat-main" aria-label={t('ask:conversationAria')}>
        <div className="chat-topline">
          <button
            type="button"
            className="chat-topline-btn chat-threads-toggle"
            onClick={() => setThreadsOpen((v) => !v)}
            aria-expanded={threadsOpen}
            aria-label={t('ask:threads.toggle')}
            title={t('ask:threads.toggle')}
          >
            <ThreadsIcon size={18} />
          </button>
          <h2 className="chat-topline-title" title={title}>
            {title}
          </h2>
          {isMockMode && <span className="demo-badge">{t('ask:demoBadge')}</span>}
        </div>

        {/* Both keyed by thread so per-thread state (scroll follow, draft) resets on
            switch; distinct prefixes because siblings must not share a key. */}
        <Conversation
          key={`conversation:${thread?.id ?? 'new'}`}
          thread={thread}
          selectedIri={selected?.iri ?? null}
          onSelectCitation={(c) => setPicked({ threadId: threadId ?? null, citation: c })}
          onShowVocab={onShowVocab}
          onRetry={retry}
          onExample={send}
        />

        <Composer
          key={`composer:${thread?.id ?? 'new'}`}
          disabled={keyMissing}
          busy={busy}
          onSend={send}
          onStop={stop}
        />
      </section>

      {selected && (
        <ProvenanceTrace
          citation={selected}
          onShowVocab={onShowVocab}
          onClose={() => setPicked(null)}
        />
      )}
    </div>
  )
}

// ---- thread list (left column) ------------------------------------------------

type GroupKey = 'today' | 'yesterday' | 'week' | 'older'

// "Now" for the today/yesterday grouping — sampled once at mount and refreshed
// each minute (so a tab left open rolls its groups over at midnight) rather than
// read during render, which would make the render impure.
function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(id)
  }, [])
  return now
}

function groupOf(updatedAt: number, now: number): GroupKey {
  const startOfToday = new Date(now)
  startOfToday.setHours(0, 0, 0, 0)
  const day = 24 * 60 * 60 * 1000
  if (updatedAt >= startOfToday.getTime()) return 'today'
  if (updatedAt >= startOfToday.getTime() - day) return 'yesterday'
  if (updatedAt >= startOfToday.getTime() - 6 * day) return 'week'
  return 'older'
}

function ThreadList({
  threads,
  activeId,
  onSelect,
  onDelete,
  onRename,
  onClose,
}: {
  threads: AskThread[]
  activeId: string | null
  onSelect: (id: string | null) => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
  onClose: () => void
}) {
  const { t, i18n } = useTranslation()
  const [confirmId, setConfirmId] = useState<string | null>(null)
  // Inline rename: which row is being edited (the input is keyed by row, so
  // the draft starts from that row's title and resets when editing ends).
  const [editingId, setEditingId] = useState<string | null>(null)
  const now = useNow()
  const groups: { key: GroupKey; items: AskThread[] }[] = []
  for (const th of threads) {
    const key = groupOf(th.updatedAt, now)
    const g = groups[groups.length - 1]
    if (g && g.key === key) g.items.push(th)
    else groups.push({ key, items: [th] })
  }
  const dateFmt = new Intl.DateTimeFormat(i18n.language.startsWith('en') ? 'en' : 'ja', {
    month: 'short',
    day: 'numeric',
  })

  return (
    <nav className="chat-threads" aria-label={t('ask:threads.aria')}>
      <div className="chat-threads-head">
        <button type="button" className="btn btn--soft chat-new-btn" onClick={() => onSelect(null)}>
          <AddIcon size={16} /> {t('ask:threads.new')}
        </button>
        <button
          type="button"
          className="chat-topline-btn chat-threads-close"
          onClick={onClose}
          aria-label={t('ask:threads.close')}
          title={t('ask:threads.close')}
        >
          <CloseIcon size={16} />
        </button>
      </div>
      <div className="chat-threads-list">
        {threads.length === 0 && <p className="chat-threads-empty">{t('ask:threads.empty')}</p>}
        {groups.map((g) => (
          <section key={g.key} className="chat-threads-group">
            <h3 className="chat-threads-group-h">{t(`ask:threads.group.${g.key}`)}</h3>
            <ul className="chat-threads-ul">
              {g.items.map((th) => {
                const active = th.id === activeId
                const confirming = confirmId === th.id
                const editing = editingId === th.id
                const n = th.turns.filter((x) => x.role === 'user').length
                return (
                  <li
                    key={th.id}
                    className={`chat-thread-item${active ? ' active' : ''}${editing ? ' editing' : ''}`}
                  >
                    {editing ? (
                      <RenameField
                        key={`rename:${th.id}`}
                        initial={th.title}
                        onCommit={(title) => {
                          setEditingId(null)
                          onRename(th.id, title)
                        }}
                        onCancel={() => setEditingId(null)}
                      />
                    ) : confirming ? (
                      <div className="chat-thread-confirm" role="group" aria-label={t('ask:threads.deleteConfirm')}>
                        <span className="chat-thread-confirm-text">{t('ask:threads.deleteConfirm')}</span>
                        <button
                          type="button"
                          className="btn btn--danger btn--sm"
                          onClick={() => {
                            setConfirmId(null)
                            onDelete(th.id)
                          }}
                        >
                          {t('ask:threads.deleteYes')}
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => setConfirmId(null)}
                        >
                          {t('ask:threads.deleteNo')}
                        </button>
                      </div>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="chat-thread-btn"
                          onClick={() => onSelect(th.id)}
                          onDoubleClick={() => setEditingId(th.id)}
                          aria-current={active ? 'page' : undefined}
                          title={th.title}
                        >
                          <span className="chat-thread-title">{th.title || t('ask:threads.untitled')}</span>
                          <span className="chat-thread-meta">
                            <span>{dateFmt.format(th.updatedAt)}</span>
                            <span aria-hidden="true">·</span>
                            <span>{t('ask:threads.turns', { count: n })}</span>
                            {isThreadBusy(th) && <span className="spinner" aria-label={t('ask:answering')} />}
                          </span>
                        </button>
                        <span className="chat-thread-actions">
                          <button
                            type="button"
                            className="chat-thread-action"
                            onClick={() => setEditingId(th.id)}
                            aria-label={t('ask:threads.rename', { title: th.title })}
                            title={t('ask:threads.renameTitle')}
                          >
                            <PencilIcon size={15} />
                          </button>
                          <button
                            type="button"
                            className="chat-thread-action chat-thread-action--danger"
                            onClick={() => setConfirmId(th.id)}
                            aria-label={t('ask:threads.delete', { title: th.title })}
                            title={t('ask:threads.deleteTitle')}
                          >
                            <TrashIcon size={15} />
                          </button>
                        </span>
                      </>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>
    </nav>
  )
}

// Inline rename input for one thread row: Enter / blur commit, Esc cancels.
function RenameField({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string
  onCommit: (title: string) => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [value, setValue] = useState(initial)
  // Esc must not also "commit on blur" when the input unmounts — remember it.
  const cancelledRef = useRef(false)
  return (
    <input
      type="text"
      className="chat-thread-rename"
      value={value}
      autoFocus
      aria-label={t('ask:threads.renameTitle')}
      placeholder={t('ask:threads.renamePlaceholder')}
      onFocus={(e) => e.currentTarget.select()}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.nativeEvent.isComposing) return // IME: let the conversion finish
        if (e.key === 'Enter') {
          e.preventDefault()
          onCommit(value)
        } else if (e.key === 'Escape') {
          e.preventDefault()
          cancelledRef.current = true
          onCancel()
        }
      }}
      onBlur={() => {
        if (!cancelledRef.current) onCommit(value)
      }}
    />
  )
}

// ---- conversation (middle column) ---------------------------------------------

function Conversation({
  thread,
  selectedIri,
  onSelectCitation,
  onShowVocab,
  onRetry,
  onExample,
}: {
  thread: AskThread | undefined
  selectedIri: string | null
  onSelectCitation: (c: Citation) => void
  onShowVocab?: (className: string) => void
  onRetry: (assistantTurnId: string) => void
  onExample: (question: string) => void
}) {
  const { t } = useTranslation()
  const endRef = useRef<HTMLDivElement | null>(null)
  // Follow the conversation while the reader is at (or near) the bottom; a
  // reader who scrolled up to re-read an earlier answer is not yanked down
  // when a new answer arrives. A question the user just sent always scrolls.
  const atBottomRef = useRef(true)
  const turns = thread?.turns
  useEffect(() => {
    if (!turns) return
    const last = turns[turns.length - 1]
    const justAsked = last?.role === 'assistant' && last.pending
    if (atBottomRef.current || justAsked) endRef.current?.scrollIntoView({ block: 'end' })
  }, [turns])

  if (!thread || thread.turns.length === 0) {
    return (
      <div className="chat-scroll">
        <div className="chat-empty">
          <span className="chat-empty-mark">
            <BrandMark size={44} />
          </span>
          <h3 className="chat-empty-title">{t('ask:empty.title')}</h3>
          <p className="chat-empty-intro">
            <Trans
              i18nKey="ask:intro"
              components={[
                <strong key="0" />,
                <strong key="1" />,
                <strong key="2" />,
                <strong key="3" />,
                <strong key="4" />,
                <strong key="5" />,
                <strong key="6" />,
              ]}
            />
          </p>
          {isMockMode && (
            <div className="chat-examples" aria-label={t('ask:examples.aria')}>
              {(t('ask:examples.items', { returnObjects: true }) as string[]).map((q) => (
                <button key={q} type="button" className="chat-example" onClick={() => onExample(q)}>
                  {q}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    )
  }

  const busy = isThreadBusy(thread)
  return (
    <div
      className="chat-scroll"
      onScroll={(e) => {
        const el = e.currentTarget
        atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120
      }}
    >
      {/* Answers arrive seconds later, asynchronously — announce arrivals. */}
      <div className="chat-thread" aria-live="polite">
        {thread.turns.map((turn) =>
          turn.role === 'user' ? (
            <div key={turn.id} className="chat-msg chat-msg--user">
              <div className="chat-bubble">{turn.text}</div>
            </div>
          ) : (
            <AnswerMessage
              key={turn.id}
              turn={turn}
              selectedIri={selectedIri}
              onSelectCitation={onSelectCitation}
              onShowVocab={onShowVocab}
              onRetry={() => onRetry(turn.id)}
              retryable={!busy}
            />
          ),
        )}
        <div ref={endRef} />
      </div>
    </div>
  )
}

function AnswerMessage({
  turn,
  selectedIri,
  onSelectCitation,
  onShowVocab,
  onRetry,
  retryable,
}: {
  turn: AskAssistantTurn
  selectedIri: string | null
  onSelectCitation: (c: Citation) => void
  onShowVocab?: (className: string) => void
  onRetry: () => void
  retryable: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="chat-msg chat-msg--assistant">
      <span className="chat-avatar" aria-hidden="true">
        <BrandMark size={18} />
      </span>
      <div className="chat-msg-body">
        {turn.pending && (
          <div className="chat-pending" role="status">
            <span className="spinner" />
            {t('ask:answering')}
          </div>
        )}
        {!turn.pending && !turn.result && (
          <div className={`chat-msg-error${turn.stopped ? ' chat-msg-error--stopped' : ''}`} role="alert">
            <span className="chat-msg-error-text">
              {turn.stopped
                ? t('ask:stopped')
                : turn.interrupted
                  ? t('ask:interrupted')
                  : turn.error || t('ask:failed')}
            </span>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onRetry}
              disabled={!retryable}
            >
              <RetryIcon size={14} /> {t('ask:retry')}
            </button>
          </div>
        )}
        {turn.result && (
          <AnswerCard
            result={turn.result}
            selectedIri={selectedIri}
            onSelectCitation={onSelectCitation}
            onShowVocab={onShowVocab}
          />
        )}
      </div>
    </div>
  )
}

/** One grounded answer: provenance badge, Markdown answer, citations, notes,
 *  disclosed SPARQL — the same "引用できる事実" card as before, now one turn. */
function AnswerCard({
  result,
  selectedIri,
  onSelectCitation,
  onShowVocab,
}: {
  result: AskResponse
  selectedIri: string | null
  onSelectCitation: (c: Citation) => void
  onShowVocab?: (className: string) => void
}) {
  const { t } = useTranslation()
  const verified = result.verifiedTools?.length ?? 0
  return (
    <section className="answer-card">
      <div className="answer-head">
        {verified > 0 ? (
          <span className="answer-badge answer-badge-verified">
            <CheckIcon size={13} />{' '}
            {t('ask:badge.verifiedTools', {
              tools: result.verifiedTools!.map((vt) => vt.title).join(' · '),
            })}
          </span>
        ) : result.unverifiedSparql ? (
          <span className="answer-badge answer-badge-unverified">{t('ask:badge.unverifiedSparql')}</span>
        ) : (
          <span className="answer-badge">
            <CheckIcon size={13} /> {t('ask:badge.grounded')}
          </span>
        )}
        {result.unverifiedSparql && verified > 0 && (
          <span className="answer-badge answer-badge-unverified">{t('ask:badge.plusUnverifiedSparql')}</span>
        )}
        <span className="answer-head-note">
          {verified > 0
            ? t('ask:headNote.verified')
            : result.unverifiedSparql
              ? t('ask:headNote.unverified')
              : t('ask:headNote.grounded')}
        </span>
      </div>
      {/* The LLM escape can return Markdown (GFM tables / lists); typed
          answers are plain sentences. Render as Markdown so a table is a
          table, not raw "| … |" pipes. */}
      <div className="answer-text answer-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.answer}</ReactMarkdown>
      </div>
      {/* The tools' own caveats about the DATA (not the model's prose): a
          number stored as text under ORDER BY makes the "maximum" above
          unreliable — say so right under the answer, deterministically. */}
      {(result.warnings?.length ?? 0) > 0 && (
        <div className="answer-warnings" role="alert">
          {result.warnings!.map((w, i) => (
            <p key={i} className="answer-warning">
              ⚠{' '}
              {w.kind === 'untyped-numeric-compare'
                ? t('ask:warning.untypedNumeric', { variable: w.variable ?? '?' })
                : w.message}
            </p>
          ))}
        </div>
      )}

      {result.citations.length > 0 && (
        <div className="citations">
          <h3 className="section-h">
            {t('ask:citations.heading')}
            <span className="section-h-hint">{t('ask:citations.hint')}</span>
          </h3>
          <div className="citation-list">
            {result.citations.map((c) => (
              <CitationCard
                key={c.iri}
                citation={c}
                selected={selectedIri === c.iri}
                onSelect={onSelectCitation}
                onShowVocab={onShowVocab}
              />
            ))}
          </div>
        </div>
      )}

      {result.notes.length > 0 && (
        <div className="notes">
          <h3 className="section-h">{t('ask:notes.heading')}</h3>
          <ul className="notes-list">
            {result.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}

      {result.sparql.length > 0 && (
        <details className="sparql-disclosure">
          <summary>
            {t('ask:sparql.summary', { n: result.sparql.length })}
            <span className="sparql-disclosure-tag">{t('ask:sparql.readonlyTag')}</span>
          </summary>
          <p className="sparql-disclosure-hint">{t('ask:sparql.hint')}</p>
          {result.sparql.map((q, i) => (
            <SparqlBlock key={i} query={q} />
          ))}
        </details>
      )}
    </section>
  )
}

// ---- composer (bottom of the middle column) -----------------------------------

const COMPOSER_MAX_HEIGHT = 200

function Composer({
  disabled,
  busy,
  onSend,
  onStop,
}: {
  /** No model configured (live mode) — sending is blocked; the gate says why. */
  disabled: boolean
  /** An answer is pending in this thread — one question at a time; the send
   *  button becomes a stop button. */
  busy: boolean
  onSend: (text: string) => void
  onStop: () => void
}) {
  const { t } = useTranslation()
  // The composer is keyed by thread, so a question handed over from another
  // screen (かんたん S9 chip → askPrefill.ts) is consumed exactly once, on the
  // mount that follows the navigation. It only fills the box — the human sends.
  const [draft, setDraft] = useState(() => takeAskPrefill() ?? '')
  const taRef = useRef<HTMLTextAreaElement | null>(null)

  // Auto-grow to the content (one line → up to COMPOSER_MAX_HEIGHT, then scroll).
  // An empty box just keeps its natural one-row height (measuring the wrapped
  // placeholder at mount, before layout settles, over-estimated it).
  useEffect(() => {
    const el = taRef.current
    if (!el) return
    if (!draft) {
      el.style.height = ''
      return
    }
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_HEIGHT)}px`
  }, [draft])

  const canSend = !disabled && !busy && draft.trim().length > 0

  function submit() {
    if (!canSend) return
    onSend(draft)
    setDraft('')
  }

  return (
    <form
      className="chat-composer"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <div className={`chat-composer-box${disabled ? ' chat-composer-box--disabled' : ''}`}>
        <textarea
          ref={taRef}
          className="chat-input"
          rows={1}
          value={draft}
          placeholder={t('ask:inputPlaceholder')}
          aria-label={t('ask:inputPlaceholder')}
          disabled={disabled}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter inserts a newline. Don't send on the
            // Enter that confirms an IME (kanji/かな) conversion — without the
            // isComposing guard Japanese input is impossible.
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              submit()
            }
          }}
        />
        {busy ? (
          <button
            type="button"
            className="chat-send chat-send--stop"
            onClick={onStop}
            aria-label={t('ask:stop')}
            title={t('ask:stopTitle')}
          >
            <StopIcon size={18} />
          </button>
        ) : (
          <button
            type="submit"
            className="chat-send"
            disabled={!canSend}
            aria-label={t('ask:send')}
            title={disabled ? t('ask:submitTitle') : t('ask:send')}
          >
            <SendIcon size={18} />
          </button>
        )}
      </div>
      <div className="chat-composer-foot">
        <span className="chat-composer-hint">{t('ask:composerHint')}</span>
        {!isMockMode && (
          <div className="chat-composer-gate">
            <LlmGate />
          </div>
        )}
      </div>
    </form>
  )
}

// 開示された SPARQL は「読み取り専用の追試」への入口 — コピーして SPARQL 画面で
// そのまま再実行できるよう、ブロックごとにコピーボタンを付ける。
function SparqlBlock({ query }: { query: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard
      ?.writeText(query)
      .then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1600)
      })
      .catch(() => {})
  }
  return (
    <div className="sparql-block-wrap">
      <pre className="sparql-block">{query}</pre>
      <button type="button" className="btn btn--ghost btn--sm sparql-block-copy" onClick={copy}>
        {copied ? t('ask:sparql.copied') : t('ask:sparql.copy')}
      </button>
    </div>
  )
}
