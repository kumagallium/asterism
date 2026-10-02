// カードの結果が元の全部ではないときの 1 行の注記（文言の選び方は truncationNote.ts）。
import { useTranslation } from 'react-i18next'
import type { CardToolResult } from './cardsApi'
import { truncationNote } from './truncationNote'

export function CardTruncationNote({ result }: { result: CardToolResult }) {
  const { t, i18n } = useTranslation('cards')
  const note = truncationNote(result)
  if (!note) return null
  // 点数は桁区切りつきで（12000 → 12,000）。
  const fmt = new Intl.NumberFormat(i18n.language)
  const params = {
    shown: fmt.format(note.params.shown),
    ...(note.params.total !== undefined ? { total: fmt.format(note.params.total) } : {}),
  }
  return <p className="cardpage-truncation">{t(note.key, params)}</p>
}
