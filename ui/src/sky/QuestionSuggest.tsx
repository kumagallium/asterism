import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type QuestionSuggestion, suggestQuestions } from '../consult/graphChatApi'
import { useLlmSettings } from '../settings/context'
import { ThemesApiError } from '../themes/themesApi'
import type { SuggestItem } from '../workspace/workspaceState'
import './questionSuggest.css'

// 「問いを提案してもらう」（PR 10a・design.md §18）。人が押したときだけ AI を呼び、返った問いをカードに出す。
// 1 行 = 問いの文 + 薄く実行文（機械が組んだもの）+「この問いで始める」。押すと `#/home?write=1&q=<問い>` で
// 下書きの流れへ。押さなければ何も残らない（提案の文・却下は保存しない）。
//   ・ホームの会話: 星を選んだときの札のボタン → 会話のカード（`SuggestCard`・ws.items の suggest）
//   ・⑦の完了: ボタンとカードを完了カードの中に（`QuestionSuggestBlock`・自分の状態）

function Rows({ rows, onStart }: { rows: QuestionSuggestion[]; onStart: (q: string) => void }) {
  const { t } = useTranslation('sky')
  if (rows.length === 0) return <p className="sky-hint">{t('suggestQ.none')}</p>
  return (
    <ul className="qs-list" data-testid="suggest-rows">
      {rows.map((r) => (
        <li key={r.question} className="qs-row" data-testid="suggest-row">
          <div className="qs-text">
            <span className="qs-q">{r.question}</span>
            {r.shape && <span className="qs-shape">{r.shape}</span>}
          </div>
          <button type="button" className="btn btn--sm" onClick={() => onStart(r.question)} data-testid="suggest-start">
            {t('suggestQ.start')}
          </button>
        </li>
      ))}
    </ul>
  )
}

/** 会話の中のカード（ホーム）。`item` は ws.items の suggest。 */
export function SuggestCard({
  item,
  onStart,
  onClose,
}: {
  item: SuggestItem
  onStart: (q: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation('sky')
  return (
    <section className="sky-card sky-card--session qs-card" data-testid="suggest-card" aria-live="polite">
      <h3 className="qs-title">
        <span>{t('suggestQ.title')}</span>
        <span className="qs-from">{item.label}</span>
      </h3>
      {item.status === 'running' && (
        <p className="sky-hint" role="status">
          <span className="consult-spinner" aria-hidden="true" /> {t('suggestQ.running')}
        </p>
      )}
      {item.status === 'failed' && (
        <p className="promote-err" role="alert">
          {t('suggestQ.failed', { detail: item.detail })}
        </p>
      )}
      {item.status === 'done' && <Rows rows={item.rows} onStart={onStart} />}
      {item.status !== 'running' && (
        <div className="sky-about-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClose} data-testid="suggest-close">
            {t('suggestQ.close')}
          </button>
        </div>
      )}
    </section>
  )
}

/** 星の札・⑦の完了カードのボタン。 */
export function SuggestButton({ onAsk, disabled }: { onAsk: () => void; disabled?: boolean }) {
  const { t } = useTranslation('sky')
  return (
    <button type="button" className="btn btn--sm" onClick={onAsk} disabled={disabled} data-testid="suggest-ask">
      {t('suggestQ.ask')}
    </button>
  )
}

/**
 * ⑦の完了カードの中のブロック（AI 設定ずみのときだけ出す）。ボタン → 同じ場所にカード。
 * `onStart` は「この問いで始める」（⑦を片づけてからホームの問いを書く道へ行く）。
 */
export function QuestionSuggestBlock({ datasetId, onStart }: { datasetId: string; onStart: (q: string) => void }) {
  const { t, i18n } = useTranslation('sky')
  const { isReady, getActiveCredentials } = useLlmSettings()
  const [state, setState] = useState<{ status: 'idle' | 'running' | 'done' | 'failed'; rows: QuestionSuggestion[]; detail: string }>({
    status: 'idle',
    rows: [],
    detail: '',
  })
  if (!isReady) return null
  const ask = () => {
    setState({ status: 'running', rows: [], detail: '' })
    suggestQuestions(datasetId, i18n.language.startsWith('en') ? 'en' : 'ja', getActiveCredentials())
      .then((rows) => setState({ status: 'done', rows, detail: '' }))
      .catch((e: unknown) =>
        setState({
          status: 'failed',
          rows: [],
          detail: e instanceof ThemesApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e),
        }),
      )
  }
  return (
    <div className="kz-next-card qs-block" data-testid="suggest-block">
      {state.status === 'idle' || state.status === 'running' || state.status === 'failed' ? (
        <div className="kz-actions">
          <SuggestButton onAsk={ask} disabled={state.status === 'running'} />
          {state.status === 'running' && (
            <span className="sky-hint" role="status">
              <span className="consult-spinner" aria-hidden="true" /> {t('suggestQ.running')}
            </span>
          )}
        </div>
      ) : null}
      {state.status === 'failed' && (
        <p className="promote-err" role="alert">
          {t('suggestQ.failed', { detail: state.detail })}
        </p>
      )}
      {state.status === 'done' && (
        <>
          <h4 className="kz-next-title">{t('suggestQ.title')}</h4>
          <Rows rows={state.rows} onStart={onStart} />
        </>
      )}
    </div>
  )
}
