// 980px 以下で画面の下に出す 3 つのタブ（PR 14d-1・契約 §4）。アイコンの下に名前。
import { useTranslation } from 'react-i18next'
import { PLACES, type Place } from './chatRoute'
import { PlaceIcon } from './NavRail'

export function PlaceTabbar({ place, onPlace }: { place: Place | null; onPlace: (p: Place) => void }) {
  const { t } = useTranslation('chat')
  return (
    <nav aria-label={t('nav.label')} className="place-tabbar">
      {PLACES.map((p) => (
        <button
          key={p}
          type="button"
          className={`place-tab${place === p ? ' is-active' : ''}`}
          data-testid={`place-tab-${p}`}
          aria-current={place === p ? 'page' : undefined}
          onClick={() => onPlace(p)}
        >
          <PlaceIcon place={p} size={20} />
          <span className="place-tab-name">{t(`nav.${p}`)}</span>
        </button>
      ))}
    </nav>
  )
}
