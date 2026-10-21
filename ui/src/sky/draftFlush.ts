// 画面を離れる前に、会話の下書きの直しを保存する合図（UI 再設計 PR 8b の穴 4）。
//   useHomeWorkspace が `registerDraftFlush` で自分の flush を登録し、App の navigate() / hashchange が
//   hash を変える前に `flushDrafts()` を呼ぶ。保存は非同期だが、fetch は呼んだ時点で出るので、
//   そのあと画面が外れても届く（beforeunload には頼らない）。
const flushers = new Set<() => void>()

/** 登録する。外す関数を返す。 */
export function registerDraftFlush(fn: () => void): () => void {
  flushers.add(fn)
  return () => {
    flushers.delete(fn)
  }
}

/** 登録された保存をすべて今すぐ始める（待たない）。 */
export function flushDrafts(): void {
  for (const fn of flushers) fn()
}
