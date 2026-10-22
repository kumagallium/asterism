// 中央の領域＝チャット（PR 11a・11g で見慣れたチャットの文法に）。人の文は右の吹き出し、AI の文は地の文
// （先頭に小さな印）＋図への提案の札（ReplyCard）。時系列に並べ、下に入力欄。
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { BrandMark, PaperclipIcon } from '../icons'
import type { BindingDecision, Point, Session, Undone } from './chatApi'
import { confirmTargets, proposedCount, type Highlight } from './chatState'
import { PointCard } from './PointCard'
import { Composer } from './Composer'
import { ReplyCard } from './ReplyCard'

export interface ChatPaneProps {
  /** 無ければ新しい話（まだ保存していない）。 */
  session: Session | null
  loading: boolean
  /** 読み込みの失敗（話が開けない）。 */
  loadError: string | null
  busy: boolean
  /** 手番を送っている最中（返事を待っている）。 */
  sending?: boolean
  /** 送った瞬間の人の文（返りが来るまで会話の末尾に出す）。 */
  pendingUser?: string | null
  /** 送ったのは表（返事の場所に「読んでいます…」を出す）。 */
  pendingReading?: boolean
  /** 手番・✓・戻すの失敗の 1 行（入力欄の上）。 */
  error: string | null
  noKey: boolean
  onSend: (text: string) => Promise<boolean>
  /** 表を添える。 */
  onAttach?: (files: File[]) => Promise<boolean> | boolean | void
  /** 読んでいる手番の id → 進みの 1 行。 */
  progress?: Readonly<Record<string, string>>
  /** 読み取りが途中で止まった手番の id。 */
  interrupted?: Readonly<Record<string, boolean>>
  /** 返事の中の小さな選択を決めた。 */
  onChoose?: (turnId: string, noteIndex: number, value: string) => void
  /** 残した語の slug → 名前。 */
  names?: ReadonlyMap<string, string>
  onConfirm: (turnId: string, decisions?: BindingDecision[]) => void
  onRevert: (turnId: string) => void
  onOpenSettings: () => void
  /** 点を押した 1 枚（先頭に固定）。 */
  point?: Point | null
  /** 話の id → 題。 */
  titleOf?: (sessionId: string) => string | undefined
  /** 「中身」（<名前>を見せて）。 */
  onContents?: (name: string) => void
  onPointRevert?: (point: Point) => void
  /** 確認済みの用語・線を外す提案にする。 */
  onPointRemove?: (point: Point) => void
  /** 確認済みの手番を戻した結果（手番の id → 結果）。 */
  undone?: Record<string, Undone>
  /** 1 枚・表・出典の中で押したものを図で濃くする。 */
  onHighlight?: (h: Highlight | null) => void
}

/** 考えている印（3 つの点が順に濃くなる。reduced-motion では静止）。 */
export function Thinking() {
  return (
    <span className="chat-thinking" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  )
}

export function ChatPane(p: ChatPaneProps) {
  const { t } = useTranslation('chat')
  const turns = p.session?.turns ?? []
  const targets = confirmTargets(turns)
  const count = proposedCount(turns)
  const endRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [turns.length, p.busy, p.sending, p.pendingUser])

  return (
    <div className="chat-pane">
      {p.point && (
        <div className="chat-pinned">
          <PointCard
            key={`${p.point.kind}|${p.point.iri}|${p.point.label}`}
            point={p.point}
            busy={p.busy}
            titleOf={p.titleOf}
            onContents={(name) => void p.onContents?.(name)}
            onRevert={(pt) => p.onPointRevert?.(pt)}
            onRemove={(pt) => p.onPointRemove?.(pt)}
            onHighlight={(h) => p.onHighlight?.(h)}
          />
        </div>
      )}
      <div className="chat-scroll" role="log" aria-live="polite">
        {p.loadError ? (
          <p className="promote-err" role="alert">
            {p.loadError}
          </p>
        ) : p.loading ? (
          <p className="chat-note-line">{t('list.loading')}</p>
        ) : turns.length === 0 && !p.sending && !p.pendingUser ? (
          <p className="chat-empty">{t('empty.first')}</p>
        ) : (
          turns.map((turn) => (
            <div key={turn.id} className="chat-turn">
              {turn.user && (
                <div className="chat-msg chat-msg--user">
                  <div className="chat-bubble">
                    {turn.user.text}
                    {turn.user.attachments.map((a, i) => (
                      <div key={i} className="chat-bubble-file" data-testid="user-attachment">
                        <PaperclipIcon size={14} />
                        {a.name}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <ReplyCard
                turn={turn}
                canConfirm={targets.has(turn.id)}
                count={count}
                busy={p.busy}
                onConfirm={p.onConfirm}
                onRevert={p.onRevert}
                onAsk={(q) => p.onSend(q)}
                names={p.names}
                progress={p.progress?.[turn.id]}
                interrupted={p.interrupted?.[turn.id]}
                onChoose={p.onChoose}
                onHighlight={p.onHighlight}
                undone={p.undone?.[turn.id]}
              />
            </div>
          ))
        )}
        {p.pendingUser && (
          <div className="chat-msg chat-msg--user" data-testid="pending-user">
            <div className="chat-bubble">
              {p.pendingReading ? (
                p.pendingUser.split('\n').map((name, i) => (
                  <div key={i} className="chat-bubble-file">
                    <PaperclipIcon size={14} />
                    {name}
                  </div>
                ))
              ) : (
                p.pendingUser
              )}
            </div>
          </div>
        )}
        {p.sending && !!p.pendingUser && (
          <div className="chat-msg chat-msg--ai" data-testid="reply-thinking">
            <BrandMark size={18} className="chat-ai-mark" />
            <div className="chat-ai-body">
              <p className="chat-ai-text chat-ai-text--wait">
                <Thinking />
                {p.pendingReading ? t('attach.reading') : t('reply.thinking')}
              </p>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div className="chat-foot">
        {p.error && (
          <p className="promote-err" role="alert">
            {p.error}
          </p>
        )}
        <Composer onSend={p.onSend} onAttach={p.onAttach} busy={p.busy} noKey={p.noKey} onOpenSettings={p.onOpenSettings} />
      </div>
    </div>
  )
}
