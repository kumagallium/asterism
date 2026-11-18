// 取り込みのジョブの置き場（契約 pr14d2b §2.2）。画面の部品の外（モジュール）に持つので、場所を移って
// ImportPlace がアンマウントされても購読は続き、戻れば進み・取り消し・結果の 1 枚がそのまま出る。
// 純関数の storeReducer（状態の動き）＋薄い層（createImportStore: 非同期と購読）。
import { useSyncExternalStore } from 'react'
import { cancelJob, subscribeJob } from '../../api'
import type { ConnectFailure } from './graphiumConnect'
import { idle, importJobReducer, type ImportJobAction, type ImportJobState } from './importJob'
import { listImports, normalizeResult, removeImport, type ImportSource } from './knowledgeImportApi'

/** 一覧の上に出す失敗（外す・取り消し・一覧を読めない）。新しい失敗が古い失敗を置き換える。 */
export interface ImportNotice {
  kind: 'remove' | 'cancel' | 'list'
  detail: string
}

export interface ImportStoreState {
  job: ImportJobState
  jobId: string | null
  sources: ImportSource[] | null
  /** 開始（準備・POST）の失敗。Graphium の窓口に出す。 */
  startError: ConnectFailure | null
  notice: ImportNotice | null
  /** 外すの最中。 */
  working: boolean
}

export const initialStore: ImportStoreState = { job: idle, jobId: null, sources: null, startError: null, notice: null, working: false }

export type StoreAction =
  | { type: 'job'; action: ImportJobAction }
  | { type: 'jobId'; id: string | null }
  | { type: 'sources'; sources: ImportSource[] }
  | { type: 'startError'; error: ConnectFailure | null }
  | { type: 'notice'; notice: ImportNotice | null }
  | { type: 'working'; on: boolean }

export function storeReducer(s: ImportStoreState, a: StoreAction): ImportStoreState {
  switch (a.type) {
    case 'job': {
      const job = importJobReducer(s.job, a.action)
      return job === s.job ? s : { ...s, job }
    }
    case 'jobId':
      return s.jobId === a.id ? s : { ...s, jobId: a.id }
    case 'sources':
      // 一覧が読めたら「一覧を読めない」の失敗は消える（外す・取り消しの失敗は残す）
      return { ...s, sources: a.sources, notice: s.notice?.kind === 'list' ? null : s.notice }
    case 'startError':
      return { ...s, startError: a.error }
    case 'notice':
      return { ...s, notice: a.notice }
    case 'working':
      return s.working === a.on ? s : { ...s, working: a.on }
  }
}

/** 準備（設定・道具の確認）と POST をまとめた 1 手。成功ならジョブ id、失敗なら理由。 */
export type Begin = () => Promise<{ ok: true; jobId: string } | { ok: false; failure: ConnectFailure }>

export const isBusy = (s: ImportStoreState): boolean => s.job.phase === 'running' || s.job.phase === 'preparing' || s.working

export interface StoreDeps {
  listImports: typeof listImports
  removeImport: typeof removeImport
  cancelJob: typeof cancelJob
  subscribeJob: typeof subscribeJob
}
const realDeps: StoreDeps = { listImports, removeImport, cancelJob, subscribeJob }

const msg = (e: unknown): string => (e instanceof Error ? e.message : String(e))

export function createImportStore(deps: StoreDeps) {
  let state = initialStore
  const listeners = new Set<() => void>()
  let handle: { close: () => void } | null = null
  let changed: () => void = () => {}
  let listSeq = 0

  const dispatch = (a: StoreAction) => {
    const next = storeReducer(state, a)
    if (next === state) return
    state = next
    listeners.forEach((l) => l())
  }
  const job = (action: ImportJobAction) => dispatch({ type: 'job', action })

  /** 一覧を読む。新しい読み込みが始まっていたら古い応答は捨てる。中身が変わったとき true。 */
  async function reload(): Promise<boolean> {
    const seq = ++listSeq
    try {
      const rows = await deps.listImports()
      if (seq !== listSeq) return false
      const before = state.sources
      dispatch({ type: 'sources', sources: rows })
      return before !== null && JSON.stringify(before) !== JSON.stringify(rows)
    } catch (e) {
      if (seq === listSeq) dispatch({ type: 'notice', notice: { kind: 'list', detail: msg(e) } })
      return false
    }
  }

  /** 数秒おきの読み直し: 中身が変わったときだけ図の読み直しを呼ぶ。 */
  async function poll(): Promise<void> {
    if (await reload()) changed()
  }

  function settle() {
    handle = null
    dispatch({ type: 'jobId', id: null })
    void reload()
    changed()
  }

  async function start(source: string, begin: Begin): Promise<void> {
    if (isBusy(state)) return // 準備中・走行中の 2 本目は受けない
    dispatch({ type: 'startError', error: null })
    dispatch({ type: 'notice', notice: null })
    job({ type: 'prepare', source })
    let r: Awaited<ReturnType<Begin>>
    try {
      r = await begin()
    } catch (e) {
      r = { ok: false, failure: { code: 'http', detail: msg(e) } }
    }
    if (!r.ok) {
      job({ type: 'abort' })
      dispatch({ type: 'startError', error: r.failure })
      void reload()
      return
    }
    job({ type: 'start', source })
    dispatch({ type: 'jobId', id: r.jobId })
    handle?.close()
    handle = deps.subscribeJob<unknown>(r.jobId, {
      onRunning: (d) => job({ type: 'progress', stage: d.stage, done: d.done, total: d.total }),
      onDone: (res) => {
        const result = normalizeResult(res)
        job(result ? { type: 'finished', result } : { type: 'failed', error: 'bad result' })
        settle()
      },
      onError: (m) => {
        job({ type: 'failed', error: m })
        settle()
      },
      onCancelled: () => {
        job({ type: 'cancelled' })
        settle()
      },
    })
  }

  function cancel() {
    const id = state.jobId
    if (!id) return
    dispatch({ type: 'notice', notice: null })
    deps.cancelJob(id).catch((e: unknown) => dispatch({ type: 'notice', notice: { kind: 'cancel', detail: msg(e) } }))
  }

  async function remove(source: string): Promise<void> {
    if (isBusy(state)) return
    dispatch({ type: 'notice', notice: null })
    dispatch({ type: 'working', on: true })
    try {
      await deps.removeImport(source)
    } catch (e) {
      dispatch({ type: 'notice', notice: { kind: 'remove', detail: msg(e) } })
    }
    dispatch({ type: 'working', on: false })
    await reload()
    changed()
  }

  return {
    subscribe(l: () => void) {
      listeners.add(l)
      return () => void listeners.delete(l)
    },
    getSnapshot: () => state,
    /** 最後に渡された関数を持つ（アンマウント後も終わりに 1 回呼ぶ）。 */
    setChanged(fn: () => void) {
      changed = fn
    },
    start,
    cancel,
    remove,
    reload,
    poll,
    dismiss: () => job({ type: 'dismiss' }),
    dismissNotice: () => dispatch({ type: 'notice', notice: null }),
  }
}

export const importStore = createImportStore(realDeps)

export function useImportStore(): ImportStoreState {
  return useSyncExternalStore(importStore.subscribe, importStore.getSnapshot, importStore.getSnapshot)
}
