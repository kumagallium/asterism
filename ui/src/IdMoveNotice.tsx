import { useTranslation } from 'react-i18next'
import type { IdMove } from './api'
import { idMoveNoticeView } from './idMove'

/**
 * K10: 公開を押す前に、その帰結を見せる。数えかたを直した更新は、もう誰かが
 * 引用しているかもしれない ID を動かす — 公開の画面の中で、画面の外に届く
 * ただ 1 つのこと（ADR id-move-after-publish.md）。
 *
 * 言うことは 3 つ: 引っ越し先を書けた件数／引き継げないぶんのファイルと理由／
 * 戻り道。戻り道だけは画面ごとに違う（その画面に実在するボタンの名前で言う）
 * ので、呼ぶ側が `exit` で渡す。
 */
export function IdMoveNotice({ move, exit }: { move: IdMove | null; exit: string }) {
  const { t } = useTranslation()
  const view = idMoveNoticeView(move)
  if (!view) return null
  return (
    <div className={`kz-idmove${view.broken ? ' kz-idmove--warn' : ''}`}>
      <p className="kz-idmove-head">{t('kantan:s8.idMoveTitle')}</p>
      {view.forwarded > 0 && (
        <p>{t('kantan:s8.idMoveForwarded', { n: view.forwarded.toLocaleString() })}</p>
      )}
      {view.broken && (
        <>
          <p>{t('kantan:s8.idMoveBroken')}</p>
          <ul className="kz-idmove-list">
            {view.blocked.map((b) => (
              <li key={b.key}>
                {b.reason === 'columns'
                  ? t('kantan:s8.idMoveBrokenColumns', {
                      source: b.source,
                      columns: b.columns.join('、') || '—',
                    })
                  : t('kantan:s8.idMoveBrokenKind', { source: b.source })}
              </li>
            ))}
          </ul>
          <p>{exit}</p>
        </>
      )}
    </div>
  )
}
