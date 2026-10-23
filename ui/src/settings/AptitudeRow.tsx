// モデルの行の「適性を調べる」: ボタン・進み・バッジ・率・落ちた問（PR 11q）。
import { useEffect, useRef, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import type { JobHandle } from '../api'
import {
  failureParts,
  limitedHintKey,
  pct,
  startAptitude,
  verdictClass,
  watchAptitude,
  type AptitudeFailure,
  type AptitudeResult,
} from './aptitudeApi'
import type { LlmCredentials } from './store'

function failureText(f: AptitudeFailure, t: TFunction<'settings'>): string {
  const { kindKey, no, reason } = failureParts(f)
  const mode = (m: string) => t(`aptitude.mode.${m}`, { defaultValue: m })
  let why = reason
  if (reason === 'create' || reason === 'diff') why = t('aptitude.reason.shape')
  else if (reason === 'unreadable') why = t('aptitude.reason.unreadable')
  else if (reason === 'out_of_scope') why = t('aptitude.reason.outOfScope')
  else if (reason.includes('->')) {
    const [a, b] = reason.split('->')
    why = t('aptitude.reason.routing', { expected: mode(a), actual: mode(b) })
  }
  return `${t(`aptitude.kind.${kindKey}`)} #${no} — ${why}`
}

/** 保存済みの結果（または未検査）の表示。静的に描ける。 */
export function AptitudeView({ result }: { result: AptitudeResult | null }) {
  const { t } = useTranslation('settings')
  const [open, setOpen] = useState(false)
  if (!result) {
    return (
      <span className="aptitude-none" data-testid="aptitude-none">
        {t('aptitude.none')}
      </span>
    )
  }
  return (
    <div className="aptitude-view">
      <span className={verdictClass(result.verdict)} data-testid="aptitude-badge">
        {t(`aptitude.verdict.${result.verdict}`)}
      </span>
      <span className="aptitude-rates" data-testid="aptitude-rates">
        {t('aptitude.rates', {
          shape: pct(result.shape),
          routing: pct(result.routing),
          scope: pct(result.scope),
        })}
      </span>
      {result.verdict === 'limited' && <p className="field-help">{t(`aptitude.${limitedHintKey(result)}`)}</p>}
      {result.failed.length > 0 && (
        <>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOpen(!open)}>
            {t('aptitude.failed')}
          </button>
          {open && (
            <ul className="aptitude-failed">
              {result.failed.map((f, i) => (
                <li key={i}>
                  {failureText(f, t)}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

export function AptitudeRow({
  saved,
  creds,
  onSaved,
}: {
  saved: AptitudeResult | null
  creds: LlmCredentials | null
  onSaved: (r: AptitudeResult) => void
}) {
  const { t } = useTranslation('settings')
  const [progress, setProgress] = useState<{ n: number; total: number } | null>(null)
  const [error, setError] = useState('')
  const handleRef = useRef<JobHandle | null>(null)
  // 閉じる・タブを切り替える（unmount）ときは、走っているジョブを取り消す。
  useEffect(
    () => () => {
      const h = handleRef.current
      if (h) {
        h.close()
        void h.cancel().catch(() => undefined)
      }
    },
    [],
  )

  async function run() {
    setError('')
    setProgress({ n: 0, total: 40 })
    try {
      const id = await startAptitude(creds)
      const h = watchAptitude(id, {
        onProgress: (n, total) => setProgress({ n, total }),
        onDone: (r) => {
          handleRef.current = null
          setProgress(null)
          onSaved(r)
        },
        onError: (m) => {
          handleRef.current = null
          setProgress(null)
          setError(m)
        },
      })
      handleRef.current = h
    } catch (e) {
      setProgress(null)
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="aptitude-row">
      {progress ? (
        <span className="aptitude-running" data-testid="aptitude-running">
          {t('aptitude.running', { n: progress.n, total: progress.total })}
        </span>
      ) : (
        <>
          <AptitudeView result={saved} />
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            data-testid="aptitude-run"
            title={t('aptitude.cost')}
            disabled={!creds}
            onClick={run}
          >
            {t('aptitude.run')}
          </button>
        </>
      )}
      {error && <span className="field-help field-error">{error}</span>}
    </div>
  )
}
