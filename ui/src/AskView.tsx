import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CitationCard } from './CitationCard'
import { ask, isMockMode, type AskResponse, type Citation } from './demoApi'
import { AskIcon, CheckIcon } from './icons'
import { ProvenanceTrace } from './ProvenanceTrace'

const EXAMPLES = [
  'ZT が最も高い熱電材料は？',
  'SnSe を含む組成の試料は？',
  '新しく設計したスキーマにはどんなクラスがある？',
]

// Shared with the workbench (same user-brought key, sessionStorage, never
// persisted to disk). Typed questions need no key; the general/new-schema path
// (LLM writes read-only SPARQL) does.
const API_KEY_STORAGE = 'asterism.apiKey'

/**
 * Ask view: natural-language question -> grounded answer + clickable citation
 * cards + data-quality notes, with the provenance trace as an always-on right
 * panel. Clicking a citation loads its trace beside the answer. The answer is
 * produced by the demo agent (the consumption layer); this view only calls the
 * contract (ask / provenance).
 */
export function AskView({ onShowVocab }: { onShowVocab?: (className: string) => void }) {
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState<AskResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Citation | null>(null)
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem(API_KEY_STORAGE) ?? '')

  function onApiKeyChange(v: string) {
    setApiKey(v)
    if (v) sessionStorage.setItem(API_KEY_STORAGE, v)
    else sessionStorage.removeItem(API_KEY_STORAGE)
  }

  async function run(q: string) {
    const query = q.trim()
    if (!query) return
    setError('')
    setResult(null)
    setSelected(null)
    setLoading(true)
    try {
      setResult(await ask(query, apiKey || undefined))
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
          自然言語で問うと、<strong>取り込み済みのデータ</strong>に基づく
          <strong>根拠つきの回答</strong>と<strong>引用</strong>・<strong>出どころ（来歴）</strong>が返ります。
          {isMockMode && <span className="demo-badge">demo データ (mock)</span>}
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
              placeholder="例: ZT が最も高い熱電材料は？"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                // Don't submit on the Enter that confirms an IME (kanji/かな)
                // conversion — only on a real, non-composing Enter. Without this
                // guard Japanese input is impossible (Enter fires mid-conversion).
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) run(question)
              }}
            />
          </div>
          <button onClick={() => run(question)} disabled={loading || !question.trim()}>
            {loading ? (
              <>
                <span className="spinner" />
                回答中…
              </>
            ) : (
              '質問する'
            )}
          </button>
        </section>

        <div className="ask-examples">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              className="example-chip"
              onClick={() => {
                setQuestion(ex)
                run(ex)
              }}
            >
              {ex}
            </button>
          ))}
        </div>

        <details className="ask-advanced">
          <summary>詳細設定（一般的な質問用の API キー）</summary>
          <p className="ask-advanced-hint">
            定番の型付き質問（ZT・組成など）はキー不要です。
            <strong>新しく設計したスキーマ</strong>への一般的な質問では、ここにキーを入れると
            スキーマを内省して<strong>読み取り専用の SPARQL を生成</strong>し回答します。
            キーはこのタブ内のみ保持し、保存しません（ワークベンチと共通）。
          </p>
          <input
            type="password"
            className="ask-key-input"
            value={apiKey}
            placeholder="sk-ant-…（任意）"
            autoComplete="off"
            onChange={(e) => onApiKeyChange(e.target.value)}
          />
        </details>

        {error && <pre className="error">{error}</pre>}

        {result && (
          <section className="answer-card">
            <div className="answer-head">
              <span className="answer-badge">
                <CheckIcon size={13} /> 根拠つきの回答
              </span>
              <span className="answer-head-note">取り込み済みのデータに基づく</span>
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
                  根拠（引用）
                  <span className="section-h-hint">クリックで出どころを表示</span>
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
                <h3 className="section-h">データ品質メモ</h3>
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
                  使用した SPARQL（{result.sparql.length}）
                  <span className="sparql-disclosure-tag">読み取り専用</span>
                </summary>
                <p className="sparql-disclosure-hint">
                  この回答は、スキーマを内省して生成した次の読み取り専用クエリの結果に基づきます。
                </p>
                {result.sparql.map((q, i) => (
                  <pre key={i} className="sparql-block">
                    {q}
                  </pre>
                ))}
              </details>
            )}
          </section>
        )}
      </div>

      <ProvenanceTrace citation={selected} onShowVocab={onShowVocab} />
    </div>
  )
}
