// 関係を書いたあとの画面への反映（R3c）。書き込みが成功した時点から、目録・足した関係・種類の読み直しが全部終わるまで
// 節の操作を無効にする窓を作る。読み直しが失敗・時間切れでも、必ず窓を閉じる。
type T = (key: string, opts?: Record<string, unknown>) => string

export interface ReflectDeps {
  /** 目録を読み直す（失敗・時間切れは false）。 */
  reload: () => Promise<boolean>
  /** 一覧（足した関係・種類）の引き直しを始める（これで各 hook の busy が引き継ぐ）。 */
  bump: () => void
  /** 反映の窓（節の操作を無効にする）の開閉。 */
  setBusy: (busy: boolean) => void
  notify: (text: string) => void
  t: T
}

/** `done`: 足した関係の表示名（足した直後の知らせ用）。`msg`: 先に出す知らせ（404 など）。反映の失敗は後ろに添える。 */
export async function reflectChange(d: ReflectDeps, opts: { done?: string; msg?: string } = {}): Promise<void> {
  d.setBusy(true)
  try {
    let ok = false
    try {
      ok = await d.reload()
    } catch {
      ok = false
    }
    if (!ok) {
      if (opts.msg) d.notify(`${opts.msg} ${d.t('added.reflectFailedTail')}`)
      else if (opts.done) d.notify(d.t('added.doneReflectFailed', { name: opts.done }))
      else d.notify(d.t('added.reflectFailed'))
    } else if (opts.msg) d.notify(opts.msg)
  } finally {
    d.bump()
    d.setBusy(false)
  }
}
