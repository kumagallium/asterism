// PDF を読み取る部品の状態を、画面どうしで共有する（モジュールレベルのストア）。
// 入れている最中・起動している最中だけ 1.5 秒おきに取り直す。タイマーは購読者数に
// かかわらず 1 本。ready になった／ready でなくなったときは、/api/instance の
// 取り直しを促す（置く欄が PDF を受け付けるようになる）。

import { useCallback, useSyncExternalStore } from 'react'
import { invalidateInstanceInfo } from './settings/instanceApi'
import {
  fetchPdfRuntime,
  installPdfRuntime,
  removePdfRuntime,
  type PdfRuntimeStatus,
} from './settings/pdfRuntimeApi'

const POLL_MS = 1500

/** 部品をこの画面から管理できる環境か（状態があり、unsupported・external 以外）。 */
export function isManageable(s: PdfRuntimeStatus | null): s is PdfRuntimeStatus {
  return s !== null && s.state !== 'unsupported' && s.state !== 'external'
}

/** 進み具合の % 。完了前に 100 を見せない（上限 99）。合計が不明なら 0。 */
export function progressPercent(s: Pick<PdfRuntimeStatus, 'bytes_done' | 'bytes_total'>): number {
  if (!(s.bytes_total > 0) || !(s.bytes_done > 0)) return 0
  return Math.min(99, Math.round((s.bytes_done / s.bytes_total) * 100))
}

/** 辞書の {{size}} に入れる GB 表記。合計が不明のときは約 1.7。 */
export function sizeGb(s: Pick<PdfRuntimeStatus, 'bytes_total'> | null): string {
  const total = s && s.bytes_total > 0 ? s.bytes_total : 1.7e9
  return (total / 1e9).toFixed(1)
}

let current: PdfRuntimeStatus | null = null
let started = false
let fetching = false
// 入れる・消すのたびに進める。その前に飛ばした取得の答え（古い状態）で、
// 操作の答え（新しい状態）を上書きしないための世代。
let generation = 0
let timer: ReturnType<typeof setInterval> | null = null
const subs = new Set<() => void>()

function set(next: PdfRuntimeStatus | null): void {
  const wasReady = current?.state === 'ready'
  current = next
  const isReady = next?.state === 'ready'
  if (wasReady !== isReady) invalidateInstanceInfo()
  for (const cb of Array.from(subs)) cb()
  syncTimer()
}

function wantsPolling(): boolean {
  return current?.state === 'installing' || current?.state === 'starting'
}

function syncTimer(): void {
  if (wantsPolling() && subs.size > 0) {
    if (timer === null) timer = setInterval(() => void refresh(), POLL_MS)
  } else if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

export async function refresh(): Promise<void> {
  if (fetching) return
  fetching = true
  const asked = generation
  try {
    const next = await fetchPdfRuntime()
    if (asked === generation) set(next)
  } catch {
    // いま聞けなかっただけ（ネットワーク・5xx）。直前の状態を保つ — 入れている最中に
    // 1 回取りこぼしただけで、タブが消えて取り直しも止まる、とならないように。
  } finally {
    fetching = false
  }
}

export async function installRuntime(): Promise<void> {
  const next = await installPdfRuntime()
  generation += 1
  set(next)
}

export async function removeRuntime(): Promise<void> {
  const next = await removePdfRuntime()
  generation += 1
  set(next)
}

function subscribe(cb: () => void): () => void {
  subs.add(cb)
  if (!started) {
    started = true
    void refresh()
  }
  syncTimer()
  return () => {
    subs.delete(cb)
    syncTimer()
    // 購読者がいなくなったら、次に使われたとき取り直せるようにする
    if (subs.size === 0) started = false
  }
}

function getSnapshot(): PdfRuntimeStatus | null {
  return current
}

export interface PdfRuntime {
  status: PdfRuntimeStatus | null
  manageable: boolean
  install: () => Promise<void>
  remove: () => Promise<void>
  refresh: () => Promise<void>
}

export function usePdfRuntime(): PdfRuntime {
  const status = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  const install = useCallback(() => installRuntime(), [])
  const remove = useCallback(() => removeRuntime(), [])
  const doRefresh = useCallback(() => refresh(), [])
  return { status, manageable: isManageable(status), install, remove, refresh: doRefresh }
}
