import { useContext, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { WorkspaceChat } from '../consult/WorkspaceChat'
import type { GraphOp } from '../themes/consultGraphApi'
import { ChatSlotContext, askBind, setBindReady } from './hostedStage'

// ③「意味をつける」の右に置く「AI に相談して結ぶ」（design.md §16.2「データ」・契約 pr7c C1）。
// 列の意味を一言で伝える（「この列は粒径で、粒界散乱の指標」）→ AI が `bind_column` を提案 →
// 「そう」で `upper.json` に当てはめを足す（来歴: drafted_by=ai・まとめの ID・理由）。線になるのは
// **公開のとき**。AI に渡すのは列の見出しと型のヒントだけで、列の値は渡さない（api が組む）。
//
// 最初のカードは、機械が数えた「名前が一致する候補 N 件」（D2・AI を呼ばない）。候補そのものは
// 表の各行の下（FitSuggestion）に出ていて、ここは件数と行き先を言うだけ。
// 説明は 1 行（渡す情報の範囲はその行の title）。入力欄の placeholder に例。
// 設計（mapping.yaml）がまだ無い間は api が 422 を返すので、会話は設計ができてから（呼ぶ側が出す）。
//
// ホームの右の領域にかんたんが出ているとき（UI 再設計 PR 8c）は、左の会話の欄の置き場（ChatSlotContext）へ
// portal する — 表の右の列は無くなり、会話は左に 1 つ。state（提案カード・履歴）はここに残り、表の更新（onApplied）
// もウィザードのままつながる。置き場が無ければ（ホームの外）従来どおり表の右に置く。

export function DatasetChat({
  datasetId,
  lang,
  candidates,
  openSignal,
  onApplied,
}: {
  datasetId: string
  lang: 'ja' | 'en'
  /** 表の各行の下に出ている「名前が一致する候補」の合計（機械の数え・AI なし）。 */
  candidates: number
  /** 外のボタンが開く合図。 */
  openSignal: number
  /** 「そう」で当たった（呼ぶ側が `upper.json` を読み戻して、表に印を出す）。 */
  onApplied: (ops: GraphOp[]) => void
}) {
  const { t } = useTranslation()
  const slot = useContext(ChatSlotContext)
  // 左の欄に「③の会話が出ている」ことを伝える（送り先の既定が bind になる）
  useEffect(() => {
    setBindReady(true)
    return () => setBindReady(false)
  }, [])
  // 「空欄の意味を AI に相談して埋める」（外のボタン）で開く合図 → 左の欄がこの会話を前に出す
  const seenSignal = useRef(openSignal)
  useEffect(() => {
    if (openSignal === seenSignal.current) return
    seenSignal.current = openSignal
    askBind()
  }, [openSignal])
  const intro =
    candidates > 0 ? (
      <section className="thm-card-p" data-testid="fit-candidates-card">
        <h3 className="thm-card-p-h">{t('kantan:chat.candidates', { n: candidates })}</h3>
        <p className="ws-hint">{t('kantan:chat.candidatesNote')}</p>
      </section>
    ) : null
  const focus = (
    <div className="ws-focus">
      <p className="ws-hint" title={t('kantan:chat.privacy')}>
        {t('kantan:chat.lead')}
      </p>
    </div>
  )
  const chat = (
    <WorkspaceChat
      screen="dataset"
      context={{ dataset_id: datasetId }}
      lang={lang}
      intro={intro}
      focus={focus}
      placeholder={t('kantan:chat.example')}
      openSignal={openSignal}
      onApplied={({ ops }) => onApplied(ops)}
      testId="dataset-chat"
      embedded={!!slot}
    />
  )
  return slot ? createPortal(chat, slot) : chat
}
