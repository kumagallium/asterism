// Design-consult chat threads (ADR design-consult-chat.md D2): the SAME
// persistence mechanism Ask uses (`threadStore.ts`), under the `consult`
// namespace, so a conversation with the drawer survives a reload exactly like
// an Ask thread does (localStorage, or single-user server disk).
//
// D2's two thread kinds are two *slots*, not two special thread objects: a
// design-session slot (keyed by the dataset name if there is one yet, else
// `draft`) and a fixed `general` slot for "how do I use this screen"
// questions asked outside a design session. Each slot remembers which
// (uuid-keyed) thread it currently points at via a small session index kept
// in localStorage — reopening the same design session's slot resumes its
// conversation instead of starting a new one every time the drawer opens.
// The assistant's `result` is a plain reply string (no citations/sparql — the
// consult endpoint is tool-free), unlike Ask's AskResponse.

import { createThreadStore, type Attempt, type Thread, type Turn } from '../threadStore'

/** The fixed slot for screen/usage questions asked outside a design session. */
export const GENERAL_SLOT = 'general'

const STORAGE_KEY = 'asterism.consult.threads.v1'
const SESSION_INDEX_KEY = 'asterism.consult.sessionIndex.v1'
const MAX_THREADS = 60

function normalizeResult(raw: unknown): string | null {
  return typeof raw === 'string' ? raw : null
}

const store = createThreadStore<string>({
  namespace: 'consult',
  storageKey: STORAGE_KEY,
  maxThreads: MAX_THREADS,
  normalizeResult,
})

export const useConsultThreads = store.useThreads
export const useConsultThreadsLoaded = store.useThreadsLoaded
export const getConsultThread = store.getThread
export const isConsultThreadBusy = store.isThreadBusy
export const resolveConsultAnswer = store.resolveAnswer
export const failConsultAnswer = store.failAnswer
export const registerConsultInflight = store.registerInflight
export const unregisterConsultInflight = store.unregisterInflight
export const stopConsultAnswer = store.stopAnswer
export const retryConsultAnswer = store.retryAnswer
export const renameConsultThread = store.renameThread
/** Append a message to an ALREADY-KNOWN thread id (the caller picked this
 *  thread explicitly — e.g. from the full history list — rather than via a
 *  slot). Same continuation guarantee as `sendToSlot`'s append path. */
export const appendConsultMessage = store.appendMessage
export const deleteConsultThread = store.deleteThread

export type ConsultAttempt = Attempt
export type ConsultThread = Thread<string>
export type ConsultTurn = Turn<string>

// ---- slot -> thread-id index (client-only convenience, not synced to the
// server: a stale/missing mapping just starts a fresh thread for that slot,
// never loses data) --------------------------------------------------------

function loadIndex(): Record<string, string> {
  if (typeof localStorage === 'undefined') return {}
  try {
    const raw = localStorage.getItem(SESSION_INDEX_KEY)
    const parsed = raw ? (JSON.parse(raw) as unknown) : {}
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, string>) : {}
  } catch {
    return {}
  }
}

function saveIndex(idx: Record<string, string>): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(SESSION_INDEX_KEY, JSON.stringify(idx))
  } catch {
    /* ignore */
  }
}

/** The thread currently bound to `slot` (a design-session slug or
 *  GENERAL_SLOT), or undefined if this slot has never been used / its thread
 *  was deleted. */
export function threadForSlot(slot: string) {
  const id = loadIndex()[slot]
  return id ? getConsultThread(id) : undefined
}

export interface SentMessage {
  threadId: string
  assistantTurnId: string
}

/** Send the first message of a slot: reuses the slot's existing thread if one
 *  is bound and still exists, otherwise starts a new thread and binds it.
 *  Returns a flat `{threadId, assistantTurnId}` (rather than the store's own
 *  `Attempt` shapes, which differ between append/start) so the caller never
 *  has to branch on which path was taken. */
export function sendToSlot(slot: string, message: string): SentMessage | null {
  const bound = threadForSlot(slot)
  if (bound) {
    const appended = store.appendMessage(bound.id, message)
    return appended ? { threadId: bound.id, assistantTurnId: appended.assistantTurnId } : null
  }
  const started = store.startThread(message)
  const idx = loadIndex()
  idx[slot] = started.thread.id
  saveIndex(idx)
  return { threadId: started.thread.id, assistantTurnId: started.assistantTurnId }
}

/** Start a brand-new thread and (re)bind `slot` to it, REPLACING whatever the
 *  slot pointed at before. The old thread is untouched — it stays reachable
 *  from the full thread list (D2's "全スレッド" view) — this only changes
 *  which thread new messages sent to this slot land in going forward. This is
 *  the "新しいチャット" action. */
export function startNewInSlot(slot: string, message: string): SentMessage {
  const started = store.startThread(message)
  const idx = loadIndex()
  idx[slot] = started.thread.id
  saveIndex(idx)
  return { threadId: started.thread.id, assistantTurnId: started.assistantTurnId }
}

/** Move a slot's binding to a new slot name, but ONLY if the new slot has no
 *  conversation of its own yet — never silently overwrites one. This is what
 *  keeps a design-session conversation attached to the same thread when the
 *  session's slug changes mid-wizard (`draft` -> the dataset's real name once
 *  it gets one): without this, naming the dataset silently orphaned whatever
 *  had already been asked under `draft`, which read as "the conversation
 *  doesn't continue" even though the thread itself was never lost. */
export function rebindSlot(fromSlot: string, toSlot: string): void {
  if (fromSlot === toSlot) return
  const idx = loadIndex()
  const id = idx[fromSlot]
  if (!id || idx[toSlot]) return
  delete idx[fromSlot]
  idx[toSlot] = id
  saveIndex(idx)
}

/** Remove every slot binding that points at `threadId` (a thread the user just
 *  deleted from the full list — its slot(s), if any, must not keep pointing
 *  at a thread that no longer exists). */
export function unbindThreadEverywhere(threadId: string): void {
  const idx = loadIndex()
  let changed = false
  for (const [slot, id] of Object.entries(idx)) {
    if (id === threadId) {
      delete idx[slot]
      changed = true
    }
  }
  if (changed) saveIndex(idx)
}
