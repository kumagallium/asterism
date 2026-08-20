import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { authHeaders, getApiToken, setApiToken } from '../authToken'
import { fetchInstanceInfo, invalidateInstanceInfo } from './instanceApi'

// 他のクライアントと同じ API ベース（既定は同一オリジン /api・別ホスト配備は VITE_API_URL）
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// トークンで保護された配備（サーバ側 ASTERISM_API_TOKEN 設定時）では、取り込み・
// 公開・ツール保存など書き込み系がすべてトークン必須になる。従来は build-time の
// VITE_API_TOKEN か sessionStorage 直叩きしか手段がなく、UI から設定できなかった
// （authToken.ts の "set from a settings field" が未実装だった）。ここがその設定欄。
// 値はこのタブの sessionStorage にのみ保存し、サーバへは送信ヘッダとしてだけ使う。
//
// ただし出すのは「貼れば実際に変わる」配備だけ（write_gate=token_required）。
// デスクトップはループバックで、本番はセッションゲート通過後に caddy が、いずれも
// サーバ側でトークンを *置換* 注入するので、そこで貼った値は捨てられる — 欄が出て
// いること自体が誤解になる。素の api に直結している構成でだけ意味がある。

type CheckState = '' | 'checking' | 'ok' | 'mismatch' | 'open' | 'error'

export function WriteTokenSection() {
  const { t } = useTranslation('settings')
  const buildTimeToken = Boolean(import.meta.env.VITE_API_TOKEN as string | undefined)
  const [draft, setDraft] = useState('')
  const [isSet, setIsSet] = useState(() => getApiToken().length > 0)
  const [check, setCheck] = useState<CheckState>('')
  // null = 判定中（この間は出さない）。判定はモーダルを開いた時の 1 回だけで、
  // 保存後に消えたりしないよう以後は保持する。
  const [needed, setNeeded] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchInstanceInfo().then((info) => {
      if (cancelled) return
      // 旧 api（write_gate 無し）では従来どおり出す — 判断材料が無いのに
      // 隠すと、保護された配備で書き込み手段を失う。
      setNeeded(info === null || info.write_gate === undefined || info.write_gate === 'token_required')
    })
    return () => {
      cancelled = true
    }
  }, [])

  function save(clear: boolean) {
    setApiToken(clear ? '' : draft.trim())
    setDraft('')
    setIsSet(getApiToken().length > 0)
    setCheck('')
    invalidateInstanceInfo() // 次に開いたときは新しいトークンで判定する
    // Saving a wrong code used to look exactly like saving the right one
    // ("set"), so people went back and hit the same refusal on the next
    // ingest. Check it here, while they can still fix it.
    if (!clear) void verify()
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

  if (needed !== true) return null

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
