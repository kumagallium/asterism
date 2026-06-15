import { useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CitationCard } from './CitationCard'
import { ask, isMockMode, type AskResponse, type Citation } from './demoApi'
import { AskIcon, CheckIcon } from './icons'
import { ProvenanceTrace } from './ProvenanceTrace'

// Shared with the workbench (same user-brought key, sessionStorage, never
// persisted to disk). Ask REQUIRES a key: the AI uses it to route the question to
// the verified tools (it only picks the tool + args; the facts/citations come from
// the deterministic tool, not the AI). For key-free deterministic tool execution,
// use the catalog's ツール tab instead.
const API_KEY_STORAGE = 'asterism.apiKey'

/**
 * Ask view: natural-language question -> grounded answer + clickable citation
 * cards + data-quality notes, with the provenance trace as an always-on right
 * panel. Clicking a citation loads its trace beside the answer. The answer is
 * produced by the demo agent (the consumption layer); this view only calls the
 * contract (ask / provenance).
 */
export function AskView({ onShowVocab }: { onShowVocab?: (className: string) => void }) {
  const { t } = useTranslation()
  const examples = [
    t('ask:examples.ztHighest'),
    t('ask:examples.snseSamples'),
    t('ask:examples.schemaClasses'),
  ]
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

  const keyMissing = !apiKey.trim() && !isMockMode

  async function run(q: string) {
    const query = q.trim()
    if (!query) return
    if (keyMissing) {
      setError(t('ask:keyRequiredError'))
      return
    }
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
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                // Don't submit on the Enter that confirms an IME (kanji/かな)
                // conversion — only on a real, non-composing Enter. Without this
                // guard Japanese input is impossible (Enter fires mid-conversion).
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

        <section className={`ask-key-row${keyMissing ? ' ask-key-row--needed' : ''}`}>
          <label className="ask-key-label" htmlFor="ask-key">
            {t('ask:key.label')} <span className="ask-key-required">{t('ask:key.required')}</span>
          </label>
          <input
            id="ask-key"
            type="password"
            className="ask-key-input"
            value={apiKey}
            placeholder={t('ask:key.placeholder')}
            autoComplete="off"
            onChange={(e) => onApiKeyChange(e.target.value)}
          />
          <p className="ask-key-note">
            <Trans i18nKey="ask:key.note" components={[<strong key="0" />, <strong key="1" />]} />
          </p>
        </section>

        <div className="ask-examples">
          {examples.map((ex) => (
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
