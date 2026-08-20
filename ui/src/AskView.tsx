import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getAppDataInfo } from './appdata'
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
  useAskThreadsLoaded,
} from './askThreads'
import { CitationCard } from './CitationCard'
import { ask, isAbortError, isMockMode, type AskResponse, type Citation } from './demoApi'
import {
  catalogClassNames,
  findDatasetByIri,
  getCatalogDatasets,
  isAskable,
  registryIdOf,
  type CatalogDataset,
} from './galleryApi'
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
import { SourcePanel } from './SourcePanel'
import { useLlmSettings } from './settings/context'
import { LlmGate } from './settings/LlmGate'

/**
 * Which plain sentence a failed turn gets. Ask never prints the raw error: it is
 * an English HTTP/JSON line (`ask failed (HTTP 500): {"detail":…}`, `Failed to
 * fetch`) shown at the moment the reader is most disappointed, and it names no
 * next step. The raw string is still available, folded under 技術情報.
 *
 * Deliberately narrow: the bare word "token" is NOT a settings marker (a model
 * answer can mention tokens), and only an auth-shaped failure routes to Settings.
 */
type AskErrorKind = 'settings' | 'timeout' | 'network' | 'other'

function classifyAskError(raw: string): AskErrorKind {
  const s = raw.toLowerCase()
  if (/\b(401|403)\b|unauthorized|forbidden|api[ _-]?key|authentication|invalid_api_key/.test(s))
    return 'settings'
  if (/timeout|timed out|etimedout|deadline exceeded/.test(s)) return 'timeout'
  if (/failed to fetch|networkerror|load failed|econnrefused|network error|\b5\d\d\b/.test(s))
    return 'network'
  return 'other'
}

/** What a citation needs from the catalog, resolved once per catalog load.
 *  Both are deterministic reads of the IRI a citation already carries — nothing
 *  is inferred from a dataset's display name and no IRI is ever constructed. */
interface AskCatalog {
  /** Class names that exist in catalogued datasets (the ことば link, ASK-38). */
  vocabClasses: ReadonlySet<string>
  /** The dataset that minted an ID, when it can be told. */
  datasetFor: (iri: string) => CatalogDataset | undefined
  /** The dataset a verified tool belongs to — an aggregate answer names no IRI,
   *  so its source can only be found by the tool's own dataset id. */
  datasetById: (id: string) => CatalogDataset | undefined
}

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
  onAddData,
  onOpenDataset,
}: {
  onShowVocab?: (className: string) => void
  /** Active thread id from the route (null = new chat). */
  threadId: string | null
  /** Change the active thread (null = new chat); the App writes the hash. */
  onSelectThread: (id: string | null, opts?: { replace?: boolean }) => void
  /** Go to データを追加 — the next step when nothing is published yet. */
  onAddData?: () => void
  /** Open a dataset — the way out when a citation has no recorded source trail. */
  onOpenDataset?: (id: string) => void
}) {
  const { t } = useTranslation()
  const threads = useAskThreads()
  const threadsLoaded = useAskThreadsLoaded()
  const thread = threadId ? threads.find((th) => th.id === threadId) : undefined
  const { isReady, getActiveCredentials, openSettings } = useLlmSettings()
  const keyMissing = !isReady && !isMockMode
  const conversationEmpty = !thread || thread.turns.length === 0

  // The catalog backs three things here: whether anything is published (the empty
  // state), which dataset a citation's ID belongs to, and which class names exist
  // (the ことば link). Best-effort — every consumer treats "not loaded" as "say
  // nothing extra" rather than showing an error.
  const [datasets, setDatasets] = useState<CatalogDataset[] | null>(null)
  useEffect(() => {
    let cancelled = false
    getCatalogDatasets()
      .then((ds) => {
        if (!cancelled) setDatasets(ds)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [conversationEmpty])

  // Ask only ever cites PUBLISHED data (ADR K3). With nothing published the AI
  // would answer "no match" and the reader would not learn why — so the empty
  // state says what to do instead. 'unknown' = the catalog could not be read:
  // fail open to the normal introduction rather than a wrong "you have nothing".
  const published: 'unknown' | 'none' | 'some' =
    isMockMode || datasets === null ? 'unknown' : datasets.some(isAskable) ? 'some' : 'none'

  // Everything a citation card / the trace panel needs from the catalog. Both
  // derivations are deterministic reads of the IRI the citation already carries.
  const catalog = useMemo<AskCatalog>(() => {
    const list = datasets ?? []
    return {
      vocabClasses: catalogClassNames(list),
      datasetFor: (iri: string) => findDatasetByIri(iri, list),
      datasetById: (id: string) => list.find((d) => registryIdOf(d) === id),
    }
  }, [datasets])

  // An example chip only FILLS the box — a question is sent by a human
  // (askPrefill.ts states the same rule for the かんたん S9 hand-off). The
  // counter makes picking the same chip twice re-fill it.
  const [prefill, setPrefill] = useState<{ text: string; n: number }>({ text: '', n: 0 })
  function pickExample(q: string) {
    setPrefill((p) => ({ text: q, n: p.n + 1 }))
  }

  // Provenance panel selection, tagged with the thread it belongs to so a
  // citation picked in one thread does not stay open after switching (back /
  // forward change the route without going through our click handlers).
  const [picked, setPicked] = useState<{ threadId: string | null; citation: Citation } | null>(null)
  const selected = picked && picked.threadId === (threadId ?? null) ? picked.citation : null
  // The same slot, for an answer whose source is a whole dataset rather than one
  // record (an aggregate). Only ever one panel at a time — picking either clears
  // the other, so the reader never has two "where this came from" open at once.
  const [pickedSource, setPickedSource] = useState<{
    threadId: string | null
    datasetId: string
    titles: string[]
  } | null>(null)
  const selectedSource =
    pickedSource && pickedSource.threadId === (threadId ?? null) ? pickedSource : null

  // Narrow screens: the thread list becomes an overlay toggled from the topline.
  const [threadsOpen, setThreadsOpen] = useState(false)

  // Esc closes whichever panel is open (the provenance pane / the thread overlay).
  const panelOpen = !!selected || !!selectedSource || threadsOpen
  useEffect(() => {
    if (!panelOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setPicked(null)
      setPickedSource(null)
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
    <div
      className={`chat${selected || selectedSource ? ' chat--trace' : ''}${
        threadsOpen ? ' chat--threads-open' : ''
      }`}
    >
      <ThreadList
        threads={threads}
        loaded={threadsLoaded}
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
          onSelectCitation={(c) => {
            setPickedSource(null)
            setPicked({ threadId: threadId ?? null, citation: c })
          }}
          selectedSourceId={selectedSource?.datasetId ?? null}
          onSelectSource={(datasetId, titles) => {
            setPicked(null)
            setPickedSource({ threadId: threadId ?? null, datasetId, titles })
          }}
          onShowVocab={onShowVocab}
          onRetry={retry}
          onExample={pickExample}
          keyMissing={keyMissing}
          published={published}
          onOpenSettings={() => openSettings('ai')}
          onAddData={onAddData}
          catalog={catalog}
        />

        <Composer
          key={`composer:${thread?.id ?? 'new'}`}
          disabled={keyMissing}
          busy={busy}
          onSend={send}
          onStop={stop}
          prefill={prefill}
          // 空状態のカードが同じことを言うので、そのときは帯を出さない（同じ文を 2 回出さない）
          hideGate={conversationEmpty}
        />
      </section>

      {!selected && selectedSource && (
        <SourcePanel
          datasetId={selectedSource.datasetId}
          dataset={catalog.datasetById(selectedSource.datasetId)}
          toolTitles={selectedSource.titles}
          onClose={() => setPickedSource(null)}
          onOpenDataset={
            onOpenDataset && catalog.datasetById(selectedSource.datasetId)
              ? () => {
                  const ds = catalog.datasetById(selectedSource.datasetId)
                  if (ds) onOpenDataset(ds.id)
                }
              : undefined
          }
        />
      )}

      {selected && (
        <ProvenanceTrace
          citation={selected}
          onShowVocab={onShowVocab}
          onClose={() => setPicked(null)}
          datasetName={catalog.datasetFor(selected.iri)?.name}
          onOpenDataset={
            onOpenDataset
              ? () => {
                  const ds = catalog.datasetFor(selected.iri)
                  if (ds) onOpenDataset(ds.id)
                }
              : undefined
          }
          vocabClasses={catalog.vocabClasses}
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
  loaded,
  activeId,
  onSelect,
  onDelete,
  onRename,
  onClose,
}: {
  threads: AskThread[]
  /** False while the single-user check / initial server read are still in
   *  flight — hold off the empty-state copy so it does not flash before the
   *  real history arrives. */
  loaded: boolean
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
        {loaded && threads.length === 0 && (
          <p className="chat-threads-empty">
            {t(getAppDataInfo()?.singleUser ? 'ask:threads.emptyDisk' : 'ask:threads.empty')}
          </p>
        )}
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

/** The example chips: they FILL the box, never send.
 *
 *  In mock mode the fixtures' own questions are the only ones that resolve. In
 *  live mode the wording is reused verbatim from かんたん S7 (the same three
 *  questions the deterministic try-it-out step already answered), so a first-time
 *  reader is not asked to invent a question in front of an empty box. Only the
 *  label-free S7 questions are used — the others need a column label we do not
 *  have here, and inventing one would name a column that may not exist. */
function ExampleChips({ onPick }: { onPick: (q: string) => void }) {
  const { t } = useTranslation()
  const items = isMockMode
    ? (t('ask:examples.items', { returnObjects: true }) as string[])
    : [t('kantan:s7.qCountMany'), t('kantan:s7.qCountAny'), t('kantan:s7.qSamplesAny')]
  return (
    <div className="chat-examples" aria-label={t('ask:examples.aria')}>
      {items.map((q) => (
        <button key={q} type="button" className="chat-example" onClick={() => onPick(q)}>
          {q}
        </button>
      ))}
    </div>
  )
}

function Conversation({
  thread,
  selectedIri,
  onSelectCitation,
  selectedSourceId,
  onSelectSource,
  onShowVocab,
  onRetry,
  onExample,
  keyMissing,
  published,
  onOpenSettings,
  onAddData,
  catalog,
}: {
  thread: AskThread | undefined
  selectedIri: string | null
  onSelectCitation: (c: Citation) => void
  selectedSourceId: string | null
  onSelectSource: (datasetId: string, titles: string[]) => void
  onShowVocab?: (className: string) => void
  onRetry: (assistantTurnId: string) => void
  onExample: (question: string) => void
  /** Live mode with no AI configured — the composer is disabled. */
  keyMissing: boolean
  /** Whether anything is published (Ask cites published data only). */
  published: 'unknown' | 'none' | 'some'
  onOpenSettings: () => void
  onAddData?: () => void
  catalog: AskCatalog
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
    // Exactly one of these three: the blocked states name the user's next step
    // (K5 / K3), and only when neither blocks does the introduction + chips show.
    const gate = keyMissing ? 'ai' : published === 'none' ? 'nothing' : 'none'
    return (
      <div className="chat-scroll">
        <div className="chat-empty">
          <span className="chat-empty-mark">
            <BrandMark size={44} />
          </span>
          {gate === 'ai' ? (
            <>
              <h3 className="chat-empty-title">{t('ask:empty.aiNotReady.title')}</h3>
              <p className="chat-empty-intro">{t('ask:empty.aiNotReady.body')}</p>
              <button type="button" className="btn" onClick={onOpenSettings}>
                {t('ask:empty.aiNotReady.cta')}
              </button>
            </>
          ) : gate === 'nothing' ? (
            <>
              <h3 className="chat-empty-title">{t('ask:empty.noPublished.title')}</h3>
              <p className="chat-empty-intro">{t('ask:empty.noPublished.body')}</p>
              {onAddData && (
                <button type="button" className="btn" onClick={onAddData}>
                  {t('ask:empty.noPublished.cta')}
                </button>
              )}
            </>
          ) : (
            <>
              <h3 className="chat-empty-title">{t('ask:empty.title')}</h3>
              <p className="chat-empty-intro">{t('ask:intro')}</p>
              {(isMockMode || published === 'some') && (
                <>
                  <ExampleChips onPick={onExample} />
                  <p className="chat-empty-intro">{t('ask:examples.hint')}</p>
                </>
              )}
            </>
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
              selectedSourceId={selectedSourceId}
              onSelectSource={onSelectSource}
              onShowVocab={onShowVocab}
              onRetry={() => onRetry(turn.id)}
              retryable={!busy}
              onExample={onExample}
              onOpenSettings={onOpenSettings}
              catalog={catalog}
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
  selectedSourceId,
  onSelectSource,
  onShowVocab,
  onRetry,
  retryable,
  onExample,
  onOpenSettings,
  catalog,
}: {
  turn: AskAssistantTurn
  selectedIri: string | null
  onSelectCitation: (c: Citation) => void
  selectedSourceId: string | null
  onSelectSource: (datasetId: string, titles: string[]) => void
  onShowVocab?: (className: string) => void
  onRetry: () => void
  retryable: boolean
  onExample: (question: string) => void
  onOpenSettings: () => void
  catalog: AskCatalog
}) {
  const { t } = useTranslation()
  // `answered: false` = the agent produced no answer text at all (attempts
  // exhausted). Showing that as a normal answer card would dress "nothing" up as
  // a finding — render the "didn't work" state, with a way out.
  const unanswered = !!turn.result && turn.result.answered === false
  const failed = !turn.pending && !turn.result
  const kind = failed && !turn.stopped && !turn.interrupted ? classifyAskError(turn.error ?? '') : null
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
        {(failed || unanswered) && (
          <div className={`chat-msg-error${turn.stopped ? ' chat-msg-error--stopped' : ''}`} role="alert">
            <span className="chat-msg-error-text">
              {turn.stopped
                ? t('ask:stopped')
                : turn.interrupted
                  ? t('ask:interrupted')
                  : unanswered
                    ? t('ask:unanswered')
                    : t(`ask:error.${kind ?? 'other'}`)}
            </span>
            {/* 出口は常に 2 つ以上。原因が設定のときだけ、設定への導線を主にする。 */}
            {kind === 'settings' && (
              <button type="button" className="btn btn--sm" onClick={onOpenSettings}>
                {t('ask:error.openSettings')}
              </button>
            )}
            {/* Disabled while another answer is on its way — say so, so the dead
                button is not read as "this is broken too". */}
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onRetry}
              disabled={!retryable}
              title={retryable ? undefined : t('ask:retryBusy')}
            >
              <RetryIcon size={14} /> {t('ask:retry')}
            </button>
            {/* 聞き方を変えれば通ることが多い — 例を出して、押すと入力欄に入れる。
                flexBasis: この帯は横並びの flex — 例と技術情報は自分の行を取る。 */}
            {unanswered && (
              <div style={{ flexBasis: '100%' }}>
                <ExampleChips onPick={onExample} />
              </div>
            )}
            {/* 生の英語 API エラーは通常表示から外し、確認したい人だけが開ける場所へ。 */}
            {turn.error && !turn.stopped && !turn.interrupted && (
              <details className="sparql-disclosure" style={{ flexBasis: '100%' }}>
                <summary>{t('ask:techSummary')}</summary>
                <pre className="sparql-block">{turn.error}</pre>
              </details>
            )}
          </div>
        )}
        {turn.result && !unanswered && (
          <AnswerCard
            result={turn.result}
            selectedIri={selectedIri}
            onSelectCitation={onSelectCitation}
            selectedSourceId={selectedSourceId}
            onSelectSource={onSelectSource}
            onShowVocab={onShowVocab}
            catalog={catalog}
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
  selectedSourceId,
  onSelectSource,
  onShowVocab,
  catalog,
}: {
  result: AskResponse
  selectedIri: string | null
  onSelectCitation: (c: Citation) => void
  selectedSourceId: string | null
  onSelectSource: (datasetId: string, titles: string[]) => void
  onShowVocab?: (className: string) => void
  catalog: AskCatalog
}) {
  const { t } = useTranslation()
  const verified = result.verifiedTools?.length ?? 0
  // An answer has no source when it touched no data at all — not merely when it
  // cites no row. An AGGREGATE ("the 2θ range is 20.0°–80.0°") names no single
  // record by nature, so it arrives with zero citations while being as traced as
  // an answer gets: a read-only query, shown in full, run against the published
  // graph. Judging by citations alone put 「出どころのない答え（AI の説明のみ）」
  // and 「数字として使わないでください」 on a number a verified tool had just read
  // out of the reader's own data (live 2026-08-20). A weak model that never
  // called a tool still lands here — it executed nothing.
  const executedQueries = result.sparql?.length ?? 0
  const noSources = result.citations.length === 0 && verified === 0 && executedQueries === 0
  // The published datasets this answer was read from, grouped, with the vetted
  // ways it was read. Only from verified tools: those name their dataset, so
  // this states what the answer knows rather than parsing it back out of a query.
  const toolSources = Object.values(
    (result.verifiedTools ?? []).reduce<
      Record<string, { id: string; dataset: CatalogDataset | undefined; titles: string[] }>
    >((acc, vt) => {
      const entry = (acc[vt.dataset] ??= {
        id: vt.dataset,
        dataset: catalog.datasetById(vt.dataset),
        titles: [],
      })
      if (!entry.titles.includes(vt.title)) entry.titles.push(vt.title)
      return acc
    }, {}),
  )
  return (
    <section className="answer-card">
      <div className="answer-head">
        {noSources ? (
          <span className="answer-badge">{t('ask:badge.noSources')}</span>
        ) : verified > 0 ? (
          <span
            className="answer-badge answer-badge-verified"
            title={t('ask:badge.verifiedToolsTitle', {
              tools: result.verifiedTools!.map((vt) => vt.title).join(' · '),
            })}
          >
            <CheckIcon size={13} /> {t('ask:badge.verifiedTools')}
          </span>
        ) : result.unverifiedSparql ? (
          <span className="answer-badge answer-badge-unverified">{t('ask:badge.unverifiedSparql')}</span>
        ) : (
          <span className="answer-badge">
            <CheckIcon size={13} /> {t('ask:badge.grounded')}
          </span>
        )}
        {!noSources && result.unverifiedSparql && verified > 0 && (
          <span className="answer-badge answer-badge-unverified">{t('ask:badge.plusUnverifiedSparql')}</span>
        )}
        <span className="answer-head-note">
          {noSources
            ? t('ask:headNote.noSources')
            : verified > 0
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
          {result.warnings!.map((w, i) =>
            w.kind === 'untyped-numeric-compare' ? (
              // The plain sentence names the problem and where to fix it. The
              // SPARQL variable and the xsd:double cast are for whoever writes
              // queries — they are the escape hatch, not the instruction.
              <div key={i} className="answer-warning">
                ⚠ {t('ask:warning.untypedNumeric')}
                <details className="sparql-disclosure">
                  <summary>{t('ask:techSummary')}</summary>
                  <p className="sparql-disclosure-hint">
                    {t('ask:warning.untypedNumericTech', { variable: w.variable ?? '?' })}
                  </p>
                </details>
              </div>
            ) : (
              <p key={i} className="answer-warning">
                ⚠ {w.message}
              </p>
            ),
          )}
        </div>
      )}

      {/* An aggregate answer ("the 2θ range is 20.0°–80.0°") names no single
          record, so the citation cards below have nothing to show — and the
          screen then claimed 「出どころつき」 while showing no source at all
          (live 2026-08-20). The source of such a number is the published
          dataset it was read from, and the vetted way it was read; both are in
          the answer already. Said in words, above the technical query. */}
      {result.citations.length === 0 && toolSources.length > 0 && (
        <div className="citations">
          <h3 className="section-h">
            {t('ask:sources.heading')}
            <span className="section-h-hint">{t('ask:sources.hint')}</span>
          </h3>
          <ul className="answer-sources">
            {toolSources.map((s) => (
              <li key={s.id}>
                {/* Opens the panel on the RIGHT, like a citation does — checking
                    where a number came from must not cost the reader their
                    conversation. Opening the dataset itself is a second,
                    deliberate click inside that panel. */}
                <button
                  type="button"
                  className={`answer-source${selectedSourceId === s.id ? ' selected' : ''}`}
                  aria-pressed={selectedSourceId === s.id}
                  onClick={() => onSelectSource(s.id, s.titles)}
                >
                  <span className="answer-source-name">
                    {s.dataset ? s.dataset.name : t('ask:sources.unknown')}
                  </span>
                  <span className="answer-source-via">
                    {t('ask:sources.via', { tools: s.titles.join(' · ') })}
                  </span>
                </button>
              </li>
            ))}
          </ul>
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
                datasetName={catalog.datasetFor(c.iri)?.name}
                vocabClasses={catalog.vocabClasses}
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
  prefill,
  hideGate,
}: {
  /** No model configured (live mode) — sending is blocked; the gate says why. */
  disabled: boolean
  /** An answer is pending in this thread — one question at a time; the send
   *  button becomes a stop button. */
  busy: boolean
  onSend: (text: string) => void
  onStop: () => void
  /** A question handed over from an example chip: fills the box, never sends.
   *  `n` increments per pick so the same chip re-fills. */
  prefill: { text: string; n: number }
  /** The empty state already carries the "AI is not set up" card — don't repeat it. */
  hideGate: boolean
}) {
  const { t } = useTranslation()
  const { isReady } = useLlmSettings()
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

  // An example chip fills the box and puts the cursor in it — the human sends.
  // The draft is adjusted during render (the supported "state derived from a
  // changed prop" pattern) rather than in an effect, so no extra paint happens
  // between the click and the filled box; only the focus needs the DOM.
  const [prefillSeen, setPrefillSeen] = useState(prefill.n)
  if (prefill.n !== prefillSeen) {
    setPrefillSeen(prefill.n)
    setDraft(prefill.text)
  }
  useEffect(() => {
    if (prefill.n === 0) return
    taRef.current?.focus()
  }, [prefill.n])

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
          // A greyed-out box with no reason in it is a dead end — say what makes it live.
          placeholder={disabled ? t('ask:inputPlaceholderLocked') : t('ask:inputPlaceholder')}
          aria-label={disabled ? t('ask:inputPlaceholderLocked') : t('ask:inputPlaceholder')}
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
        {/* Asking a question is the point of this screen; which model answers is
            not a decision to keep on the table. The band only appears while AI
            is not usable yet, i.e. when it names the user's next step. */}
        {!isMockMode && !isReady && !hideGate && (
          <div className="chat-composer-gate">
            <LlmGate plain />
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
