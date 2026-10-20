import { useTranslation } from 'react-i18next'
import { type ProposalItem, dropReason, openRows } from './workspaceState'

// 会話の中の「提案カード」。サーバが組んだ文（sentences）を 1 行ずつ・理由を薄く出し、
// 「そう（N 件）」「違う」「1 件ずつ」で返事をする。「そう」→ 図が変わりカードは「確定済み N 件」に畳む。
// 「違う」→ 却下が保存され、次の提案に出ない。「1 件ずつ」→ 各行に ✓／✕。
// 人の直接編集（文のプルダウン・線のドラッグ・手で足す）の記録も、同じカードの畳んだ形で残す。

export function ProposalCard({
  item,
  disabled,
  onYes,
  onNo,
  onPer,
  onRowYes,
  onRowNo,
}: {
  item: ProposalItem
  /** 別の操作の最中（二重に押させない）。 */
  disabled: boolean
  onYes: () => void
  onNo: () => void
  onPer: () => void
  onRowYes: (index: number) => void
  onRowNo: (index: number) => void
}) {
  const { t } = useTranslation()
  const open = openRows(item)
  const yes = item.decided.filter((d) => d === 'yes').length
  const done = open.length === 0
  const lock = disabled || item.busy

  if (done) {
    const title =
      item.by === 'human'
        ? t('themes:chat.doneHuman', { n: item.ops.length })
        : yes > 0
          ? t('themes:chat.done', { n: yes })
          : t('themes:chat.skipped')
    return (
      <details className="thm-card-p thm-card-p--done" data-testid="proposal-done">
        <summary>{title}</summary>
        <ul className="thm-card-p-rows">
          {item.sentences.map((s, i) => (
            <li key={i} className={`thm-card-p-row is-${item.decided[i] ?? 'yes'}`}>
              <span className="thm-card-p-text">{s}</span>
              {item.notes[i] && <span className="thm-card-p-note">{t(`themes:chat.dropped.${dropReason(item.notes[i]!)}`)}</span>}
            </li>
          ))}
        </ul>
      </details>
    )
  }

  return (
    <section className="thm-card-p" data-testid="proposal-card" aria-busy={item.busy}>
      <ul className="thm-card-p-rows">
        {item.sentences.map((s, i) => {
          const d = item.decided[i]
          const reason = typeof item.ops[i].reason === 'string' ? (item.ops[i].reason as string) : ''
          return (
            <li key={i} className={`thm-card-p-row${d ? ` is-${d}` : ''}`} data-testid="proposal-row">
              <span className="thm-card-p-text">{s}</span>
              {d === null && item.per && (
                <span className="thm-card-p-ticks">
                  <button
                    type="button"
                    className="thm-tick"
                    aria-label={t('themes:chat.rowYes')}
                    title={t('themes:chat.rowYes')}
                    disabled={lock}
                    onClick={() => onRowYes(i)}
                  >
                    ✓
                  </button>
                  <button
                    type="button"
                    className="thm-tick thm-tick--no"
                    aria-label={t('themes:chat.rowNo')}
                    title={t('themes:chat.rowNo')}
                    disabled={lock}
                    onClick={() => onRowNo(i)}
                  >
                    ✕
                  </button>
                </span>
              )}
              {d === 'yes' && <span className="thm-card-p-mark">✓ {t('themes:chat.rowDone')}</span>}
              {d === 'no' && <span className="thm-card-p-mark">✕ {t('themes:chat.rowSkipped')}</span>}
              {d === 'dropped' && item.notes[i] && (
                <span className="thm-card-p-note">{t(`themes:chat.dropped.${dropReason(item.notes[i]!)}`)}</span>
              )}
              {reason && d === null && <span className="thm-reason">{reason}</span>}
            </li>
          )
        })}
      </ul>
      <div className="thm-card-p-actions">
        <button type="button" className="btn btn--accent btn--sm" disabled={lock} onClick={onYes} data-testid="proposal-yes">
          {t('themes:chat.yes', { n: open.length })}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={lock} onClick={onNo} data-testid="proposal-no">
          {t('themes:chat.no')}
        </button>
        {!item.per && open.length > 1 && (
          <button type="button" className="btn btn--ghost btn--sm" disabled={lock} onClick={onPer} data-testid="proposal-per">
            {t('themes:chat.per')}
          </button>
        )}
      </div>
    </section>
  )
}

/** 最初の下書き（①→②）の 1 枚目のカード。提案（薄い）のまま見せ、「そう（全部）」で本物にする。 */
export function DraftCard({
  conceptNames,
  relationSentences,
  per,
  total,
  left,
  onYesAll,
  onPer,
  onRowYes,
  onRowNo,
}: {
  /** まだ提案（薄い）の概念の名前（key 付き）。 */
  conceptNames: { key: string; name: string; kind: 'class' | 'property' }[]
  /** まだ提案（薄い）の関係の文。`index` は下書きの関係の添字。 */
  relationSentences: { id: string; index: number; sentence: string; reason: string }[]
  per: boolean
  /** 最初の下書きの概念・関係の数。 */
  total: { concepts: number; relations: number }
  left: number
  onYesAll: () => void
  onPer: () => void
  onRowYes: (row: { type: 'concept'; key: string } | { type: 'relation'; id: string }) => void
  onRowNo: (row: { type: 'concept'; key: string } | { type: 'relation'; index: number }) => void
}) {
  const { t } = useTranslation()
  if (left === 0) {
    return (
      <details className="thm-card-p thm-card-p--done" data-testid="draft-done">
        <summary>{t('themes:chat.draftDone', { m: total.concepts, n: total.relations })}</summary>
      </details>
    )
  }
  return (
    <section className="thm-card-p thm-card-p--draft" data-testid="draft-card">
      <h3 className="thm-card-p-h">{t('themes:chat.draftTitle', { m: total.concepts, n: total.relations })}</h3>
      {!per && conceptNames.length > 0 && (
        <p className="thm-card-p-concepts">
          {conceptNames.map((c) => (
            <span key={c.key} className={`thm-card-p-chip thm-card-p-chip--${c.kind}`}>
              {c.name}
            </span>
          ))}
        </p>
      )}
      <ul className="thm-card-p-rows">
        {per &&
          conceptNames.map((c) => (
            <li key={c.key} className="thm-card-p-row">
              <span className="thm-card-p-text">
                {c.name}
                <span className="thm-card-p-kind"> {t(`themes:kind.${c.kind}`)}</span>
              </span>
              <span className="thm-card-p-ticks">
                <button type="button" className="thm-tick" aria-label={t('themes:chat.rowYes')} onClick={() => onRowYes({ type: 'concept', key: c.key })}>
                  ✓
                </button>
                <button type="button" className="thm-tick thm-tick--no" aria-label={t('themes:chat.rowNo')} onClick={() => onRowNo({ type: 'concept', key: c.key })}>
                  ✕
                </button>
              </span>
            </li>
          ))}
        {relationSentences.map((r) => (
          <li key={r.id} className="thm-card-p-row">
            <span className="thm-card-p-text">{r.sentence}</span>
            {per && (
              <span className="thm-card-p-ticks">
                <button type="button" className="thm-tick" aria-label={t('themes:chat.rowYes')} onClick={() => onRowYes({ type: 'relation', id: r.id })}>
                  ✓
                </button>
                <button type="button" className="thm-tick thm-tick--no" aria-label={t('themes:chat.rowNo')} onClick={() => onRowNo({ type: 'relation', index: r.index })}>
                  ✕
                </button>
              </span>
            )}
            {r.reason && <span className="thm-reason">{r.reason}</span>}
          </li>
        ))}
      </ul>
      <div className="thm-card-p-actions">
        <button type="button" className="btn btn--accent btn--sm" onClick={onYesAll} data-testid="draft-yes-all">
          {t('themes:chat.yesAll')}
        </button>
        {!per && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onPer} data-testid="draft-per">
            {t('themes:chat.per')}
          </button>
        )}
      </div>
    </section>
  )
}
