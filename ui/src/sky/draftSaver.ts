// 会話の下書きの直し（答え方・テーマ名・ID・確定済みの行の ✕）をその場で保存する仕組み（UI 再設計 PR 8b の穴 4）。
//   mark() — 直した（debounce の間に連続して直せる）。delay 後に保存する
//   flush() — 待たずに保存する（離れる前・閉じる前・別の下書きを開く前）。保存中ならその終わりを待ってから
//   保存に失敗した直しは dirty のまま残り、次の直しか flush でもう一度送る。
// React を知らない純粋な部品（試験は draftSaver.test.ts）。

export interface DraftSaverOptions<D extends { id: string }> {
  /** いまの下書き（保存の時点で読む）。無い・id がまだ無いあいだは保存しない（最初の保存が全部運ぶ）。 */
  current: () => D | null
  save: (d: D) => Promise<unknown>
  delay: number
  onError?: (e: unknown) => void
  onOk?: () => void
}

export function createDraftSaver<D extends { id: string }>(o: DraftSaverOptions<D>) {
  let dirty = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let inflight: Promise<void> | null = null

  async function idle(): Promise<void> {
    while (inflight) await inflight
  }
  async function flush(): Promise<void> {
    clearTimeout(timer)
    await idle()
    if (!dirty) return
    const d = o.current()
    if (!d || !d.id) return
    dirty = false
    const job: Promise<void> = o
      .save(d)
      .then(() => o.onOk?.())
      .catch((e) => {
        dirty = true
        o.onError?.(e)
      })
      .finally(() => {
        inflight = null
      })
    inflight = job
    await job
  }
  return {
    mark(): void {
      dirty = true
      clearTimeout(timer)
      timer = setTimeout(() => void flush(), o.delay)
    },
    flush,
    /** 保存中の分が終わるまで待つ（別の保存を始める前に）。 */
    idle,
    /** 全部を別の口で送った（直しはもう残っていない）。待っている保存も取り消す。 */
    clear(): void {
      clearTimeout(timer)
      dirty = false
    },
    isDirty: (): boolean => dirty,
  }
}
