// アプリ更新のお知らせバナー（デスクトップ版のみ・画面最上部の全幅ストリップ）。
// updater.ts が更新を見つけると CustomEvent で知らせ、ここが
// 「Asterism X.Y.Z が利用できます ［あとで］［再起動して更新］」を出す。
//
// 更新は「今でなくてよい」情報なので、［あとで］でこのセッション中は引っ込む
// （ウィザードの途中でも画面上部を占有し続けない）。次の起動・次の確認でまた出る。
// 引っ込めたあとの受け皿は設定 →「このアプリ」で、そこにも同じ［再起動して更新］が
// ある（「あとで」の説明文が約束しているのはこの導線）。実行そのものは
// useUpdateInstall がモジュールに 1 つだけ持つ＝どちらから押しても二重に走らない。
// 更新の再確認は保守操作なので、設定 →「このアプリ」の「今すぐ確認」に一本化した
// （バナーに 2 つの似た選択肢を並べると、初見者はどちらを押すか迷う）。

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { pendingUpdate, UPDATE_AVAILABLE_EVENT, type UpdateAvailableDetail } from './updater'
import { useUpdateInstall } from './useUpdateInstall'

export function UpdateBanner() {
  // 更新固有の文言は settings、確認 2 段めの文言は common（settings.json は別 chain 所有）
  const { t } = useTranslation(['settings', 'common'])
  // マウント前に見つかっていた更新も拾う（イベントは一度きりなので）
  const [update, setUpdate] = useState<UpdateAvailableDetail | null>(() => pendingUpdate())
  const install = useUpdateInstall()
  // 「あとで」で引っ込めた版。次の版が見つかれば（イベントで update が変わる）また出す。
  const [dismissed, setDismissed] = useState<string | null>(null)
  // 再起動はデスクトップ版では同梱のローカルサーバごと落ちる＝ S3「AI が読んでいます」
  // や S5「取り込み中」を巻き込む。押した瞬間に実行せず、何が中断されるかを言ってから
  // もう一度押してもらう（K10 と同じ「確認は 1 本」の形）。
  const [confirming, setConfirming] = useState(false)

  const { clearError } = install
  useEffect(() => {
    const onAvailable = (e: Event) => {
      setUpdate((e as CustomEvent<UpdateAvailableDetail>).detail)
      clearError()
    }
    window.addEventListener(UPDATE_AVAILABLE_EVENT, onAvailable)
    return () => window.removeEventListener(UPDATE_AVAILABLE_EVENT, onAvailable)
  }, [clearError])

  async function onInstall() {
    if (!update) return
    setConfirming(false)
    await install.run(update)
  }

  if (!update || update.version === dismissed) return null

  const busy = install.installing

  return (
    <div className="update-banner" role="status" aria-live="polite">
      <span className="update-banner-text">
        {confirming
          ? t('common:updater.confirmText')
          : t('updater.available', { version: update.version })}
      </span>
      <span className="update-banner-actions">
        {confirming ? (
          <>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setConfirming(false)}
            >
              {t('common:updater.confirmCancel')}
            </button>
            <button type="button" className="btn btn--sm" onClick={onInstall} disabled={busy}>
              {t('common:updater.confirmGo')}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setDismissed(update.version)}
              disabled={busy}
              title={t('common:updater.laterTitle')}
            >
              {t('common:updater.later')}
            </button>
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => (busy ? undefined : setConfirming(true))}
              disabled={busy}
            >
              {install.label}
            </button>
          </>
        )}
      </span>
      {/* 生の Tauri/Rust エラー（英語）は本文に出さず title に退避する — 共有シェルの
          通常表示は平易文だけにし、次の一手をその文が持つ。 */}
      {install.error && (
        <span className="update-banner-error" title={install.error}>
          {t('common:updater.installFailed')}
        </span>
      )}
    </div>
  )
}
