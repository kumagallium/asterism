import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { QuestionDraft, QuestionOp, QuestionSelection, TrialQueries } from '../api'
import { runTrialQuestion } from '../api'
import type { DatasetRules } from '../galleryApi'
import { VocabApiError, copyCqFromDataset, getUpper, listTerms } from '../vocabApi'
import { vocabQuestionHash } from '../vocabQuestion'
import {
  QUESTION_OPS,
  canSaveQuestion,
  hasAnswer,
  newQuestion,
  questionChoices,
  selectionKey,
  selectionOf,
  selectionOfQuestion,
  upperTarget,
} from './questions'

// ⑥「ためす」の「自分の問い」(ADR upper-structure-shared-terms §2.3・§2.5.2)。
// 種類／項目／集計（件数・範囲・上位の値）を**選んで**、題を書く。SPARQL は api が決定論の
// テンプレートで組む（LLM なし・自由記述なし）。答えは下書きの graph で見せ、公開後は
// 公開済みのデータで答える。保存は下書き（questions.json）— 宣言ツールにするのは公開のとき。

/** 答え 1 件（カードの本文）。ウィザードの `buildTrialQAs` と同じ文に整える。 */
export interface QuestionAnswer {
  a: string
  sparql?: string
}

type RunState =
  | { state: 'loading' }
  | { state: 'ok'; res: TrialQueries; key: string }
  | { state: 'unavailable'; key: string }
  | { state: 'error'; message: string; key: string }

type MapNote =
  | { kind: 'done'; term: string; existed: boolean }
  | { kind: 'guide'; title: string }
  | { kind: 'error'; message: string }

export function QuestionBuilder({
  datasetId,
  rules,
  questions,
  onChange,
  answerOf,
  kindLabel,
}: {
  datasetId: string
  rules: DatasetRules | null
  /** 保存済みの問い（questions.json）。 */
  questions: QuestionDraft[]
  /** 足す・消すたびに全体を渡す。保存（PUT）は呼び出し側。失敗は投げる。 */
  onChange: (next: QuestionDraft[]) => Promise<void>
  /** 走らせた結果 → カードに出す答えの文。出せなければ null。 */
  answerOf: (res: TrialQueries) => QuestionAnswer | null
  /** 種類の IRI → 画面に出す名前（ウィザードの `classLabel`）。 */
  kindLabel: (iri: string) => string
}) {
  const { t } = useTranslation()
  const choices = useMemo(() => questionChoices(rules), [rules])

  const [op, setOp] = useState<QuestionOp>('count')
  const [kindIri, setKindIri] = useState('')
  const [propertyIri, setPropertyIri] = useState('')
  const [title, setTitle] = useState('')
  const [preview, setPreview] = useState<RunState | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  // 保存済みの問いの答え（id → 結果）。選択が変わったら引き直す（key が合わない）。
  const [answers, setAnswers] = useState<Record<string, RunState>>({})
  const [mapNotes, setMapNotes] = useState<Record<string, MapNote>>({})
  const [mapBusy, setMapBusy] = useState<string | null>(null)
  const requested = useRef<Set<string>>(new Set())
  // いまの選択の鍵。「ためしに聞く」の応答が届いたとき、選択がもう変わっていたら捨てる
  // （前の選択の答えを、いまの選択の答えとして見せない）。
  const selKeyRef = useRef('')

  const sel: QuestionSelection = selectionOf(op, kindIri, propertyIri)
  const ready = canSaveQuestion(title, sel)
  const selReady = canSaveQuestion('x', sel)
  selKeyRef.current = selectionKey(sel)
  const needsProperty = op !== 'count'
  const propertyOptions = choices.properties.filter(
    (p) => !kindIri || p.kinds.length === 0 || p.kinds.includes(kindIri),
  )

  // 選択を変えたら、前の試し結果は古い。
  useEffect(() => {
    setPreview(null)
  }, [op, kindIri, propertyIri])

  // 保存済みの問いの答えを、まだ引いていないものだけ引く。
  useEffect(() => {
    for (const q of questions) {
      const key = selectionKey(selectionOfQuestion(q))
      const token = `${q.id}\u0000${key}`
      if (requested.current.has(token)) continue
      requested.current.add(token)
      setAnswers((cur) => ({ ...cur, [q.id]: { state: 'loading' } }))
      void runTrialQuestion(datasetId, selectionOfQuestion(q))
        .then((res) => {
          setAnswers((cur) => ({
            ...cur,
            [q.id]: res.available ? { state: 'ok', res, key } : { state: 'unavailable', key },
          }))
        })
        .catch((e: unknown) => {
          setAnswers((cur) => ({
            ...cur,
            [q.id]: { state: 'error', message: e instanceof Error ? e.message : String(e), key },
          }))
        })
    }
  }, [questions, datasetId])

  async function tryIt() {
    if (!selReady) return
    setPreview({ state: 'loading' })
    const key = selectionKey(sel)
    try {
      const res = await runTrialQuestion(datasetId, sel)
      if (selKeyRef.current !== key) return
      setPreview(res.available ? { state: 'ok', res, key } : { state: 'unavailable', key })
    } catch (e) {
      if (selKeyRef.current !== key) return
      setPreview({ state: 'error', message: e instanceof Error ? e.message : String(e), key })
    }
  }

  async function add() {
    if (!ready || busy) return
    setBusy(true)
    setErr('')
    try {
      await onChange([...questions, newQuestion(title, sel, questions)])
      setTitle('')
      setPreview(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    if (busy) return
    setBusy(true)
    setErr('')
    try {
      await onChange(questions.filter((q) => q.id !== id))
      // 同じ id は足し直しで再び使われる。引いた記録を残すと、答えが「聞いています」のまま止まる。
      for (const token of [...requested.current]) {
        if (token.startsWith(`${id}\u0000`)) requested.current.delete(token)
      }
      setAnswers((cur) => Object.fromEntries(Object.entries(cur).filter(([k]) => k !== id)))
      setMapNotes((cur) => Object.fromEntries(Object.entries(cur).filter(([k]) => k !== id)))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  /** 「ことばへ写す」: 上位の対応表で写す先の語を見つけ、あれば写す。無ければ鋳造フォームへ。 */
  async function mapToVocab(q: QuestionDraft) {
    if (mapBusy) return
    setMapBusy(q.id)
    const guide: MapNote = { kind: 'guide', title: q.title }
    try {
      const [upper, terms] = await Promise.all([getUpper(), listTerms()])
      const target = upperTarget(q, upper)
      const term = target ? terms.find((x) => x.iri === target) : undefined
      if (!term) {
        setMapNotes((cur) => ({ ...cur, [q.id]: guide }))
        return
      }
      const copied = await copyCqFromDataset(term.slug, datasetId, q.id)
      setMapNotes((cur) => ({
        ...cur,
        [q.id]: { kind: 'done', term: term.label, existed: copied.existed },
      }))
    } catch (e) {
      // 409 = 上位が写す先の語ではない（語が無い・線が無い）— 行き止まりにせず案内する。
      if (e instanceof VocabApiError && e.status === 409) {
        setMapNotes((cur) => ({ ...cur, [q.id]: guide }))
      } else {
        setMapNotes((cur) => ({
          ...cur,
          [q.id]: { kind: 'error', message: e instanceof Error ? e.message : String(e) },
        }))
      }
    } finally {
      setMapBusy(null)
    }
  }

  function answerCard(qTitle: string, run: RunState | null | undefined) {
    if (!run || run.state === 'loading') {
      return (
        <p className="kz-note" role="status">
          <span className="spinner" />
          {t('kantan:s7.own.running')}
        </p>
      )
    }
    if (run.state === 'error') return <p className="kz-note">{t('kantan:s7.own.runFailed')}</p>
    if (run.state === 'unavailable') return <p className="kz-note">{t('kantan:s7.own.unavailable')}</p>
    const answer = hasAnswer(run.res) ? answerOf(run.res) : null
    return (
      <div className="kz-qa">
        <div className="kz-qa-q">{qTitle}</div>
        <div className="kz-qa-a">{answer ? answer.a : t('kantan:s7.own.noAnswer')}</div>
        {answer?.sparql && (
          <details className="kz-stop-detail">
            <summary>{t('kantan:s7.techSummary')}</summary>
            <pre className="sparql-block">{answer.sparql}</pre>
          </details>
        )}
      </div>
    )
  }

  const noChoices = choices.kinds.length === 0 && choices.properties.length === 0

  return (
    <section className="kz-own-questions" data-testid="own-questions">
      <h4 className="kz-q-text">{t('kantan:s7.own.title')}</h4>
      <p className="kz-note">{t('kantan:s7.own.lead')}</p>
      <p className="kz-note">{t('kantan:s7.own.afterPublish')}</p>

      {questions.map((q) => {
        const note = mapNotes[q.id]
        return (
          <div key={q.id} className="kz-own-question" data-testid="own-question">
            {answerCard(q.title, answers[q.id])}
            {q.lint_error && (
              <p className="kz-note kz-pub-err" role="alert">
                {t('kantan:s7.own.lintError')}
              </p>
            )}
            <div className="kz-actions">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                // 写したあとは押せない（同じ問いが重ねて足されない）。
                disabled={mapBusy === q.id || note?.kind === 'done'}
                onClick={() => void mapToVocab(q)}
                title={t('kantan:s7.own.mapHint')}
              >
                {t('kantan:s7.own.map')}
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={busy}
                onClick={() => void remove(q.id)}
              >
                {t('kantan:s7.own.remove')}
              </button>
            </div>
            {note?.kind === 'done' && (
              <p className="kz-note" role="status">
                {t(note.existed ? 'kantan:s7.own.mapExisted' : 'kantan:s7.own.mapDone', {
                  term: note.term,
                })}
              </p>
            )}
            {note?.kind === 'guide' && (
              <p className="kz-note" role="status">
                {t('kantan:s7.own.mapGuide')}{' '}
                <a href={vocabQuestionHash(note.title)}>{t('kantan:s7.own.mapGuideLink')}</a>
              </p>
            )}
            {note?.kind === 'error' && <p className="kz-note kz-pub-err">{note.message}</p>}
          </div>
        )
      })}

      {noChoices ? (
        <p className="kz-note">{t('kantan:s7.own.noChoices')}</p>
      ) : (
        <div className="kz-own-form">
          <div className="kz-q">
            <label className="kz-q-text" htmlFor="kz-own-op">
              {t('kantan:s7.own.opLabel')}
            </label>
            <select
              id="kz-own-op"
              className="skeleton-gate-input"
              value={op}
              onChange={(e) => setOp(e.target.value as QuestionOp)}
            >
              {QUESTION_OPS.map((o) => (
                <option key={o} value={o}>
                  {t(`kantan:s7.own.op.${o}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="kz-q">
            <label className="kz-q-text" htmlFor="kz-own-kind">
              {t(needsProperty ? 'kantan:s7.own.kindOptional' : 'kantan:s7.own.kind')}
            </label>
            <select
              id="kz-own-kind"
              className="skeleton-gate-input"
              value={kindIri}
              onChange={(e) => {
                setKindIri(e.target.value)
                setPropertyIri('')
              }}
            >
              <option value="">{t('kantan:s7.own.pick')}</option>
              {choices.kinds.map((k) => (
                <option key={k.iri} value={k.iri}>
                  {kindLabel(k.iri)}
                </option>
              ))}
            </select>
          </div>
          {needsProperty && (
            <div className="kz-q">
              <label className="kz-q-text" htmlFor="kz-own-prop">
                {t('kantan:s7.own.property')}
              </label>
              <select
                id="kz-own-prop"
                className="skeleton-gate-input"
                value={propertyIri}
                onChange={(e) => setPropertyIri(e.target.value)}
              >
                <option value="">{t('kantan:s7.own.pick')}</option>
                {propertyOptions.map((p) => (
                  <option key={p.iri} value={p.iri}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="kz-q">
            <label className="kz-q-text" htmlFor="kz-own-title">
              {t('kantan:s7.own.titleLabel')}
            </label>
            <input
              id="kz-own-title"
              type="text"
              className="skeleton-gate-input"
              value={title}
              placeholder={t('kantan:s7.own.titlePlaceholder')}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="kz-actions">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={!selReady || preview?.state === 'loading'}
              onClick={() => void tryIt()}
            >
              {t('kantan:s7.own.try')}
            </button>
            <button type="button" disabled={!ready || busy} onClick={() => void add()}>
              {t('kantan:s7.own.add')}
            </button>
          </div>
          {preview && answerCard(title.trim() || t('kantan:s7.own.untitled'), preview)}
          {err && (
            <p className="kz-note kz-pub-err" role="alert">
              {err}
            </p>
          )}
        </div>
      )}
    </section>
  )
}

