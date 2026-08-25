// Design-consult chat threads (ADR design-consult-chat.md D2, revised
// 2026-08-25 user ruling): the SAME persistence mechanism Ask uses
// (`threadStore.ts`), under the `consult` namespace, so a conversation with
// the drawer survives a reload exactly like an Ask thread does (localStorage,
// or single-user server disk).
//
// D2 (revised): threads are a FLAT list the user manages themselves — no
// automatic per-design-session / per-topic binding. "Which conversation is
// this" is the user's call (Graphium's model), not something the UI infers
// from what dataset is on screen. The drawer opens to whichever thread was
// last touched (or a blank compose if there are none yet); the full list is
// always reachable to switch, and "+ 新しいチャット" always starts a genuinely
// new one. The auto-attached context (D4 — step/dataset/skeleton/focus
// column) still rides every send regardless of which thread it lands in;
// only the THREAD SELECTION stopped being automatic.
//
// The assistant's `result` is a plain reply string (no citations/sparql — the
// consult endpoint is tool-free), unlike Ask's AskResponse.

import { createThreadStore, type Attempt, type RegenAttempt, type Thread, type Turn } from '../threadStore'

const STORAGE_KEY = 'asterism.consult.threads.v1'
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
export const getAllConsultThreads = store.getAllThreads
export const isConsultThreadBusy = store.isThreadBusy
export const resolveConsultAnswer = store.resolveAnswer
export const failConsultAnswer = store.failAnswer
export const registerConsultInflight = store.registerInflight
export const unregisterConsultInflight = store.unregisterInflight
export const stopConsultAnswer = store.stopAnswer
export const renameConsultThread = store.renameThread
export const deleteConsultThread = store.deleteThread
/** Start a genuinely new thread (the "+ 新しいチャット" action / the first
 *  message when nothing is selected yet). */
export const startConsultThread = store.startThread
/** Append a message to an explicitly-known thread id (the one currently open
 *  in the drawer — there is no slot indirection anymore). */
export const appendConsultMessage = store.appendMessage
/** User-message edit-and-resend (Graphium ChatBubble onEditResend): rewrite
 *  the turn, drop everything after it, re-send. */
export const editConsultUserTurn = store.editUserTurn
/** Assistant-answer regenerate (Graphium ChatBubble onRegenerate): drop this
 *  answer and re-ask the same preceding question. */
export const regenerateConsultAnswer = store.regenerateFrom

export type ConsultAttempt = Attempt
export type ConsultRegenAttempt = RegenAttempt
export type ConsultThread = Thread<string>
export type ConsultTurn = Turn<string>

/** The most recently updated thread's id, or null when there are none yet —
 *  what the drawer opens to by default (Graphium: open = the last chat you
 *  were in, or a blank new one). */
export function latestConsultThreadId(): string | null {
  const all = getAllConsultThreads()
  if (all.length === 0) return null
  return all.reduce((latest, t) => (t.updatedAt > latest.updatedAt ? t : latest)).id
}
