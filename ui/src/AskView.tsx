import { useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { takeAskPrefill } from './askPrefill'
import { CitationCard } from './CitationCard'
import { ask, isMockMode, type AskResponse, type Citation } from './demoApi'
import { AskIcon, CheckIcon } from './icons'
import { ProvenanceTrace } from './ProvenanceTrace'
import { useLlmSettings } from './settings/context'
import { LlmGate } from './settings/LlmGate'

// Ask REQUIRES a configured model: the AI uses it to route the question to the
// verified tools (it only picks the tool + args; the facts/citations come from the
// deterministic tool, not the AI). The active model + its key come from Settings
// (shared across the app); for key-free deterministic tool execution, use the
// catalog's ツール tab instead.

/**
 * Ask view: natural-language question -> grounded answer + clickable citation
 * cards + data-quality notes, with the provenance trace as an always-on right
 * panel. Clicking a citation loads its trace beside the answer. The answer is
 * produced by the demo agent (the consumption layer); this view only calls the
 * contract (ask / provenance).
 */
// タブ遷移で AskView が unmount されても直近の回答を失わないための保持領域。
// 回答は LLM 課金と数十秒の待ちを伴う成果物なので、ナビ往復（引用→データセット
// 確認→戻る）で消えると再実行を強いる。リロードで消えるのは許容（モジュール寿命）。
let lastAsk: { question: string; result: AskResponse | null } | null = null

export function AskView({ onShowVocab }: { onShowVocab?: (className: string) => void }) {
  const { t } = useTranslation()
  // 手渡された質問（かんたん S9 チップ → askPrefill.ts）は一度だけ消費し、
  // 前回の回答復元より優先する。この initializer が result のものより先に
  // 走る（宣言順）ことで lastAsk クリアが復元にも効く。
  const [question, setQuestion] = useState(() => {
    const handed = takeAskPrefill()
    if (handed !== null) {
      lastAsk = null // プリフィルは新しい質問の意図 — 前回の回答は出さない
      return handed
    }
    return lastAsk?.question ?? ''
  })
  const [result, setResult] = useState<AskResponse | null>(() => lastAsk?.result ?? null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Citation | null>(null)
  const { isReady, getActiveCredentials } = useLlmSettings()

  const keyMissing = !isReady && !isMockMode

  async function run(q: string) {
    const query = q.trim()
    if (!query || loading) return
    if (keyMissing) {
      setError(t('ask:keyRequiredError'))
      return
    }
    setError('')
    setResult(null)
    setSelected(null)
    setLoading(true)
    try {
      const res = await ask(query, getActiveCredentials())
      setResult(res)
      lastAsk = { question: query, result: res }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="ask-view">
      <div className="ask-main">
        <p className="ask-intro">
          <Trans
            i18nKey="ask:intro"
            components={[
              <strong key="0" />,
              <strong key="1" />,
              <strong key="2" />,
              <strong key="3" />,
              <strong key="4" />,
              <strong key="5" />,
              <strong key="6" />,
            ]}
          />
          {isMockMode && <span className="demo-badge">{t('ask:demoBadge')}</span>}
        </p>

        <section className="ask-bar">
          <div className="ask-input-wrap">
            <span className="ask-icon">
              <AskIcon size={18} />
            </span>
            <input
              type="text"
              className="ask-input"
              value={question}
              placeholder={t('ask:inputPlaceholder')}
              aria-label={t('ask:inputPlaceholder')}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                // Don't submit on the Enter that confirms an IME (kanji/かな)
                // conversion — only on a real, non-composing Enter. Without this
                // guard Japanese input is impossible (Enter fires mid-conversion).
                // run() 側の loading ガードとあわせ、待機中の再送信もしない。
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) run(question)
              }}
            />
          </div>
          <button
            onClick={() => run(question)}
            disabled={loading || !question.trim() || keyMissing}
            title={keyMissing ? t('ask:submitTitle') : undefined}
          >
            {loading ? (
              <>
                <span className="spinner" />
                {t('ask:answering')}
              </>
            ) : (
              t('ask:submit')
            )}
          </button>
        </section>

        {!isMockMode && <LlmGate />}

        {/* 回答は数秒〜数十秒後に非同期挿入されるので、支援技術へ到着を通知する */}
        <div aria-live="polite">
        {error && <pre className="error">{error}</pre>}

        {result && (
          <section className="answer-card">
            <div className="answer-head">
              {(result.verifiedTools?.length ?? 0) > 0 ? (
                <span className="answer-badge answer-badge-verified">
                  <CheckIcon size={13} />{' '}
                  {t('ask:badge.verifiedTools', {
                    tools: result.verifiedTools!.map((vt) => vt.title).join(' · '),
                  })}
                </span>
              ) : result.unverifiedSparql ? (
                <span className="answer-badge answer-badge-unverified">{t('ask:badge.unverifiedSparql')}</span>
              ) : (
                <span className="answer-badge">
                  <CheckIcon size={13} /> {t('ask:badge.grounded')}
                </span>
              )}
              {result.unverifiedSparql && (result.verifiedTools?.length ?? 0) > 0 && (
                <span className="answer-badge answer-badge-unverified">{t('ask:badge.plusUnverifiedSparql')}</span>
              )}
              <span className="answer-head-note">
                {(result.verifiedTools?.length ?? 0) > 0
                  ? t('ask:headNote.verified')
                  : result.unverifiedSparql
                    ? t('ask:headNote.unverified')
                    : t('ask:headNote.grounded')}
              </span>
            </div>
            {/* The LLM escape can return Markdown (GFM tables / lists); typed
                answers are plain sentences. Render as Markdown so a table is a
                table, not raw "| … |" pipes. */}
            <div className="answer-text answer-md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.answer}</ReactMarkdown>
            </div>

            {result.citations.length > 0 && (
              <div className="citations">
                <h3 className="section-h">
                  {t('ask:citations.heading')}
                  <span className="section-h-hint">{t('ask:citations.hint')}</span>
                </h3>
                <div className="citation-list">
                  {result.citations.map((c) => (
                    <CitationCard
                      key={c.iri}
                      citation={c}
                      selected={selected?.iri === c.iri}
                      onSelect={setSelected}
                      onShowVocab={onShowVocab}
                    />
                  ))}
                </div>
              </div>
            )}

            {result.notes.length > 0 && (
              <div className="notes">
                <h3 className="section-h">{t('ask:notes.heading')}</h3>
                <ul className="notes-list">
                  {result.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </div>
            )}

            {result.sparql.length > 0 && (
              <details className="sparql-disclosure">
                <summary>
                  {t('ask:sparql.summary', { n: result.sparql.length })}
                  <span className="sparql-disclosure-tag">{t('ask:sparql.readonlyTag')}</span>
                </summary>
                <p className="sparql-disclosure-hint">
                  {t('ask:sparql.hint')}
                </p>
                {result.sparql.map((q, i) => (
                  <SparqlBlock key={i} query={q} />
                ))}
              </details>
            )}
          </section>
        )}
        </div>
      </div>

      <ProvenanceTrace citation={selected} onShowVocab={onShowVocab} />
    </div>
  )
}

// 開示された SPARQL は「読み取り専用の追試」への入口 — コピーして SPARQL 画面で
// そのまま再実行できるよう、ブロックごとにコピーボタンを付ける。
function SparqlBlock({ query }: { query: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard
      ?.writeText(query)
      .then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1600)
      })
      .catch(() => {})
  }
  return (
    <div className="sparql-block-wrap">
      <pre className="sparql-block">{query}</pre>
      <button type="button" className="btn btn--ghost btn--sm sparql-block-copy" onClick={copy}>
        {copied ? t('ask:sparql.copied') : t('ask:sparql.copy')}
      </button>
    </div>
  )
}
