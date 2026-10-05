import { useTranslation } from 'react-i18next'
import type { PublishedNameChange } from '../api'

/**
 * K10: 公開を押す前に、その帰結を見せる。ここで公開されるのは項目の名前だけ —
 * 保存した名前のうち、公開済みの ID を開いたページにまだ出ていないものを 1 件ずつ
 * 並べ、その下にボタンを置く（データは取り込み直さない）。見た目は「ID の引っ越し」
 * の知らせ（IdMoveNotice）と同じ枠: どちらも「この公開で、画面の外の何が変わるか」。
 */
export function PublishedNamesNotice({
  changes,
  busy,
  done,
  error,
  onPublish,
}: {
  /** 公開側にまだ出ていない名前（namesToPublish の結果）。 */
  changes: PublishedNameChange[]
  busy: boolean
  /** 直前に公開した件数。まだ公開していなければ null。 */
  done: number | null
  /** 公開に失敗したときの技術的な詳細（たたんで見せる）。 */
  error: string
  onPublish: () => void
}) {
  const { t } = useTranslation()
  if (changes.length === 0) {
    if (done === null) return null
    return (
      <p className="kz-idmove" role="status">
        {t('kantan:s7.namesPublished', { n: done })}
      </p>
    )
  }
  return (
    <div className="kz-idmove">
      <p className="kz-idmove-head">{t('kantan:s7.namesTitle')}</p>
      <p>{t('kantan:s7.namesLead')}</p>
      <ul className="kz-idmove-list">
        {changes.map((change) => (
          <li key={change.iri}>
            {t('kantan:s7.namesChange', { published: change.published, design: change.design })}
          </li>
        ))}
      </ul>
      <div className="kz-actions">
        <button type="button" className="btn btn--ghost" onClick={onPublish} disabled={busy}>
          {t(busy ? 'kantan:s7.namesPublishing' : 'kantan:s7.namesPublish')}
        </button>
      </div>
      <p>{t('kantan:s7.namesNote')}</p>
      {error && (
        <div role="alert">
          <p>{t('kantan:s7.namesFailed')}</p>
          <details className="kz-stop-detail">
            <summary>{t('kantan:s5.stop.detailSummary')}</summary>
            <pre className="error">{error}</pre>
          </details>
        </div>
      )}
    </div>
  )
}
