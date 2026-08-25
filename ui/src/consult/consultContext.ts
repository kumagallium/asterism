// The design-consult drawer's "what am I looking at" store (ADR
// design-consult-chat.md D4). A tiny module-scoped store, not React context —
// the wizard's step machine and this drawer are otherwise unrelated trees, and
// wiring a context provider through the whole app for one attachment would be
// more machinery than the thing it carries. `KantanWizard` PUSHES patches here
// (step change, dataset name, skeleton summary, a focused column) and never
// reads it back; the drawer reads it once per send.
//
// Patches MERGE into the current state (not replace) so a step-level update
// (fired on every step/dataset/skeleton change) never clobbers a focus-column
// update that landed a moment earlier from a field the user is editing, and
// vice versa.

import { useSyncExternalStore } from 'react'

export interface ConsultFocusColumn {
  name: string
  /** Up to a few real values from the column, so the model can reason about
   *  the actual data rather than only the name. */
  samples: string[]
}

export interface ConsultContextState {
  /** Human-readable label of the step/screen the user is on. */
  step?: string
  /** The design's dataset name, when there is one yet. */
  dataset?: string
  /** A short description of the skeleton (kinds of row + how each gets an ID). */
  skeletonSummary?: string
  /** The column the user's cursor is on right now, if any. null clears it
   *  explicitly (e.g. the field lost focus) — undefined leaves it untouched. */
  focusColumn?: ConsultFocusColumn | null
}

let current: ConsultContextState = {}
const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

/** Merge a patch into the current context and notify subscribers. */
export function setConsultContext(patch: ConsultContextState): void {
  current = { ...current, ...patch }
  emit()
}

/** Drop everything (e.g. leaving the wizard entirely). */
export function clearConsultContext(): void {
  current = {}
  emit()
}

export function getConsultContext(): ConsultContextState {
  return current
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** React hook: re-renders whenever the context changes. */
export function useConsultContext(): ConsultContextState {
  return useSyncExternalStore(subscribe, getConsultContext, getConsultContext)
}
