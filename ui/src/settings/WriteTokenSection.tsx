import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { authHeaders, getApiToken, setApiToken } from '../authToken'

// 他のクライアントと同じ API ベース（既定は同一オリジン /api・別ホスト配備は VITE_API_URL）
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// トークンで保護された配備（サーバ側 ASTERISM_API_TOKEN 設定時）では、取り込み・
// 公開・ツール保存など書き込み系がすべてトークン必須になる。従来は build-time の
// VITE_API_TOKEN か sessionStorage 直叩きしか手段がなく、UI から設定できなかった
// （authToken.ts の "set from a settings field" が未実装だった）。ここがその設定欄。
// 値はこのタブの sessionStorage にのみ保存し、サーバへは送信ヘッダとしてだけ使う。

type CheckState = '' | 'checking' | 'ok' | 'mismatch' | 'open' | 'error'

export function WriteTokenSection() {
  const { t } = useTranslation('settings')
  const buildTimeToken = Boolean(import.meta.env.VITE_API_TOKEN as string | undefined)
  const [draft, setDraft] = useState('')
  const [isSet, setIsSet] = useState(() => getApiToken().length > 0)
  const [check, setCheck] = useState<CheckState>('')

  function save(clear: boolean) {
    setApiToken(clear ? '' : draft.trim())
    setDraft('')
    setIsSet(getApiToken().length > 0)
    setCheck('')
  }

  // 保存済みトークンで書き込みゲートを 1 回だけ叩いて即フィードバックする。
  // 200/400/403 = 認証は通過（403 は raw SPARQL 非公開の配備でも認証自体は成立）。
  // 401 = トークン不一致。503 = サーバ側トークン未設定（ゲート閉鎖 or 保護なし）。
  async function verify() {
    setCheck('checking')
    try {
      const res = await fetch(`${API_BASE}/api/sparql`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ query: 'ASK { ?s ?p ?o }' }),
      })
      if (res.status === 401) setCheck('mismatch')
      else if (res.status === 503) setCheck('open')
      else setCheck('ok')
    } catch {
      setCheck('error')
    }
  }

  return (
    <section className="serverkeys">
      <h4 className="serverkeys-title">{t('writeToken.title')}</h4>
      <p className="field-help">{t('writeToken.intro')}</p>
      <div className="serverkey-row">
        <div className="serverkey-head">
          <span className="serverkey-name">{t('writeToken.name')}</span>
          <span className={`serverkey-status ${isSet ? 'ok' : 'off'}`}>
            {buildTimeToken
              ? t('writeToken.setAtBuild')
              : isSet
                ? t('serverKeys.set')
                : t('serverKeys.unset')}
          </span>
        </div>
        <div className="serverkey-controls">
          <input
            type="password"
            autoComplete="off"
            placeholder={t('writeToken.placeholder')}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={draft.trim().length === 0}
            onClick={() => save(false)}
          >
            {t('serverKeys.save')}
          </button>
          {isSet && !buildTimeToken && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => save(true)}>
              {t('serverKeys.clear')}
            </button>
          )}
          {isSet && (
            <button type="button" className="btn btn--ghost btn--sm" disabled={check === 'checking'} onClick={verify}>
              {t('writeToken.verify')}
            </button>
          )}
        </div>
        {check && check !== 'checking' && (
          <p className={`field-help ${check === 'ok' ? 'field-ok' : 'field-error'}`}>
            {t(`writeToken.check.${check}`)}
          </p>
        )}
      </div>
    </section>
  )
}
