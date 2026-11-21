// 候補のタブの先頭の節「言えなかった関係の種類」（R2・設計 §23.13）。AI が「集合に無い関係」として挙げた種類を、組の数つきで並べる。
// 見るだけ（ボタンは置かない・関係として足すのは次の段）。開閉は親が持つ。
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { draftRelationLabel } from '../map/mapModel'
import type { UnstatedGroup } from './knowledgeRelationsApi'
import type { UnstatedLoad } from './useUnstated'

type Lang = 'ja' | 'en'

/** `onAdd`・`adder`（R3c）: 行を開いた中の最後の [関係として足す] と、その場の入力欄。`in_catalog` の行には出さない。 */
export interface UnstatedAdd {
  /** 入力欄を開いている群の鍵。 */
  adding: string | null
  onAdd: (key: string) => void
  /** 読み直しの途中は [関係として足す] を押せない（in_catalog が古い間に二重に足さない）。 */
  busy?: boolean
  adder: (group: UnstatedGroup) => ReactNode
}

export function UnstatedRow({ group, lang, open, onToggle, add }: { group: UnstatedGroup; lang: Lang; open: boolean; onToggle: () => void; add?: UnstatedAdd }) {
  const { t } = useTranslation('graphPlace')
  // 代表の名前そのもの・空の書き方は並べない（0 件なら行ごと出さない）
  const others = group.variants.filter((v) => v.label.trim() !== '' && v.label.trim() !== group.label.trim())
  const rest = group.count - group.examples.length
  return (
    <li className={`gp-item${open ? ' open' : ''}`} data-testid="gp-unstated-row" data-key={group.key}>
      <button type="button" className="gp-item-h" aria-expanded={open} data-testid="gp-unstated-toggle" onClick={onToggle}>
        <span className="gp-sent">{group.label}</span>
        <span className="gp-meta">
          <span className="gp-ev" data-testid="gp-unstated-count">{t('unstated.count', { n: group.count })}</span>
          {group.inCatalog && <em className="gp-tag" data-testid="gp-unstated-incatalog">{t('unstated.inCatalog', { name: draftRelationLabel(group.inCatalog, lang) })}</em>}
        </span>
        <span className="gp-chev" aria-hidden="true">›</span>
      </button>
      {open && (
        <div className="gp-item-b">
          <p className="gp-lbl">{t('unstated.examples')}</p>
          <ul className="gp-evl" data-testid="gp-unstated-examples">
            {group.examples.map((e) => (
              <li key={`${e.source}|${e.pair}`}>
                {e.a.name} ― {e.b.name}
                {e.note && <span className="gp-ev gp-origin">{e.note}</span>}
              </li>
            ))}
          </ul>
          {rest > 0 && <p className="gp-ev">{t('unstated.rest', { n: rest })}</p>}
          {others.length > 0 && <p className="gp-ev" data-testid="gp-unstated-variants">{t('unstated.variants', { names: others.map((v) => v.label).join(t('unstated.sep')) })}</p>}
          {add && !group.inCatalog && (add.adding === group.key ? add.adder(group) : (
            <div className="gp-acts">
              <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-unstated-add" disabled={add.busy} onClick={() => add.onAdd(group.key)}>{t('act.addFromKind')}</button>
            </div>
          ))}
        </div>
      )}
    </li>
  )
}

/** 群が 0 のときは節ごと出さない。読み込み中・失敗は 1 行（前の群があれば、それを残して出す）。 */
export function UnstatedSection({ load, lang, openKey, onToggle, shown, onMore, add }: { load: UnstatedLoad; lang: Lang; openKey: string | null; onToggle: (key: string) => void; shown: number; onMore: () => void; add?: UnstatedAdd }) {
  const { t } = useTranslation('graphPlace')
  const { status, groups } = load
  if (groups.length === 0) {
    if (status === 'loading') return <p className="gp-note" role="status" data-testid="gp-unstated-loading">{t('unstated.loading')}</p>
    if (status === 'error') return <p className="gp-note" role="alert" data-testid="gp-unstated-failed">{t('unstated.failed')}</p>
    return null
  }
  return (
    <section className="gp-un" data-testid="gp-unstated" aria-label={t('unstated.title')}>
      <h3 className="gp-un-h">{t('unstated.title')}</h3>
      <p className="gp-note">{t('unstated.note')}</p>
      {status === 'error' && <p className="gp-note" role="alert" data-testid="gp-unstated-failed">{t('unstated.failed')}</p>}
      <ul className="gp-list" data-testid="gp-unstated-list">
        {groups.slice(0, shown).map((g) => (
          <UnstatedRow key={g.key} group={g} lang={lang} open={openKey === g.key} onToggle={() => onToggle(g.key)} add={add} />
        ))}
      </ul>
      {groups.length > shown && (
        <button type="button" className="btn btn--sm btn--ghost gp-more" data-testid="gp-unstated-more" onClick={onMore}>
          {t('act.more', { n: groups.length - shown })}
        </button>
      )}
    </section>
  )
}
