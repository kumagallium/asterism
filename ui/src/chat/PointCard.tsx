// 点を押すと会話の先頭に出る 1 枚（PR 11d）。名前と種別・標準用語・文・来歴・中身・戻す・答える問い・入っている種類。
// 描くだけ（取得・戻すは ChatApp）。ボタンはこの 1 枚の中の「中身」「戻す」だけ。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Point, PointSentence } from './chatApi'
import { byKeyOf, showsContents, type Highlight } from './chatState'

export interface PointCardProps {
  point: Point
  /** 送信中・戻す中はボタンを止める。 */
  busy: boolean
  /** 話の id → 題（来歴の「話「…」」に使う。引けなければ「新しい話」）。 */
  titleOf?: (sessionId: string) => string | undefined
  onContents: (name: string) => void
  onRevert: (point: Point) => void
  /** 確認済みの用語・線を外す提案にする。 */
  onRemove?: (point: Point) => void
  /** 文を押した（その関係を図で濃く）。 */
  onHighlight: (h: Highlight | null) => void
}

const dayOf = (iso: string): string => (iso.length >= 10 ? iso.slice(0, 10) : iso)

export function PointCard({ point: p, busy, titleOf, onContents, onRevert, onRemove, onHighlight }: PointCardProps) {
  const { t } = useTranslation('chat')
  const [asking, setAsking] = useState(false)
  const [lit, setLit] = useState<number | null>(null)
  if (p.thin) {
    // 薄い星（未公開）: api を呼ばない小さな 1 枚
    return (
      <div className="sky-card chat-point" data-testid="point-card" data-kind="kind" data-thin="true">
        <h3>
          <span>{t('point.titleKind')}</span>
        </h3>
        <div className="sky-about-name">{p.label || p.iri}</div>
        {p.thin.dataset && <p className="sky-meta">{p.thin.dataset}</p>}
        <p className="sky-meta" data-testid="point-unpublished">
          {t('point.unpublished')}
        </p>
      </div>
    )
  }
  const title = {
    term: t('point.titleTerm'),
    kind: t('point.titleKind'),
    std: t('point.titleStd'),
    hub: t('point.titleHub'),
    draft: t('point.titleDraft'),
  }[p.kind as Exclude<Point['kind'], 'line'>]
  const sub = p.kind === 'term' && (p.term_kind === 'property' || p.term_kind === 'class') ? (p.term_kind === 'property' ? t('point.termProperty') : t('point.termClass')) : ''
  // 来歴: provenance があればそれ、無ければ最初の文のもの
  const first: PointSentence | undefined = p.sentences[0]
  const prov = p.provenance ?? (first ? { drafted_by: first.drafted_by, at: first.at, session_id: first.session_id } : null)
  const sessionId = prov?.session_id ?? null
  const std = p.standard
  // 線の 1 枚は見出しが文そのもの。同じ文は重ねない
  const lineText = p.kind === 'line' ? (p.sentences[0]?.text || p.label || p.iri) : ''
  const sentences = p.kind === 'line' ? p.sentences.filter((s) => s.text !== lineText) : p.sentences
  const showNone = p.kind === 'term' || p.kind === 'kind'
  // 外す提案（確認済みの用語・線）か、従来の取り消し（未確認の提案の手番）か
  // 確認済み（ストアにある）の用語・線は「外す提案」（✓ で確定）。未確認の札・線は、その提案の手番の取り消し。
  const canRemove = p.confirmed && (p.kind === 'term' || p.kind === 'line') && !!onRemove
  const canUndo = !p.confirmed && p.can_revert && !!p.revert
  const provLine = prov
    ? sessionId
      ? t('point.prov', { title: titleOf?.(sessionId) || t('point.untitled'), at: dayOf(prov.at), by: t(`point.${byKeyOf(prov.drafted_by)}`) })
      : t('point.provNoTitle', { at: dayOf(prov.at), by: t(`point.${byKeyOf(prov.drafted_by)}`) })
    : ''
  return (
    <div className="sky-card chat-point" data-testid="point-card" data-kind={p.kind}>
      <h3>
        <span>{p.kind === 'line' ? lineText : title}</span>
        {sub && <span className="sky-meta">{sub}</span>}
      </h3>
      {p.kind !== 'line' && <div className="sky-about-name">{p.label || p.iri}</div>}
      {std && (
        <p className="chat-point-std">
          <span className="sky-std">≡ {std.prefix ? `${std.prefix}:` : ''}{std.label}</span>
        </p>
      )}
      {(sentences.length > 0 || showNone) && (
        <ul className="chat-point-sentences" data-testid="point-sentences">
          {sentences.length === 0 ? (
            <li className="sky-meta">{t('point.noSentence')}</li>
          ) : (
            sentences.map((s, i) => (
              <li key={i}>
                <button
                  type="button"
                  className={`link-btn chat-point-sentence${lit === i ? ' is-lit' : ''}`}
                  onClick={() => {
                    const on = lit !== i
                    setLit(on ? i : null)
                    onHighlight(on && s.source && s.target ? { edge: { from: s.source, to: s.target } } : null)
                  }}
                >
                  {s.text}
                </button>
              </li>
            ))
          )}
        </ul>
      )}
      {provLine && (
        <p className="chat-point-prov" data-testid="point-prov">
          <span>{provLine}</span>
          {sessionId && (
            <>
              {' '}
              <a href={`#/t/${encodeURIComponent(sessionId)}`} data-testid="point-open-session">
                {t('point.openSession')}
              </a>
            </>
          )}
        </p>
      )}
      {(showsContents(p.kind, p.members.length) || canUndo || canRemove) && (
        <div className="sky-about-actions">
          {showsContents(p.kind, p.members.length) && (
            <button type="button" className="btn btn--sm" data-testid="point-contents" disabled={busy} onClick={() => onContents(p.label)}>
              {t('point.contents')}
            </button>
          )}
          {(canUndo || canRemove) && !asking && (
            <button type="button" className="link-btn" data-testid="point-revert" disabled={busy} onClick={() => setAsking(true)}>
              {t('point.revert')}
            </button>
          )}
          {(canUndo || canRemove) && asking && (
            <span className="chat-point-ask" data-testid="point-revert-ask">
              <span>{canUndo ? t('point.revertAsk') : t('point.removeAsk', { name: p.kind === 'line' ? lineText : p.label })}</span>
              <button
                type="button"
                className="btn btn--sm"
                data-testid="point-revert-yes"
                disabled={busy}
                onClick={() => {
                  setAsking(false)
                  if (canUndo) onRevert(p)
                  else onRemove?.(p)
                }}
              >
                {t('point.revertYes')}
              </button>
              <button type="button" className="link-btn" data-testid="point-revert-no" onClick={() => setAsking(false)}>
                {t('point.revertNo')}
              </button>
            </span>
          )}
        </div>
      )}
      {p.questions.length > 0 && (
        <dl className="sky-kv">
          <dt>{t('point.questions')}</dt>
          <dd data-testid="point-questions">
            {p.questions.map((q, i) => (
              <div key={i} className="chat-point-row">
                <span>{q.title}</span>
                <span className="sky-pills">
                  {q.knowledge > 0 && <span className="sky-pill sky-pill--wait">{t('point.qKnowledge', { n: q.knowledge })}</span>}
                  {q.answering > 0 && <span className="sky-pill sky-pill--data">{t('point.qData', { n: q.answering })}</span>}
                </span>
              </div>
            ))}
          </dd>
        </dl>
      )}
      {p.members.length > 0 && (
        <dl className="sky-kv">
          <dt>{t(p.kind === 'kind' || p.kind === 'std' ? 'point.boundTerms' : 'point.members')}</dt>
          <dd data-testid="point-members">
            {p.members.map((m, i) => (
              <div key={i} className="chat-point-row">
                <button type="button" className="link-btn" onClick={() => onHighlight({ iris: [m.iri] })}>
                  {m.label || m.iri}
                </button>
                {m.dataset_label && <span className="sky-meta">{m.dataset_label}</span>}
              </div>
            ))}
          </dd>
        </dl>
      )}
    </div>
  )
}
