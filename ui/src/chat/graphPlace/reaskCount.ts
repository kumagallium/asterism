// 判定し直しの数と結果の 1 行（R4）。部品の外に置いた hook・純関数。
import { useEffect, useRef, useState } from 'react'
import { fetchReaskCount, type ReaskResult } from './reaskApi'

export interface ReaskCountLoad {
  /** 数が読めたときだけ数。読み込み中・失敗は null（ボタンを出さない）。 */
  n: number | null
}

/** 足した関係の判定し直せる組の数。`trigger` が変わるたびに引き直す。 */
export function useReaskCount(name: string, trigger: unknown, enabled: boolean, fetcher: typeof fetchReaskCount = fetchReaskCount): ReaskCountLoad {
  const [state, setState] = useState<{ name: string; n: number | null }>({ name, n: null })
  const seq = useRef(0)
  useEffect(() => {
    if (!enabled) return
    const k = ++seq.current
    void fetcher(name).then(
      (r) => { if (k === seq.current) setState({ name, n: r.total }) },
      () => { if (k === seq.current) setState({ name, n: null }) },
    )
  }, [name, trigger, enabled, fetcher])
  return { n: enabled && state.name === name ? state.n : null }
}

type T = (key: string, opts?: Record<string, unknown>) => string

const WHY = ['relations_running', 'busy', 'no_copy', 'rebuilding', 'cancelled', 'aborted', 'error']

/** 結果の 1 行。飛ばした取り込み元があれば、名前と理由を後ろに添える。 */
export function resultText(t: T, r: ReaskResult, titleOf: (source: string) => string): string {
  const head = t('graphPlace:reask.done', { relation: r.relation, none: r.none, failed: r.failed })
  const skipped = Object.entries(r.skipped).sort(([a], [b]) => a.localeCompare(b))
  if (skipped.length === 0) return head
  const list = skipped.map(([s, why]) => t('graphPlace:reask.skippedOne', { source: titleOf(s), reason: t(`graphPlace:reask.why.${WHY.includes(why) ? why : 'error'}`) })).join(t('graphPlace:unstated.sep'))
  return `${head}${t('graphPlace:reask.skippedTail', { list })}`
}
