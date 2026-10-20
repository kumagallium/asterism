import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { WorkspaceChat } from '../consult/WorkspaceChat'
import { bindUnlinkedMessage } from '../consult/workspaceChatState'
import { registerWorkspaceChat } from '../consult/workspaceChatActive'
import { useLlmSettings } from '../settings/context'
import type { SkyNode } from './skyLayout'

// ホーム（星図）の右の欄の下に置く「AI と相談」（契約 pr7c A2・B3）。7b の会話の部品（GraphChat・
// ProposalCard）をそのまま使う。星を選んで一言（「この種類は 試料 と同じもの？」）→ 提案カード →
// 「そう」で alignment graph に線（drafted_by=ai・まとめの ID・理由）→ 星図を取り直す
// （育つ輪と「答えられる問い N → M」の 1 行は SkyView が出す）。「違う」は却下を保存する。
// 「AI に結ばせる」（相談の見出しの右の小さなボタン・AI 設定時だけ）は定型の一言を送る。人が「そう」と
// 言うまで何も書かない。AI が未設定のときは相談の欄ごと出さない（手で決める ▸ が開いた状態で出る）。
// 履歴が空のあいだは、入力欄と 1 行の例だけ（大きな空白を出さない）。

export function SkyChat({
  node,
  lang,
  unlinkedKinds,
  onDecided,
}: {
  /** 選んでいる点（星・丸・用語・標準用語…）。星のときだけ「選んでいる星」として AI に伝える。 */
  node: SkyNode | undefined
  lang: 'ja' | 'en'
  /** 用語にまだ結ばれていない種類の数（「AI に結ばせる」の件数）。 */
  unlinkedKinds: number
  /** 当たった線の端の IRI（星図を取り直して輪を出す）。 */
  onDecided: (iris: string[]) => void
}) {
  const { t } = useTranslation('sky')
  const { isReady } = useLlmSettings()
  const [request, setRequest] = useState<{ id: number; text: string } | null>(null)
  const kind = node?.type === 'kind' ? node : null
  // ホームは自分の欄に相談を持つ — 欄ごと出さないときも、右下の「AI に相談」は出さない
  // （会話が 2 つにならない・契約 pr7c A3）
  useEffect(() => (isReady ? undefined : registerWorkspaceChat()), [isReady])
  if (!isReady) return null

  const focus = (
    <div className="ws-focus" data-testid="home-chat-focus">
      <p className="ws-hint">{t('chat.example')}</p>
    </div>
  )
  const bind = (
    <button
      type="button"
      className="btn btn--sm"
      disabled={unlinkedKinds === 0}
      title={t('chat.bindHint', { n: unlinkedKinds })}
      onClick={() => setRequest({ id: (request?.id ?? 0) + 1, text: bindUnlinkedMessage(lang) })}
      data-testid="bind-by-ai"
    >
      {t('chat.bind')}
    </button>
  )

  return (
    <div className="sky-chat" data-testid="sky-chat">
      <WorkspaceChat
        screen="home"
        context={kind ? { focus_kind: kind.iri } : {}}
        lang={lang}
        focus={focus}
        headAction={bind}
        request={request}
        onApplied={({ result }) => {
          const iris = [...new Set(result.lines.flatMap((l) => [l.source, l.target]))]
          onDecided(iris)
        }}
        testId="home-chat"
      />
    </div>
  )
}
