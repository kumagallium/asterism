// 印つきの答え（14e-2）: 項目の一覧（問いごと）＋ 帯 ＋ 根拠を見る。
// 未確認の関係を使った答えは帯で知らせ、その場で確認できる（答えに使った分だけ）。
// 帯と札は「今の台帳」で数え直す（開き直したとき・ほかで確認したとき）。
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { bandOf, clearLedgerRowsCache, currentUse, readCurrentRows, type RowsBySource } from './current'
import { confirmUsed, LEDGER_REVIEWED_EVENT, reasonKeyOf, type PostReviewFn } from './review'
import { groupItems, itemPending, relKey, type LedgerAnswer, type LedgerItem } from './types'
import './ledgerAnswer.css'

export interface LedgerAnswerViewProps {
  ledger: LedgerAnswer
  /** テスト用: 確認の送り先を差し替える。 */
  post?: PostReviewFn
}

/** 項目に「違う」とされた関係が絡むか。 */
const itemRejected = (it: LedgerItem, rejected: ReadonlySet<string>): boolean => it.uses.some((u) => rejected.has(relKey(u.source, u.key)))

export function LedgerAnswerView({ ledger, post }: LedgerAnswerViewProps) {
  const { t, i18n } = useTranslation('ledgerAnswer')
  const [done, setDone] = useState<ReadonlySet<string>>(new Set())
  const [reasons, setReasons] = useState<string[]>([])
  const [notDone, setNotDone] = useState(0)
  const [running, setRunning] = useState(false)
  const [showEvidence, setShowEvidence] = useState(false)

  // 今の台帳（先に保存された形で出し、読めたら直す。読めなかった取り込み元は保存された数のまま）
  const [rows, setRows] = useState<RowsBySource | null>(null)
  const sources = useMemo(() => [...new Set(ledger.unconfirmed.map((u) => u.source))], [ledger.unconfirmed])
  const lang = i18n.language === 'en' ? 'en' : 'ja'
  const load = useCallback(
    (off: { v: boolean }) => {
      if (sources.length === 0) return
      void readCurrentRows(sources, lang).then((m) => !off.v && setRows(m))
    },
    [sources, lang],
  )
  useEffect(() => {
    const off = { v: false }
    load(off)
    // 確認が通った（この答え・知識グラフ・ほかの答え）: 数え直す
    const on = () => {
      clearLedgerRowsCache()
      load(off)
    }
    window.addEventListener(LEDGER_REVIEWED_EVENT, on)
    return () => {
      off.v = true
      window.removeEventListener(LEDGER_REVIEWED_EVENT, on)
    }
  }, [load])

  const cur = useMemo(() => currentUse(ledger.unconfirmed, rows), [ledger.unconfirmed, rows])
  const band = useMemo(() => bandOf(ledger, cur, done), [ledger, cur, done])
  // 札が消える鍵（この画面で確認した・もう確認済み・「違う」・台帳から消えた・外された）
  const settled = useMemo(() => new Set([...done, ...cur.resolved, ...cur.rejected, ...cur.gone, ...cur.removed]), [done, cur])
  const remaining = cur.pending.filter((u) => !done.has(relKey(u.source, u.key)))
  const groups = useMemo(() => groupItems(ledger), [ledger])
  const hasEvidence = ledger.items.some((i) => i.evidence.length > 0)

  const onConfirm = async () => {
    if (running || remaining.length === 0) return
    setRunning(true)
    try {
      const out = await confirmUsed(remaining, post)
      clearLedgerRowsCache()
      if (out.done.size > 0) {
        setDone((cur0) => new Set([...cur0, ...out.done]))
        // 図（知識グラフ）と、ほかの答えを読み直す合図
        window.dispatchEvent(new CustomEvent(LEDGER_REVIEWED_EVENT))
      }
      setReasons(out.reasons)
      setNotDone(out.notDone)
    } finally {
      setRunning(false)
    }
  }

  const reasonText = (r: string) => {
    const k = reasonKeyOf(r)
    return k ? t(`reason.${k}`) : r
  }
  const anyLine = band.remaining > 0 || band.rejected > 0 || band.gone > 0 || band.removed > 0 || band.conflict > 0 || band.confirmed > 0
  const calm = band.remaining === 0 && band.rejected === 0 && band.gone === 0 && band.removed === 0 && band.conflict === 0
  const showBar = (ledger.unconfirmed_count > 0 || ledger.conflict_count > 0) && anyLine

  const renderItem = (it: LedgerItem, i: number) => {
    const rej = itemRejected(it, cur.rejected)
    const pending = !rej && itemPending(it, settled)
    return (
      <li key={`${it.sentence}-${i}`} className="la-item" data-testid="ledger-item">
        <span className="la-sent">{it.sentence}</span>
        {it.evidence_total > 0 && <span className="la-ev"> — {t('item.evidence', { n: it.evidence_total })}</span>}
        {rej && (
          <span className="la-st la-st--rej" data-testid="ledger-rejected-badge">
            {t('item.rejected')}
          </span>
        )}
        {pending && (
          <span className="la-st" data-testid="ledger-unconfirmed-badge">
            {t('item.unconfirmed')}
          </span>
        )}
        {it.tentative && (
          <span className="la-st la-st--tent" data-testid="ledger-tentative-badge">
            {t('item.tentative')}
          </span>
        )}
        {it.context.length > 0 && <span className="la-ctx">{t('item.about', { names: it.context.join(t('item.sep')) })}</span>}
      </li>
    )
  }

  return (
    <div className="la" data-testid="ledger-answer">
      {groups.map((g, gi) => (
        <div key={gi} className="la-group" data-testid="ledger-group">
          {g.heading && <p className="la-h" data-testid="ledger-heading">{g.heading}</p>}
          {g.items.length > 0 && (
            <ul className="la-items" data-testid="ledger-items">
              {g.items.map(renderItem)}
            </ul>
          )}
          {g.more > 0 && (
            <p className="la-more" data-testid="ledger-more">
              {t('item.more', { n: g.more })}
            </p>
          )}
        </div>
      ))}

      {showBar && (
        <div className={`la-bar${calm ? ' la-bar--ok' : ''}`} role="status" data-testid="ledger-bar">
          {band.remaining > 0 && (
            <p className="la-bar-t" data-testid="ledger-bar-unconfirmed">
              {t('bar.unconfirmed', { n: band.remaining })}
            </p>
          )}
          {band.removed > 0 && (
            <p className={band.remaining > 0 ? 'la-bar-s' : 'la-bar-t'} data-testid="ledger-bar-removed">
              {t('bar.removed')}
            </p>
          )}
          {band.rejected > 0 && (
            <p className={band.remaining > 0 || band.removed > 0 ? 'la-bar-s' : 'la-bar-t'} data-testid="ledger-bar-rejected">
              {t('bar.rejected', { n: band.rejected })}
            </p>
          )}
          {band.gone > 0 && (
            <p className="la-bar-s" data-testid="ledger-bar-gone">
              {t('bar.gone', { n: band.gone })}
            </p>
          )}
          {band.confirmed > 0 && (band.remaining > 0 || !calm) && (
            <p className="la-bar-s">{t('bar.done', { n: band.confirmed })}</p>
          )}
          {calm && band.confirmed > 0 && (done.size > 0 ? (
            <p className="la-bar-t" data-testid="ledger-bar-done">{t('bar.done', { n: done.size })}</p>
          ) : (
            <p className="la-bar-t la-bar-quiet" data-testid="ledger-bar-allconfirmed">{t('bar.allConfirmed')}</p>
          ))}
          {notDone > 0 && band.remaining > 0 && (
            <p className="la-bar-s la-bar-why" data-testid="ledger-bar-why">
              {t('bar.notDone', { n: notDone })}
              {reasons.length > 0 && `: ${reasons.map(reasonText).join(' / ')}`}
            </p>
          )}
          {band.conflict > 0 && (
            <p className="la-bar-s" data-testid="ledger-bar-conflict">
              {t('bar.conflict', { n: band.conflict })}{' '}
              <a href="#/graph" data-testid="ledger-conflict-link">
                {t('bar.conflictLink')}
              </a>
            </p>
          )}
          {band.remaining > 0 && (
            <div className="la-acts">
              <button type="button" className="btn" data-testid="ledger-confirm" disabled={running} onClick={() => void onConfirm()}>
                {running ? t('bar.confirming') : t('bar.confirm', { n: band.remaining })}
              </button>
            </div>
          )}
        </div>
      )}

      {hasEvidence && (
        <div className="la-evwrap">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            data-testid="ledger-evidence-toggle"
            aria-expanded={showEvidence}
            onClick={() => setShowEvidence((v) => !v)}
          >
            {showEvidence ? t('evidence.hide') : t('evidence.show')}
          </button>
          {showEvidence && (
            <ul className="la-evl" data-testid="ledger-evidence">
              {ledger.items.map((it, i) =>
                it.evidence.length === 0 ? null : (
                  <li key={i}>
                    <span className="la-evl-h">{it.sentence}</span>
                    <ul>
                      {it.evidence.map((e, j) => (
                        <li key={j}>
                          {e.title || t('evidence.untitled')}
                          {e.source_url && (
                            <>
                              {' '}
                              <a href={e.source_url} target="_blank" rel="noreferrer" data-testid="ledger-evidence-link">
                                {t('evidence.open')}
                              </a>
                            </>
                          )}
                        </li>
                      ))}
                    </ul>
                  </li>
                ),
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
