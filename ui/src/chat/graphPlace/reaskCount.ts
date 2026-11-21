// 判定し直しの数と結果の 1 行（R4）。部品の外に置いた hook・純関数。
import { useEffect, useRef, useState } from 'react'
import { fetchReaskCount, type ReaskBlocked, type ReaskResult } from './reaskApi'

export interface ReaskCountLoad {
  /** 今すぐ走れる取り込み元の組の数。読み込み中・失敗は null（ボタンを出さない）。 */
  n: number | null
  /** いま判定し直せない取り込み元。 */
  blocked: ReaskBlocked[]
}

/** 足した関係の判定し直せる組の数。`trigger` が変わるたびに引き直す。 */
export function useReaskCount(name: string, trigger: unknown, enabled: boolean, fetcher: typeof fetchReaskCount = fetchReaskCount): ReaskCountLoad {
  const [state, setState] = useState<{ name: string; n: number | null; blocked: ReaskBlocked[] }>({ name, n: null, blocked: [] })
  const seq = useRef(0)
  useEffect(() => {
    if (!enabled) return
    const k = ++seq.current
    void fetcher(name).then(
      (r) => { if (k === seq.current) setState({ name, n: r.total, blocked: r.blocked }) },
      () => { if (k === seq.current) setState({ name, n: null, blocked: [] }) },
    )
  }, [name, trigger, enabled, fetcher])
  return enabled && state.name === name ? { n: state.n, blocked: state.blocked } : { n: null, blocked: [] }
}

type T = (key: string, opts?: Record<string, unknown>) => string

const WHY = ['relations_running', 'busy', 'no_copy', 'rebuilding', 'cancelled', 'aborted', 'error', 'retired']
const whyText = (t: T, why: string): string => t(`graphPlace:reask.why.${WHY.includes(why) ? why : 'error'}`)

/** 結果の項目（途中で折り返さない 1 項目ずつ）。「使えなかった」は 0 でないときだけ。 */
export function resultItems(t: T, r: ReaskResult): string[] {
  const items = [t('graphPlace:reask.item.relation', { n: r.relation }), t('graphPlace:reask.item.none', { n: r.none })]
  if (r.unusable > 0) items.push(t('graphPlace:reask.item.unusable', { n: r.unusable }))
  items.push(t('graphPlace:reask.item.failed', { n: r.failed }))
  return items
}

/** 結果の 1 行の後ろに添える文（飛ばした取り込み元・止めた理由）。無ければ空。 */
export function resultTail(t: T, r: ReaskResult, titleOf: (source: string) => string): string {
  const skipped = Object.entries(r.skipped).sort(([a], [b]) => a.localeCompare(b))
  let out = ''
  if (skipped.length > 0) {
    const list = skipped.map(([s, why]) => t('graphPlace:reask.skippedOne', { source: titleOf(s), reason: whyText(t, why) })).join(t('graphPlace:unstated.sep'))
    out += t('graphPlace:reask.skippedTail', { list })
  }
  if (r.stopped === 'retired') out += ` ${t('graphPlace:reask.stoppedRetired')}`
  return out
}

/** 「いま判定し直せない取り込み元」の 1 行。 */
export function blockedText(t: T, blocked: ReaskBlocked[], titleOf: (source: string) => string): string {
  const list = blocked.map((b) => t('graphPlace:reask.blockedOne', { source: titleOf(b.source), n: b.count, reason: whyText(t, b.reason) })).join(t('graphPlace:unstated.sep'))
  return t('graphPlace:reask.blocked', { list })
}
