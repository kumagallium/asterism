// 返事 1 枚（PR 11a・11g）。AI の行＝先頭の印＋地の文（text）＋差し込みの札（図への提案: sentences → questions（チップ）→ notes →
// 下の行（✓・戻す））。札の上の行は状態（図への提案 N 件／確認済み／取り消し済み）。
// notes.kind の全部（ai_unset・ai_failed・dropped・no_question・commit_error・info）と questions を描ける（11b が使う）。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BrandMark, CheckIcon, PaperclipIcon } from '../icons'
import { Thinking } from './ChatPane'
import type { BindingDecision, Decided, Note, Turn, Undone } from './chatApi'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ReplyAnswer, ReplyTable } from './chatApi'
import { bindingDecisions, keptName, settleAsk, showMachineMark, turnKind, type Highlight } from './chatState'

/** 箇条書きで出す文の数（それ以上は「ほか N 件」の折りたたみ）。 */
export const SENTENCE_LIMIT = 6

export interface ReplyCardProps {
  turn: Turn
  /** この手番に ✓ を出すか（`chatState.confirmTargets`）。 */
  canConfirm: boolean
  /** ✓ で置く差分の数（`chatState.proposedCount`）。 */
  count: number
  /** 送信中・✓ 中・戻す中はボタンを止める。 */
  busy: boolean
  onConfirm: (turnId: string, decisions?: BindingDecision[]) => void
  onRevert: (turnId: string) => void
  /** チップを押した（その文を送る）。 */
  /** 送れたら true（失敗なら false で、チップの印を戻す）。 */
  onAsk: (question: string) => Promise<boolean> | boolean | void
  /** 残した語の slug → 名前（引けなければ slug のまま）。 */
  names?: ReadonlyMap<string, string>
  /** 読んでいる手番の進みの 1 行（`state: running`）。 */
  progress?: string
  /** 読み取りが途中で止まった（購読を終えても読んでいる印のまま）。 */
  interrupted?: boolean
  /** 返事の中の小さな選択を決めた（手番・注記の番号・値）。 */
  onChoose?: (turnId: string, noteIndex: number, value: string) => void
  /** 確認済みの手番を戻した結果（失敗なら failed つき）。 */
  undone?: Undone
  /** 表の種類・出典を押した（図で濃く）。 */
  onHighlight?: (h: Highlight | null) => void
}

function stamp(iso: string, lang: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(lang, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function NoteLine({ note, name, busy, onDecide }: { note: Note; name: string; busy: boolean; onDecide?: (value: string) => void }) {
  const { t } = useTranslation('chat')
  const [picked, setPicked] = useState(note.choice?.default ?? '')
  const cls = note.kind === 'commit_error' ? 'promote-err' : `chat-note chat-note--${note.kind}`
  const choice = note.choice
  const chosen = choice?.chosen
  return (
    <div className={cls} data-note-kind={note.kind} role={note.kind === 'commit_error' ? 'alert' : undefined}>
      <span>{note.text}</span>
      {choice && chosen === undefined && (
        <fieldset className="chat-choice">
          <legend>{choice.label}</legend>
          {choice.options.map((o) => (
            <label key={o.value}>
              <input type="radio" name={name} value={o.value} checked={picked === o.value} disabled={busy} onChange={() => setPicked(o.value)} />
              {o.label}
            </label>
          ))}
          {onDecide && (
            <button type="button" className="btn btn--sm" data-testid="choice-decide" disabled={busy || picked === ''} onClick={() => onDecide(picked)}>
              {t('attach.decide')}
            </button>
          )}
        </fieldset>
      )}
      {choice && chosen !== undefined && (
        <span className="chat-mark chat-mark--confirmed" data-testid="choice-chosen">
          {t('attach.chosen', { label: choice.options.find((o) => o.value === chosen)?.label ?? chosen })}
        </span>
      )}
    </div>
  )
}

/** 「見せて」の表（20 行・横スクロール）。名前を押すとその種類を図で濃く。 */
export function ReplyTableView({ table, onHighlight }: { table: ReplyTable; onHighlight?: (h: Highlight | null) => void }) {
  const { t } = useTranslation('chat')
  const more = Math.max(0, table.total - table.rows.length)
  return (
    <div className="chat-table" data-testid="reply-table">
      <p className="chat-table-kind">
        <button type="button" className="link-btn" onClick={() => onHighlight?.({ iris: [table.kind.iri] })}>
          {table.kind.label || table.kind.iri}
        </button>
      </p>
      {table.rows.length === 0 ? (
        <p className="chat-note-line">{t('table.empty')}</p>
      ) : (
        <div className="chat-table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{t('table.kindCol')}</th>
                {table.columns.map((c) => (
                  <th key={c.iri} scope="col">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((r) => (
                <tr key={r.iri}>
                  <th scope="row">
                    <button type="button" className="link-btn" onClick={() => onHighlight?.({ iris: [table.kind.iri] })}>
                      {r.label || r.iri}
                    </button>
                  </th>
                  {table.columns.map((c) => {
                    const cell = r.cells[c.iri]
                    const tx = cell?.text ?? ''
                    const short = tx.length > 24 ? `${tx.slice(0, 24)}…` : tx
                    return (
                      <td key={c.iri} title={tx.length > 24 ? tx : undefined}>
                        {cell?.iri ? (
                          <button type="button" className="link-btn" onClick={() => onHighlight?.({ iris: [cell.iri as string] })}>
                            {short}
                          </button>
                        ) : (
                          short
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {more > 0 && <p className="chat-note-line">{t('table.more', { n: more })}</p>}
    </div>
  )
}

/** Ask の答え（出典つき）。 */
export function ReplyAnswerView({ answer, onHighlight }: { answer: ReplyAnswer; onHighlight?: (h: Highlight | null) => void }) {
  const { t } = useTranslation('chat')
  return (
    <div className="chat-answer" data-testid="reply-answer">
      <div className="chat-answer-text answer-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer.text}</ReactMarkdown>
      </div>
      {answer.answered === false && (
        <p className="chat-note-line" data-testid="reply-no-answer">
          {t('ask.noAnswer')}
        </p>
      )}
      {answer.unverified_sparql && (
        <p className="chat-answer-warning" role="note" data-testid="reply-unverified">
          {t('ask.unverified')}
        </p>
      )}
      {answer.citations.length > 0 && (
        <ul className="chat-citations" data-testid="reply-citations" aria-label={t('ask.sources')}>
          {answer.citations.map((c, i) => (
            <li key={i}>
              <button
                type="button"
                className="chat-citation"
                onClick={() => onHighlight?.({ iris: [c.iri], ...(c.dataset_id ? { datasetId: c.dataset_id } : {}) })}
              >
                <span className="chat-citation-name">{c.label || c.iri}</span>
                {c.dataset_label && <span className="sky-meta">{c.dataset_label}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {answer.warnings.map((w, i) => (
        <p key={i} className="chat-answer-warning" role="note">
          {w}
        </p>
      ))}
      {answer.notes.map((n, i) => (
        <p key={i} className="chat-note-line">
          {n}
        </p>
      ))}
      {answer.verified_tools.length > 0 && !answer.unverified_sparql && (
        <span className="chat-mark chat-mark--confirmed" data-testid="reply-verified" title={answer.verified_tools.join(' · ')}>
          {t('ask.verified')}
        </span>
      )}
    </div>
  )
}

/** 表を読んでいる最中の手番（AI の行に 1 行）。 */
function Reading({ progress, interrupted }: { progress?: string; interrupted?: boolean }) {
  const { t } = useTranslation('chat')
  return (
    <div className="chat-msg chat-msg--ai" data-testid={interrupted ? 'reply-interrupted' : 'reply-reading'}>
      <BrandMark size={18} className="chat-ai-mark" />
      <div className="chat-ai-body">
        <p className="chat-ai-text chat-ai-text--wait">
          {!interrupted && <Thinking />}
          {interrupted ? t('attach.interrupted') : progress ? t('attach.readingWith', { message: progress }) : t('attach.reading')}
        </p>
      </div>
    </div>
  )
}

export function ReplyCard(p: ReplyCardProps) {
  return p.turn.state === 'running' ? <Reading progress={p.progress} interrupted={p.interrupted} /> : <ReplyBody {...p} />
}

function ReplyBody({ turn, canConfirm, count, busy, onConfirm, onRevert, onAsk, names, onChoose, onHighlight, undone }: ReplyCardProps) {
  const { t, i18n } = useTranslation('chat')
  const r = turn.reply
  // 結ぶ／結ばないの選び（変えたものだけ持つ。✓ のときに既定へ重ねて送る）
  const [picked, setPicked] = useState<Readonly<Record<string, Decided>>>({})
  // 列の一致は 1 行ずつラジオで出すので、同じ文の箇条書きは省く
  const bindingSentence = (sn: string) => r.bindings.some((b) => sn.startsWith(`列「${b.column}」`) || sn.startsWith(`Column "${b.column}"`))
  const sentences = r.sentences.filter((sn) => !bindingSentence(sn))
  const head = sentences.slice(0, SENTENCE_LIMIT)
  const rest = sentences.slice(SENTENCE_LIMIT)
  const open = turn.state === 'proposed'
  const proposed = turn.state === 'proposed'
  const machineMark = showMachineMark(turn)
  const publish = turnKind(turn) !== 'theme'
  const [undoAsk, setUndoAsk] = useState(false)
  const [sent, setSent] = useState<ReadonlySet<string>>(new Set())
  const unsend = (q: string) =>
    setSent((cur) => {
      const n = new Set(cur)
      n.delete(q)
      return n
    })
  // 機械の手番は text と注記の先頭が同じ文になる。二重に出さない
  const notes = turn.by === 'machine' && r.notes[0] && r.notes[0].text.trim() === r.text.trim() ? r.notes.slice(1) : r.notes
  // text と箇条書きが同じ内容なら片方だけ。人の手番（user が null）は箇条書き 1 行だけ
  const sameAsOnly = sentences.length === 1 && r.text.trim() === sentences[0].trim()
  const showText = !!r.text && !r.answer && !sameAsOnly && !(turn.user === null && sentences.length > 0)
  // 札の上の行: 添えた表／図への提案 N 件（この手番の分）／確認済み／取り消し済み（Ask の答えや表だけの返事には出さない）
  const headKind = turn.attachment ? 'attachment' : turn.state === 'proposed' && !publish ? 'proposed' : turn.state === 'confirmed' ? 'confirmed' : turn.state === 'reverted' ? 'reverted' : null
  const ownCount = r.ops.length
  const showFoot = (canConfirm && turn.state !== 'none') || (turn.state === 'confirmed' && !!turn.commit) || proposed
  const hasRemoved = turn.state === 'confirmed' && !!turn.commit?.removed && (turn.commit.removed.terms > 0 || turn.commit.removed.lines > 0)
  const hasKept = turn.state === 'confirmed' && (turn.commit?.kept ?? []).length > 0
  // 中身の無い札（文だけの返事）は出さない
  const hasCard =
    !!headKind || machineMark || sentences.length > 0 || !!r.table || !!r.answer || r.suggestions.length > 0 || r.bindings.length > 0 || r.questions.length > 0 || notes.length > 0 || hasRemoved || hasKept || !!undone || showFoot
  return (
    <div className="chat-msg chat-msg--ai">
      <BrandMark size={18} className="chat-ai-mark" />
      <div className="chat-ai-body">
        {showText && <p className="chat-ai-text">{r.text}</p>}
      {hasCard && (
      <div className="chat-card" data-testid="reply-card" data-state={turn.state}>
        {(headKind || machineMark) && (
          <div className={`chat-card-head${headKind === 'confirmed' ? ' chat-card-head--confirmed' : ''}`}>
            {headKind === 'attachment' && turn.attachment && (
              <span className="chat-card-head-main" data-testid="reply-attachment">
                <PaperclipIcon size={14} />
                {turn.attachment.name}・{t('attach.rows', { n: turn.attachment.rows })}
              </span>
            )}
            {headKind === 'proposed' && (
              <span className="chat-card-head-main chat-mark--proposed">{ownCount > 0 ? t('reply.cardProposed', { n: ownCount }) : t('reply.cardProposedNone')}</span>
            )}
            {headKind === 'confirmed' && (
              <span className="chat-card-head-main">
                <CheckIcon size={14} />
                {t('reply.confirmed', { at: stamp(turn.commit?.at || turn.at, i18n.language) })}
              </span>
            )}
            {headKind === 'reverted' && <span className="chat-card-head-main">{t('reply.reverted')}</span>}
            {machineMark && <span className="chat-by-machine">{t('reply.byMachine')}</span>}
            {turn.attachment && turn.state === 'confirmed' && (
              <span className="chat-card-head-main chat-card-head--confirmed">
                <CheckIcon size={14} />
                {t('reply.confirmed', { at: stamp(turn.commit?.at || turn.at, i18n.language) })}
              </span>
            )}
            {turn.attachment && turn.state === 'reverted' && <span className="chat-card-head-main">{t('reply.reverted')}</span>}
          </div>
        )}
        {sentences.length > 0 && (
          <ul className="chat-sentences">
            {head.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
            {rest.length > 0 && (
              <li className="chat-sentences-more">
                <details>
                  <summary>{t('reply.more', { n: rest.length })}</summary>
                  <ul>
                    {rest.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </details>
              </li>
            )}
          </ul>
        )}
        {r.table && <ReplyTableView table={r.table} onHighlight={onHighlight} />}
        {r.answer && <ReplyAnswerView answer={r.answer} onHighlight={onHighlight} />}
        {r.suggestions.length > 0 && sentences.length === 0 && (
          <ul className="chat-sentences">
            {r.suggestions.map((sg, i) => (
              <li key={i}>{t('attach.suggestionLine', { subject: sg.subject, term: sg.term })}</li>
            ))}
          </ul>
        )}
        {r.bindings.length > 0 && (
          <ul className="chat-bindings" data-testid="reply-bindings">
            {r.bindings.map((b, bi) => {
              const now = picked[String(bi)] ?? b.decided
              const name = `bind-${turn.id}-${bi}`
              return (
                <li key={bi} className="chat-binding" data-column={b.column}>
                  <span>{t('attach.bindLine', { column: b.column, term: b.term_label || b.label || b.term })}</span>
                  <span className="chat-binding-radios">
                    {(['bind', 'skip'] as const).map((d) => (
                      <label key={d}>
                        <input
                          type="radio"
                          name={name}
                          value={d}
                          checked={now === d}
                          disabled={busy || !open}
                          onChange={() => setPicked((cur) => ({ ...cur, [String(bi)]: d }))}
                        />
                        {t(d === 'bind' ? 'attach.bind' : 'attach.skip')}
                      </label>
                    ))}
                  </span>
                </li>
              )
            })}
          </ul>
        )}
        {r.questions.length > 0 && (
          <div className="chat-chips">
            {r.questions.map((q) => (
              <button
                key={q}
                type="button"
                className={`chat-chip${sent.has(q) ? ' is-sent' : ''}`}
                disabled={busy || sent.has(q)}
                onClick={() => {
                  setSent((cur) => new Set([...cur, q]))
                  void settleAsk(() => onAsk(q)).then((ok) => {
                    if (!ok) unsend(q)
                  })
                }}
              >
                {q}
              </button>
            ))}
          </div>
        )}
        {notes.map((n, i) => (
          <NoteLine
            key={i}
            note={n}
            name={`choice-${turn.id}-${i}`}
            busy={busy}
            onDecide={onChoose ? (value) => onChoose(turn.id, r.notes.indexOf(n), value) : undefined}
          />
        ))}
        {turn.state === 'confirmed' && turn.commit?.removed && (turn.commit.removed.terms > 0 || turn.commit.removed.lines > 0) && (
          <p className="chat-note-line" data-testid="reply-removed">
            {t('reply.removed', { terms: turn.commit.removed.terms, lines: turn.commit.removed.lines })}
          </p>
        )}
        {turn.state === 'confirmed' &&
          (turn.commit?.kept ?? []).map((k) => (
            <p key={k.slug} className="chat-note-line" data-testid="reply-kept">
              {t('reply.kept', { slug: keptName(k.slug, names), reason: k.reason })}
            </p>
          ))}
        {undone && (
          <>
            {(undone.failed ?? []).map((f, i) => (
              <p key={i} className="promote-err" role="alert" data-testid="reply-undo-failed">
                {t('reply.undoneFailed', { what: f.what, reason: f.reason })}
              </p>
            ))}
            {!undone.failed && (
              <p className="chat-note-line" data-testid="reply-undone">
                {t('reply.undone', { lines: undone.lines, terms: undone.terms })}
                {undone.kept.length > 0 && t('reply.undoneKept', { names: undone.kept.map((k) => keptName(k.slug, names)).join('・') })}
              </p>
            )}
          </>
        )}
        {showFoot && (
          <div className="chat-card-foot">
            {canConfirm && turn.state !== 'none' && (
              <button type="button" className="btn btn--sm chat-confirm" data-testid="reply-confirm" disabled={busy} onClick={() => onConfirm(turn.id, r.bindings.length > 0 ? bindingDecisions(r.bindings, picked) : undefined)}>
                <CheckIcon size={14} />
                {publish ? t('reply.publish') : t('reply.confirm')}
                {!publish && count > 0 && ` ${t('reply.count', { n: count })}`}
              </button>
            )}
            {turn.state === 'confirmed' && turn.commit && !undoAsk && (
              <button type="button" className="link-btn" data-testid="reply-undo" disabled={busy} onClick={() => setUndoAsk(true)}>
                {t('reply.revert')}
              </button>
            )}
            {turn.state === 'confirmed' && turn.commit && undoAsk && (
              <span className="chat-point-ask" data-testid="reply-undo-ask">
                <span>{t('reply.undoAsk', { terms: turn.commit.minted.length, lines: turn.commit.lines })}</span>
                <button
                  type="button"
                  className="link-btn"
                  data-testid="reply-undo-yes"
                  disabled={busy}
                  onClick={() => {
                    setUndoAsk(false)
                    onRevert(turn.id)
                  }}
                >
                  {t('reply.revert')}
                </button>
              </span>
            )}
            {proposed && (
              <button type="button" className="link-btn" data-testid="reply-revert" disabled={busy} onClick={() => onRevert(turn.id)}>
                {t('reply.revert')}
              </button>
            )}
          </div>
        )}
      </div>
      )}
      </div>
    </div>
  )
}
