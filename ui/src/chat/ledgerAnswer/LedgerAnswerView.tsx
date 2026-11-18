// 印つきの答え（14e-2）: 項目の一覧 ＋ 未確認の帯 ＋ 根拠を見る。
// 未確認の関係を使った答えは帯で知らせ、その場で確認できる（答えに使った分だけ）。
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { clearLedgerRowsCache, currentUse, readCurrentRows, type RowsBySource } from './current'
import { confirmUsed, LEDGER_REVIEWED_EVENT, type PostReviewFn } from './review'
import { itemPending, relKey, type LedgerAnswer } from './types'
import './ledgerAnswer.css'

export interface LedgerAnswerViewProps {
  ledger: LedgerAnswer
  /** テスト用: 確認の送り先を差し替える。 */
  post?: PostReviewFn
}

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
  useEffect(() => {
    if (sources.length === 0) return
    let off = false
    void readCurrentRows(sources, lang).then((m) => !off && setRows(m))
    return () => {
      off = true
    }
  }, [sources, lang])
  const cur = useMemo(() => currentUse(ledger.unconfirmed, rows), [ledger.unconfirmed, rows])
  // 札が消える鍵（この画面で確認した・もう確認済み・「違う」・台帳から消えた）
  const settled = useMemo(() => new Set([...done, ...cur.resolved, ...cur.rejected, ...cur.gone]), [done, cur])
  const remaining = cur.pending.filter((u) => !done.has(relKey(u.source, u.key)))
  const rejectedN = cur.rejected.size
  const resolvedN = cur.resolved.size
  const confirmedNow = done.size
  const more = Math.max(0, ledger.items_total - ledger.items.length)
  const hasEvidence = ledger.items.some((i) => i.evidence.length > 0)

  const onConfirm = async () => {
    if (running || remaining.length === 0) return
    setRunning(true)
    try {
      const out = await confirmUsed(remaining, post)
      clearLedgerRowsCache()
      if (out.done.size > 0) {
        setDone((cur) => new Set([...cur, ...out.done]))
        // 図（知識グラフ）を読み直す合図
        window.dispatchEvent(new CustomEvent(LEDGER_REVIEWED_EVENT))
      }
      setReasons(out.reasons)
      setNotDone(out.notDone)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="la" data-testid="ledger-answer">
      {ledger.items.length > 0 && (
        <ul className="la-items" data-testid="ledger-items">
          {ledger.items.map((it, i) => {
            const pending = itemPending(it, settled)
            return (
              <li key={`${it.sentence}-${i}`} className="la-item" data-testid="ledger-item">
                <span className="la-sent">{it.sentence}</span>
                {it.evidence_total > 0 && <span className="la-ev"> — {t('item.evidence', { n: it.evidence_total })}</span>}
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
          })}
        </ul>
      )}
      {more > 0 && (
        <p className="la-more" data-testid="ledger-more">
          {t('item.more', { n: more })}
        </p>
      )}

      {ledger.unconfirmed_count > 0 && (remaining.length > 0 || rejectedN > 0 || resolvedN > 0 || confirmedNow > 0) && (
        <div className={`la-bar${remaining.length === 0 && rejectedN === 0 ? ' la-bar--ok' : ''}`} role="status" data-testid="ledger-bar">
          {remaining.length > 0 ? (
            <>
              <p className="la-bar-t" data-testid="ledger-bar-unconfirmed">
                {t('bar.unconfirmed', { n: remaining.length })}
              </p>
              {confirmedNow > 0 && <p className="la-bar-s">{t('bar.done', { n: confirmedNow })}</p>}
            </>
          ) : rejectedN > 0 ? (
            <p className="la-bar-t" data-testid="ledger-bar-rejected">
              {t('bar.rejected', { n: rejectedN })}
            </p>
          ) : confirmedNow > 0 ? (
            <p className="la-bar-t" data-testid="ledger-bar-done">
              {t('bar.done', { n: confirmedNow })}
            </p>
          ) : (
            <p className="la-bar-t la-bar-quiet" data-testid="ledger-bar-allconfirmed">
              {t('bar.allConfirmed')}
            </p>
          )}
          {remaining.length > 0 && rejectedN > 0 && <p className="la-bar-s">{t('bar.rejected', { n: rejectedN })}</p>}
          {notDone > 0 && remaining.length > 0 && (
            <p className="la-bar-s la-bar-why" data-testid="ledger-bar-why">
              {t('bar.notDone', { n: notDone })}
              {reasons.length > 0 && `: ${reasons.join(' / ')}`}
            </p>
          )}
          {remaining.length > 0 && (
            <div className="la-acts">
              <button type="button" className="btn" data-testid="ledger-confirm" disabled={running} onClick={() => void onConfirm()}>
                {running ? t('bar.confirming') : t('bar.confirm', { n: remaining.length })}
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
