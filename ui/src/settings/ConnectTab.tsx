import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getAppDataInfo } from '../appdata'
import { copyText } from '../clipboard'
import { CLIENTS, recipeFor, type ClientId } from './connectRecipes'

// 接続タブ — このアプリのデータを「ほかの AI」から直接聞けるようにするための
// 案内（ADR mcp-endpoint-on-the-app.md §4）。
//
// URL は 1 本でも、貼り先はクライアントごとに違う。ここが変換を引き受けないと、
// 「どこに何を書けばいいか」の翻訳を人間がやることになり、それは結局この
// source tree を読める人にしかできない。だからクライアントを選ばせて、その
// クライアントに貼る文字列をそのまま出す。
//
// URL 自体はサーバの申告（/api/appdata/info の mcp_url）を使う。8765 は
// 「希望のポート」でしかなく、他プログラムに取られていれば別のポートで上がる。
// UI に定数を書くと、その瞬間に「コピーしたのに繋がらない」が生まれる。

/** コピーボタン + 押した直後の手応え。 */
function CopyButton({ text, label }: { text: string; label: string }) {
  const { t } = useTranslation('settings')
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="btn btn--ghost btn--sm connect-copy"
      aria-label={label}
      onClick={() => {
        void copyText(text).then((ok) => {
          if (!ok) return
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1600)
        })
      }}
    >
      {copied ? t('connect.copied') : t('connect.copy')}
    </button>
  )
}

export function ConnectTab() {
  const { t } = useTranslation('settings')
  const url = getAppDataInfo()?.mcpUrl ?? null
  const [client, setClient] = useState<ClientId>('claude-code')

  if (!url) {
    return (
      <div className="connect-tab">
        <section className="serverkeys storage-section">
          <h4 className="serverkeys-title">{t('connect.title')}</h4>
          <p className="field-help">{t('connect.unavailable')}</p>
        </section>
      </div>
    )
  }

  const recipe = recipeFor(client, url)
  const steps = t(`connect.steps.${client}`, { returnObjects: true }) as string[]

  return (
    <div className="connect-tab">
      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('connect.title')}</h4>
        <p className="field-help">{t('connect.intro')}</p>

        <div className="serverkey-row connect-url">
          <div className="connect-url-info">
            <span className="about-label">{t('connect.urlLabel')}</span>
            <code className="about-value connect-url-value">{url}</code>
          </div>
          <CopyButton text={url} label={t('connect.copyUrl')} />
        </div>

        <div className="connect-clients" role="tablist" aria-label={t('connect.clientLabel')}>
          {CLIENTS.map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={client === id}
              className={`connect-client${client === id ? ' active' : ''}`}
              onClick={() => setClient(id)}
            >
              {t(`connect.clients.${id}`)}
            </button>
          ))}
        </div>

        <ol className="connect-steps">
          {steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>

        {recipe.kind !== 'url' && (
          <div className="connect-snippet">
            <pre>
              <code>{recipe.text}</code>
            </pre>
            <CopyButton text={recipe.text} label={t('connect.copySnippet')} />
          </div>
        )}

        <p className="field-help connect-caveat">{t('connect.caveat')}</p>
      </section>
    </div>
  )
}
