import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ConsultDrawer } from '../consult/ConsultDrawer'
import { registerWorkspaceChat } from '../consult/workspaceChatActive'
import { useHostedState } from '../kantan/hostedStage'
import { canSwitch, dataDest, stageMark, type DestPick } from './conversationContext'

// ホームの左の会話（データを置いているあいだ）。契約 pr8c §2。右にかんたんが出ている間、1 つの欄で 2 つの話を扱う:
//   (b) ③の列の結びつき — DatasetChat（/api/consult/graph screen=dataset・bind_column の提案カード）。ウィザードが
//       持つ会話を、この欄の置き場（`setSlot` の div）へ portal してもらう（表の更新・件数はウィザードのまま）。
//   (a) 設計の相談 — ConsultDrawer の中身（/api/design/consult・列の意味・種類名・ID の提案を表に反映）。
// 送り先は**段で決まる**（conversationContext.dataDest）。欄の上の 1 行が「いま: ③ 意味をつける — …」と言う。
// 両方の中身は mount したまま、見せるほうだけ切り替える（履歴・入力を失わない）。

export function AddConversation({
  open,
  onOpen,
  onFold,
  setSlot,
}: {
  open: boolean
  onOpen: () => void
  onFold: () => void
  /** ③の会話の置き場（DatasetChat がここへ portal する）。 */
  setSlot: (el: HTMLElement | null) => void
}) {
  const { t } = useTranslation()
  const h = useHostedState()
  const [pick, setPick] = useState<DestPick | null>(null)
  // ホームは自分の欄に会話を持つ — 右下の「AI に相談」は出さない（会話が 2 つにならない）
  useEffect(() => registerWorkspaceChat(), [])

  // 「空欄の意味を AI に相談して埋める」（③の会話があるとき）— 列の結びつきの会話を前に出す
  const [seenAsks, setSeenAsks] = useState(h.bindAsks)
  if (h.bindAsks !== seenAsks) {
    setSeenAsks(h.bindAsks)
    setPick({ recipe: 3, dest: 'bind' })
  }
  const openedFor = useRef(h.bindAsks)
  useEffect(() => {
    if (h.bindAsks === openedFor.current) return
    openedFor.current = h.bindAsks
    onOpen()
  }, [h.bindAsks, onOpen])
  // 「迷ったら AI に相談」など、設計の相談を求めるボタン — この欄の設計の相談を前に出す
  const wantConsult = useCallback(() => {
    setPick({ recipe: h.recipe ?? 3, dest: 'consult' })
    onOpen()
  }, [h.recipe, onOpen])

  const dest = dataDest(h, pick)
  const stage = h.recipe ?? 1
  const hint = dest === 'bind' ? t('sky:now.hint.bind') : h.done ? t('sky:now.hint.done') : t(`sky:now.hint.s${stage}`)
  const now = t('sky:now.data', { mark: stageMark(stage), step: t(`kantan:recipe.step${stage}`), hint })

  return (
    <div className={`ws-chat sky-conv sky-conv--add${open ? '' : ' is-folded'}`} data-testid="add-chat" data-dest={dest}>
      <aside className="thm-chat sky-addchat" aria-label={t('themes:chat.aria')} hidden={!open}>
        <header className="thm-chat-head">
          <span className="sky-now" data-testid="now-line">
            {now}
          </span>
          <span className="thm-chat-head-actions">
            {canSwitch(h) && (
              <button
                type="button"
                className="link-btn"
                onClick={() => setPick({ recipe: 3, dest: dest === 'bind' ? 'consult' : 'bind' })}
                data-testid="now-switch"
              >
                {t(dest === 'bind' ? 'sky:now.toConsult' : 'sky:now.toBind')}
              </button>
            )}
            <button type="button" className="btn btn--ghost btn--sm thm-chat-fold" onClick={onFold} data-testid="chat-fold">
              {t('themes:chat.fold')}
            </button>
          </span>
        </header>
        <div className="sky-add-slot" ref={setSlot} hidden={dest !== 'bind'} data-testid="add-slot" />
        <div className="sky-add-consult" hidden={dest !== 'consult'}>
          <ConsultDrawer embedded onRequested={wantConsult} />
        </div>
      </aside>
      {!open && (
        <button type="button" className="btn ws-chat-open" onClick={onOpen} data-testid="chat-open">
          {t('themes:chat.open')}
        </button>
      )}
    </div>
  )
}
