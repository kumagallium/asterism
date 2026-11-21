// 足した関係の「言えなかった組を判定し直す」（R4・契約 §3）。ボタン（N 組）→ 確かめの 1 行 → 走っている間 → 結果の 1 行。
// 数は GET /api/relations/{name}/reask。0 組ならボタンも「ありません」の文も出さない。やめた関係には出さない（呼ぶ側が出さない）。
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { resultText, useReaskCount } from './reaskCount'
import { busyPhase } from './reaskState'
import type { ReaskApi } from './useReask'

/** 足した直後の知らせ（R4）: 同じ種類の言えなかった組が N 組あれば、その数を文に入れてボタンを添える。0 組・読めないときは今までの文。 */
export function AddedDoneNotice({ name, label, trigger, reask, titleOf, busy }: { name: string; label: string; trigger: unknown; reask: ReaskApi; titleOf: (source: string) => string; busy?: boolean }) {
  const { t } = useTranslation('graphPlace')
  const { n } = useReaskCount(name, trigger, true)
  // 判定し直しを始めた後は「今ある組は聞き直しません」と言わない
  const started = reask.state.phase !== 'idle' && reask.state.name === name && reask.state.phase !== 'confirm'
  const text = started ? t('added.doneReasked', { name: label }) : n !== null && n > 0 ? t('added.doneReask', { name: label, n }) : t('added.done', { name: label })
  return (
    <>
      <p className="gp-note gp-store-note" role="status" data-testid="gp-added-notice">{text}</p>
      <ReaskControl name={name} trigger={trigger} reask={reask} titleOf={titleOf} busy={busy} />
    </>
  )
}

export interface ReaskControlProps {
  name: string
  trigger: unknown
  reask: ReaskApi
  /** 取り込み元の名前（正しい名前）。 */
  titleOf: (source: string) => string
  /** 読み直しの途中など。ボタンを無効にする。 */
  busy?: boolean
  /** 数が読めたときに親へ（知らせの文を替えるのに使う）。 */
  onCount?: (n: number | null) => void
}

export function ReaskControl({ name, trigger, reask, titleOf, busy = false, onCount }: ReaskControlProps) {
  const { n } = useReaskCount(name, trigger, true)
  useEffect(() => { onCount?.(n) }, [n, onCount])
  return <ReaskView name={name} n={n} reask={reask} titleOf={titleOf} busy={busy} />
}

/** 数（`n`）と状態から描くだけの部分（試験でも使う）。 */
export function ReaskView({ name, n, reask, titleOf, busy = false }: { name: string; n: number | null; reask: ReaskApi; titleOf: (source: string) => string; busy?: boolean }) {
  const { t } = useTranslation('graphPlace')
  const mine = reask.state.phase !== 'idle' && reask.state.name === name
  const others = busyPhase(reask.state) && !mine
  const s = reask.state
  if (mine && s.phase === 'confirm') {
    return (
      <div className="gp-confirm gp-reask" data-testid="gp-reask-confirm" role="group">
        <p className="gp-warn">{t('reask.confirm', { n: s.n })}</p>
        <div className="gp-acts">
          <button type="button" className="btn btn--sm btn--accent" data-testid="gp-reask-yes" onClick={reask.yes}>{t('act.yes')}</button>
          <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-reask-no" onClick={reask.no}>{t('act.keep')}</button>
        </div>
      </div>
    )
  }
  if (mine && (s.phase === 'starting' || s.phase === 'running')) {
    const text = s.phase === 'running' && s.index !== null && s.total !== null ? t('reask.running', { index: s.index, total: s.total }) : t('reask.starting')
    const pct = s.phase === 'running' && s.pairs && s.done !== null ? Math.min(100, Math.round((s.done / s.pairs) * 100)) : null
    return (
      <div className="gp-reask" data-testid="gp-reask-running" role="status">
        <p className="gp-body">{text}{s.phase === 'running' && pct !== null && <span className="gp-ev"> {s.done}/{s.pairs}</span>}</p>
        {pct !== null && <div className="gp-reask-bar" aria-hidden="true"><i style={{ width: `${pct}%` }} /></div>}
        <div className="gp-acts">
          <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-reask-cancel" disabled={s.phase === 'starting'} onClick={reask.cancel}>{t('reask.cancel')}</button>
        </div>
      </div>
    )
  }
  if (mine && s.phase === 'done') {
    return (
      <div className="gp-reask" data-testid="gp-reask-result" role="status">
        <p className="gp-body">{resultText(t, s.result, titleOf)}</p>
        <div className="gp-acts"><button type="button" className="btn btn--sm btn--ghost" data-testid="gp-reask-dismiss" onClick={reask.dismiss}>{t('act.close')}</button></div>
      </div>
    )
  }
  if (mine && s.phase === 'failed') {
    return (
      <div className="gp-reask" data-testid="gp-reask-failed" role="alert">
        <p className="gp-err">{t(`reask.fail.${s.kind}`)}</p>
        <div className="gp-acts"><button type="button" className="btn btn--sm btn--ghost" data-testid="gp-reask-dismiss" onClick={reask.dismiss}>{t('act.close')}</button></div>
      </div>
    )
  }
  if (n === null || n <= 0) return null
  return (
    <div className="gp-acts gp-reask" data-testid="gp-reask">
      <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-reask-button" disabled={busy || others} onClick={() => reask.ask(name, n)}>{t('reask.button', { n })}</button>
    </div>
  )
}
