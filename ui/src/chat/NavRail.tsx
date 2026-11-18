// 左のナビ（PR 14d-1・契約 §4）。ブランド・3 つの場所（取り込み・知識グラフ・チャット）・チャットの一覧・⚙。
// 幅 220／たたんだ 56（アイコンだけ）／980px 以下のシート（ブランドと一覧だけ。3 つの場所は下のタブ）。
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { AskIcon, BrandMark, BrandWordmark, GearIcon, ImportIcon, MenuIcon, NetworkIcon } from '../icons'
import { PLACES, type Place } from './chatRoute'

/** 場所ごとのアイコン（NavRail と PlaceTabbar で共有）。 */
export function PlaceIcon({ place, size }: { place: Place; size?: number }) {
  if (place === 'import') return <ImportIcon size={size} />
  if (place === 'graph') return <NetworkIcon size={size} />
  return <AskIcon size={size} />
}

export interface NavRailProps {
  /** 今の場所（決まる前は null）。 */
  place: Place | null
  onPlace: (p: Place) => void
  /** 幅 56 のたたんだ形。 */
  folded: boolean
  /** ≡ を出すか（980px 以下のシートでは false）。 */
  showToggle: boolean
  /** 980px 以下のシート: 3 つの項目は出さない（下のタブにある）。 */
  sheet: boolean
  onToggle: () => void
  onOpenSettings: () => void
  /** チャットの一覧（SessionList）。「チャット」の項目の直下に置く。 */
  children: ReactNode
}

export function NavRail({ place, onPlace, folded, showToggle, sheet, onToggle, onOpenSettings, children }: NavRailProps) {
  const { t } = useTranslation('chat')
  const { t: ts } = useTranslation('settings')
  const { t: tb } = useTranslation()

  const toggle = showToggle && (
    <button type="button" className="chat-icon-btn" data-testid="list-toggle" onClick={onToggle} aria-label={t('list.toggle')} title={t('list.toggle')} aria-expanded={!folded}>
      <MenuIcon size={20} />
    </button>
  )
  const gear = (
    <button type="button" className="chat-icon-btn" onClick={onOpenSettings} aria-label={ts('open')} title={ts('open')}>
      <GearIcon />
    </button>
  )

  if (sheet) {
    return (
      <div className="nav-rail nav-rail--sheet">
        <div className="chat-list-head">
          <span className="chat-brand">
            <BrandMark size={26} />
            <BrandWordmark className="chat-brand-name" size={14.5} label={tb('brand.name')} />
          </span>
        </div>
        <div className="nav-rail-list">{children}</div>
      </div>
    )
  }

  if (folded) {
    return (
      <div className="nav-rail nav-rail--folded">
        {toggle}
        <nav aria-label={t('nav.label')} className="nav-items">
          {PLACES.map((p) => (
            <button
              key={p}
              type="button"
              className={`nav-item nav-item--icon${place === p ? ' is-active' : ''}`}
              data-testid={`nav-${p}`}
              aria-current={place === p ? 'page' : undefined}
              aria-label={t(`nav.${p}`)}
              title={t(`nav.${p}`)}
              onClick={() => onPlace(p)}
            >
              <PlaceIcon place={p} size={20} />
            </button>
          ))}
        </nav>
        <span className="chat-list-spacer" />
        {gear}
      </div>
    )
  }

  return (
    <div className="nav-rail">
      <div className="chat-list-head">
        {toggle}
        <span className="chat-brand">
          <BrandMark size={26} />
          <BrandWordmark className="chat-brand-name" size={14.5} label={tb('brand.name')} />
        </span>
      </div>
      <nav aria-label={t('nav.label')} className="nav-items">
        {PLACES.map((p) => (
          <div key={p} className="nav-group">
            <button
              type="button"
              className={`nav-item${place === p ? ' is-active' : ''}`}
              data-testid={`nav-${p}`}
              aria-current={place === p ? 'page' : undefined}
              onClick={() => onPlace(p)}
            >
              <PlaceIcon place={p} />
              <span className="nav-item-name">{t(`nav.${p}`)}</span>
            </button>
            {p === 'chat' && <div className="nav-rail-list">{children}</div>}
          </div>
        ))}
      </nav>
      <div className="chat-list-foot">{gear}</div>
    </div>
  )
}
