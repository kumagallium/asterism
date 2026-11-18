// 取り込みのジョブの購読と一覧（契約 pr14d2b §2.2・§2.4）。状態の動きは importJob.ts の reducer が持つ。
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { cancelJob, subscribeJob, type JobHandle } from '../../api'
import { idle, importJobReducer, type ImportJobState } from './importJob'
import { listImports, normalizeResult, removeImport, type ImportSource } from './knowledgeImportApi'

/** 走っている作業が一覧にあるとき、読み直す間隔（ms）。 */
export const POLL_MS = 4000

export interface KnowledgeImport {
  job: ImportJobState
  jobId: string | null
  sources: ImportSource[] | null
  /** 一覧を読めなかった理由（読めたら null）。 */
  listError: string | null
  /** 外す・開始の POST の最中。 */
  working: boolean
  /** 開始の失敗（409 も同じ）の理由。 */
  startError: string | null
  start: (source: string, begin: () => Promise<string>) => Promise<void>
  cancel: () => void
  dismiss: () => void
  remove: (source: string) => Promise<void>
  reload: () => Promise<void>
}

export function useKnowledgeImport(onChanged: () => void): KnowledgeImport {
  const [job, dispatch] = useReducer(importJobReducer, idle)
  const [jobId, setJobId] = useState<string | null>(null)
  const [sources, setSources] = useState<ImportSource[] | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const handle = useRef<JobHandle | null>(null)
  const changed = useRef(onChanged)
  useEffect(() => {
    changed.current = onChanged
  }, [onChanged])

  const reload = useCallback(async () => {
    try {
      setSources(await listImports())
      setListError(null)
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e))
    }
  }, [])
  useEffect(() => {
    let live = true
    listImports()
      .then((r) => live && (setSources(r), setListError(null)))
      .catch((e: unknown) => live && setListError(e instanceof Error ? e.message : String(e)))
    return () => {
      live = false
    }
  }, [])
  useEffect(() => () => handle.current?.close(), [])

  // 画面を開き直したとき、一覧に走っている作業があれば数秒おきに読み直す（ジョブ id は無いので進みは出さない）
  const anyRunning = (sources ?? []).some((s) => s.running !== null)
  useEffect(() => {
    if (!anyRunning || job.phase === 'running') return
    const id = setInterval(() => {
      void reload().then(() => changed.current())
    }, POLL_MS)
    return () => clearInterval(id)
  }, [anyRunning, job.phase, reload])

  const settle = useCallback(() => {
    handle.current = null
    setJobId(null)
    void reload()
    changed.current()
  }, [reload])

  const start = useCallback(
    async (source: string, begin: () => Promise<string>) => {
      setStartError(null)
      dispatch({ type: 'start', source })
      setWorking(true)
      let id: string
      try {
        id = await begin()
      } catch (e) {
        dispatch({ type: 'dismiss' })
        setStartError(e instanceof Error ? e.message : String(e))
        setWorking(false)
        void reload()
        return
      }
      setWorking(false)
      setJobId(id)
      handle.current = subscribeJob<unknown>(id, {
        onRunning: (d) => dispatch({ type: 'progress', stage: d.stage, done: d.done, total: d.total }),
        onDone: (r) => {
          const result = normalizeResult(r)
          dispatch(result ? { type: 'finished', result } : { type: 'failed', error: 'bad result' })
          settle()
        },
        onError: (m) => {
          dispatch({ type: 'failed', error: m })
          settle()
        },
        onCancelled: () => {
          dispatch({ type: 'cancelled' })
          settle()
        },
      })
    },
    [reload, settle],
  )

  const cancel = useCallback(() => {
    if (jobId) void cancelJob(jobId).catch(() => {})
  }, [jobId])

  const remove = useCallback(
    async (source: string) => {
      setStartError(null)
      setWorking(true)
      try {
        await removeImport(source)
      } catch (e) {
        setStartError(e instanceof Error ? e.message : String(e))
      }
      setWorking(false)
      await reload()
      changed.current()
    },
    [reload],
  )

  const dismiss = useCallback(() => dispatch({ type: 'dismiss' }), [])
  return { job, jobId, sources, listError, working, startError, start, cancel, dismiss, remove, reload }
}
