// 「文」のタブ（PR 11o）: 選んだ点について知識グラフが言っていることを 1 行ずつ。承認済み＝濃い・未承認＝薄い。
// 行の右に 外す・直す・承認（承認は未承認の行だけ・この話に承認済みのテーマがあるときだけ）。描くだけ（送るのは ChatApp）。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { SentenceRow } from './sentenceRows'

export interface SentencesPanelProps {
  rows: SentenceRow[]
  /** 何も選んでいない（空のとき案内の文が変わる）。 */
  noSelection: boolean
  /** この話に承認済みのテーマがある（1 行の承認を出してよい）。 */
  hasTheme: boolean
  busy: boolean
  /** 点を選んでいるのに文が 0 件のときの 1 行（無ければ既定）。 */
  emptyText?: string
  onPress: (row: SentenceRow | null) => void
  onRemove: (row: SentenceRow) => void
  onFix: (row: SentenceRow) => void
  onApprove: (row: SentenceRow) => void
}

export function SentencesPanel({ rows, noSelection, hasTheme, busy, emptyText, onPress, onRemove, onFix, onApprove }: SentencesPanelProps) {
  const { t } = useTranslation('chat')
  const [lit, setLit] = useState<number | null>(null)
  const proposedOnly = noSelection && rows.length > 0
  return (
    <section className="chat-sentences-panel" data-testid="sentences-panel">
      {proposedOnly && <h3 className="chat-sentences-head">{t('sentences.proposedHead')}</h3>}
      {rows.length === 0 ? (
        <p className="chat-note-line" data-testid="sentences-empty">
          {noSelection ? t('sentences.hint') : (emptyText ?? t('point.noSentence'))}
        </p>
      ) : (
        <ul className="chat-sentences-list">
          {rows.map((r, i) => (
            <li key={`${r.text}|${i}`} className={`chat-sentences-row${r.approved ? '' : ' is-thin'}${lit === i ? ' is-lit' : ''}`} data-testid="sentence-row" data-approved={r.approved ? 'true' : 'false'}>
              <button
                type="button"
                className="link-btn chat-sentences-text"
                onClick={() => {
                  const on = lit !== i
                  setLit(on ? i : null)
                  onPress(on ? r : null)
                }}
              >
                {r.text}
              </button>
              <span className="chat-sentences-actions">
                <button type="button" className="link-btn" data-testid="sentence-remove" disabled={busy} onClick={() => onRemove(r)}>
                  {t('sentences.remove')}
                </button>
                <button type="button" className="link-btn" data-testid="sentence-fix" disabled={busy} onClick={() => onFix(r)}>
                  {t('sentences.fix')}
                </button>
                {!r.approved && hasTheme && r.draft?.turnId && (
                  <button type="button" className="link-btn" data-testid="sentence-approve" disabled={busy} onClick={() => onApprove(r)}>
                    {t('sentences.approve')}
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
