// 「この話の周り」（PR 11h）: 地図の下の補足のパネル。今の札の図（ChartPane）を、この話と選んだ点の近傍だけに絞って出す。
// 折りたたみは localStorage に覚える（既定は開く）。核が空なら何も出さない。
import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChartPane } from './ChartPane'
import type { Highlight, PointSel } from './chatState'
import type { LocalSky } from './graph/neighborhood'
import type { SkyDraft } from './chatApi'
import { readOpen, writeOpen } from './localOpen'
import type { SkyLoad } from './useSky'

export interface LocalPanelProps {
  lang: 'ja' | 'en'
  /** `localSky` の結果（近傍に絞った図の材料）。 */
  local: LocalSky
  /** 取り直しで増えた点（生まれた輪）。 */
  born?: string[]
  onSelect?: (sel: PointSel | null) => void
  highlight?: Highlight | null
}

const NO_BORN: string[] = []

export function LocalPanel({ lang, local, born = NO_BORN, onSelect, highlight = null }: LocalPanelProps) {
  const { t } = useTranslation('chat')
  const [open, setOpen] = useState(readOpen)
  const toggle = useCallback(() => {
    setOpen((cur) => {
      writeOpen(!cur)
      return !cur
    })
  }, [])
  const data = useMemo<SkyLoad>(
    () => ({ status: 'ok', sky: local.sky, proposed: local.drafts as SkyDraft[], proposedDatasets: local.proposedDatasets, born }),
    [local, born],
  )
  if (local.empty) return null
  const label = open ? t('local.close') : t('local.open')
  return (
    <section className="chat-local" data-testid="chat-local">
      <div className="chat-local-head">
        <h3>{t('local.title')}</h3>
        {local.more > 0 && <span className="chat-local-more">{t('local.more', { n: local.more })}</span>}
        <button type="button" className="chat-icon-btn" data-testid="local-toggle" aria-expanded={open} aria-label={label} title={label} onClick={toggle}>
          {open ? '−' : '+'}
        </button>
      </div>
      {open && <ChartPane lang={lang} data={data} hideFacts onSelect={onSelect} highlight={highlight} />}
    </section>
  )
}
