// 取り込みのジョブの状態（純関数の reducer・契約 pr14d2b §2.2）。hook（useKnowledgeImport）は薄く保つ。
import type { ImportResult } from './knowledgeImportApi'

export type ImportStage = 'fetch' | 'copy' | 'pairs' | 'done'
const ORDER: ImportStage[] = ['fetch', 'copy', 'pairs', 'done']

export type ImportJobState =
  | { phase: 'idle' }
  | { phase: 'running'; source: string; stage: ImportStage | null; done: number | null; total: number | null }
  | { phase: 'done'; result: ImportResult }
  | { phase: 'cancelled'; source: string }
  | { phase: 'failed'; source: string; error: string }

export type ImportJobAction =
  | { type: 'start'; source: string }
  | { type: 'progress'; stage?: unknown; done?: unknown; total?: unknown }
  | { type: 'finished'; result: ImportResult }
  | { type: 'cancelled' }
  | { type: 'failed'; error: string }
  | { type: 'dismiss' }

export const idle: ImportJobState = { phase: 'idle' }

const isStage = (s: unknown): s is ImportStage => typeof s === 'string' && (ORDER as string[]).includes(s)
const count = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null)

export function importJobReducer(state: ImportJobState, a: ImportJobAction): ImportJobState {
  switch (a.type) {
    case 'start':
      return { phase: 'running', source: a.source, stage: null, done: null, total: null }
    case 'progress': {
      if (state.phase !== 'running') return state
      // 段は戻らない（遅れて届いた古い枠で巻き戻さない）
      const next = isStage(a.stage) && (state.stage === null || ORDER.indexOf(a.stage) >= ORDER.indexOf(state.stage)) ? a.stage : state.stage
      const done = count(a.done)
      const total = count(a.total)
      return { ...state, stage: next, done: done ?? state.done, total: total ?? state.total }
    }
    case 'finished':
      return state.phase === 'running' ? { phase: 'done', result: a.result } : state
    case 'cancelled':
      return state.phase === 'running' ? { phase: 'cancelled', source: state.source } : state
    case 'failed':
      return { phase: 'failed', source: state.phase === 'running' ? state.source : '', error: a.error }
    case 'dismiss':
      return idle
  }
}

export type StepState = 'done' | 'cur' | 'todo'
export interface Step {
  id: 'copy' | 'pairs' | 'done'
  state: StepState
}

/** 3 つの段（写す・関係を選ぶ・まとめ）と今の段。fetch は「写す」に含める。 */
export function stepsOf(stage: ImportStage | null): Step[] {
  const at = stage === null ? 0 : stage === 'fetch' || stage === 'copy' ? 0 : stage === 'pairs' ? 1 : 2
  return (['copy', 'pairs', 'done'] as const).map((id, i) => ({ id, state: i < at ? 'done' : i === at ? 'cur' : 'todo' }))
}

/** 棒の割合（0〜100）。関係を選ぶ段で数が取れたときだけ。取れなければ null（棒は流れる）。 */
export function barPercent(s: Extract<ImportJobState, { phase: 'running' }>): number | null {
  if (s.stage !== 'pairs' || s.total === null || s.done === null || s.total === 0) return null
  return Math.min(100, Math.round((s.done / s.total) * 100))
}

/** 結果の 1 枚の「関係を選んでいない」理由の鍵（無ければ null）。 */
export function skipKey(r: ImportResult): 'ai_unset' | 'off' | 'error' | 'cancelled' | 'aborted' | null {
  const rel = r.relations
  if (rel.skipped === 'ai_unset' || rel.skipped === 'off' || rel.skipped === 'error') return rel.skipped
  if (rel.cancelled) return 'cancelled'
  if (rel.aborted) return 'aborted'
  return null
}

/** 落とした・失敗の合計（0 でなければ結果の 1 枚に出す）。 */
export function lostCount(r: ImportResult): number {
  return Object.values(r.copied.dropped).reduce((n, v) => n + (typeof v === 'number' ? v : 0), 0) + r.relations.failed
}

/** 問いの行: 先頭 5、残りは「ほか N」（N は全体の件数から出す）。 */
export const QUESTION_SHOW = 5
export function questionRows(r: ImportResult): { shown: ImportResult['questions']; more: number } {
  const shown = r.questions.slice(0, QUESTION_SHOW)
  return { shown, more: Math.max(0, Math.max(r.questions_total, r.questions.length) - shown.length) }
}
