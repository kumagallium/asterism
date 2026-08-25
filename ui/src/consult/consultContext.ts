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

/** One row of S6's "まだ取り込んでいない項目" (droppedColumns) table. */
export interface ConsultPendingColumn {
  name: string
  /** Up to a few real values, same as ConsultFocusColumn. */
  samples: string[]
}

/** One row of S6's "項目の意味" table — a column whose meaning/unit is
 *  already decided (blank when the AI/human hasn't filled it in yet).
 *  `samples` (2026-08-25 extension): up to a few real values, so a "意味が
 *  未入力の項目" question can be answered with the actual data, not just the
 *  bare column name (mirrors ConsultPendingColumn/ConsultFocusColumn). */
export interface ConsultColumn {
  name: string
  meaning?: string
  unit?: string
  samples?: string[]
}

/** One row of S4's「データの数えかた」ゲート — a map's key columns and its
 *  current「1 件が表すもの」(class/kind name), verbatim from the same data
 *  SkeletonGate renders. */
export interface ConsultKind {
  map: string
  source: string
  keyColumns: string[]
  /** Absent/undefined when the kind-name cell is empty. */
  kindName?: string
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
  /** S6's "まだ取り込んでいない項目" rows, verbatim from the same data the
   *  table renders. null clears it explicitly (left S6) — undefined leaves
   *  it untouched. */
  pendingColumns?: ConsultPendingColumn[] | null
  /** S6's "項目の意味" rows, verbatim from the same data the table renders.
   *  null clears it explicitly (left S6) — undefined leaves it untouched. */
  columns?: ConsultColumn[] | null
  /** S4 gate's per-map key columns + kind name. null clears it explicitly
   *  (left S4) — undefined leaves it untouched. */
  kinds?: ConsultKind[] | null
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
