import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from '../settings/context'
import {
  REL_KINDS,
  type RelKind,
  ANSWER_WAYS,
  type AnswerRow,
  type AnswerWay,
  type ConceptRow,
  type TermKind,
  type ThemeDraft,
  answerComplete,
  answerHost,
  answerState,
  conceptMints,
  conceptName,
  conceptsFor,
  defaultLabel,
  hasConceptNamed,
  idList,
  knowledgeEstimate,
  setAnswerTarget,
  setWay,
  wayNeeds,
  wayOf,
} from '../themes/themeDraft'
import { relId } from '../workspace/workspaceState'
import { nameOfKey, relationSentenceOf } from '../workspace/workspaceText'
import type { HomeWorkspace } from './useHomeWorkspace'

// 会話の中の「下書きカードの中の 1 行」（問いごとの答え方・欲しいデータ）・「N 件を置く」の欄・文の一覧と手で足す・
// 問いの書き込み欄（AI 未設定）。②のモーダル（ThemeWizard／ThemeWorkspace）にあったものを、部品としてそのまま移した。

type TFn = (k: string, o?: Record<string, unknown>) => string

/** 答え方の対象を選ぶ（概念の一覧から）。 */
function ConceptSelect({
  label,
  value,
  options,
  lang,
  optional,
  onChange,
}: {
  label: string
  value: string
  options: ConceptRow[]
  lang: string
  optional?: boolean
  onChange: (key: string) => void
}) {
  const { t } = useTranslation()
  return (
    <label>
      {label}{' '}
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        <option value="">{optional ? t('themes:answer.none') : t('themes:pick')}</option>
        {options.map((c) => (
          <option key={c.key} value={c.key}>
            {conceptName(c, lang)}
          </option>
        ))}
      </select>
    </label>
  )
}

/** 問いごとの 1 行: 「答え方: 熱伝導率 を下げるもの（知識）▾」「知識 N・データ N」「欲しいデータ」。 */
export function AnswerLines({ ws, draft, lang }: { ws: HomeWorkspace; draft: ThemeDraft; lang: string }) {
  const { t } = useTranslation()
  const tf = t as unknown as TFn
  const [openAnswer, setOpenAnswer] = useState<number | null>(null)
  const nameOf = (key: string) => nameOfKey(draft, key, lang, tf)
  const wayText = (a: AnswerRow): string => {
    const way = wayOf(a)
    if (a.mode === 'memo' || !answerComplete(a)) return t('themes:answer.way.none')
    const target = nameOf(a.target)
    const kind = nameOf(a.kind)
    const property = nameOf(a.property)
    if (way === 'range' || way === 'top') return t(`themes:answer.way.${way}${a.kind ? '' : 'Only'}`, { kind, property })
    return t(`themes:answer.way.${way}`, { target, kind, property })
  }
  if (draft.answers.length === 0) return null
  return (
    <section className="thm-aqs sky-answers" aria-label={t('themes:review.questionsAria')} data-testid="theme-answers">
      {draft.answers.map((a, i) => {
        const state = answerState(a)
        const way = wayOf(a)
        const hostRow = draft.concepts.find((c) => c.key === answerHost(a))
        const dataN = hostRow && !conceptMints(hostRow) ? (ws.answering[hostRow.slug] ?? 0) : 0
        const open = openAnswer === i
        const needs = wayNeeds(way)
        return (
          <div className="thm-aq" key={a.question} data-testid="theme-answer">
            <div className="thm-aq-head">
              <span className="thm-aq-text">{a.question}</span>
              {state === 'memo' ? (
                <span className="pill wait">{t('themes:review.still')}</span>
              ) : (
                <span className="pill ok">{t('themes:review.counts', { k: state === 'knowledge' ? knowledgeEstimate(draft, a) : 0, d: dataN })}</span>
              )}
              {a.by === 'ai' && state !== 'memo' && <span className="thm-ai">{t('themes:aiMark')}</span>}
            </div>
            <div className="thm-aq-way">
              <span className="vocab-field-hint">{t('themes:answer.label')}</span>{' '}
              <button type="button" className="link-btn" aria-expanded={open} onClick={() => setOpenAnswer(open ? null : i)}>
                {wayText(a)} ▾
              </button>
            </div>
            {a.wanted_data && <div className="thm-aq-want">{t('themes:review.wanted', { text: a.wanted_data })}</div>}
            {open && (
              <div className="thm-aq-edit">
                <label>
                  {t('themes:answer.pickWay')}{' '}
                  <select value={way} onChange={(e) => ws.updateAnswer(i, (x) => setWay(x, e.target.value as AnswerWay))}>
                    {ANSWER_WAYS.map((w) => (
                      <option key={w} value={w}>
                        {t(`themes:answer.option.${w}`)}
                      </option>
                    ))}
                  </select>
                </label>
                {needs.target && (
                  <ConceptSelect
                    label={t('themes:answer.pickTarget')}
                    value={a.target}
                    options={conceptsFor(draft, way, 'target')}
                    lang={lang}
                    onChange={(v) => ws.updateAnswer(i, (x) => setAnswerTarget(x, { target: v }))}
                  />
                )}
                {needs.kind && (
                  <ConceptSelect
                    label={t('themes:answer.pickKind')}
                    value={a.kind}
                    options={conceptsFor(draft, way, 'kind')}
                    lang={lang}
                    optional={needs.kindOptional}
                    onChange={(v) => ws.updateAnswer(i, (x) => setAnswerTarget(x, { kind: v }))}
                  />
                )}
                {needs.property && (
                  <ConceptSelect
                    label={t('themes:answer.pickProperty')}
                    value={a.property}
                    options={conceptsFor(draft, way, 'property')}
                    lang={lang}
                    onChange={(v) => ws.updateAnswer(i, (x) => setAnswerTarget(x, { property: v }))}
                  />
                )}
                {draft.concepts.length === 0 && way !== 'memo' && <span className="vocab-field-hint">{t('themes:answer.noConcepts')}</span>}
              </div>
            )}
          </div>
        )
      })}
    </section>
  )
}

/** 会話の下に固定する「N 件を置く」。置く内容（概念・関係・問い・ID・テーマ名）は畳んである。 */
export function PlaceBar({ ws, draft, lang }: { ws: HomeWorkspace; draft: ThemeDraft; lang: string }) {
  const { t } = useTranslation()
  const tf = t as unknown as TFn
  const real = ws.real ?? draft
  const ids = idList(real)
  const problemCount = Object.keys(ws.problems).length
  const label = draft.label.trim() || defaultLabel(draft.questions)
  const extend = !!draft.extend
  return (
    <div className="sky-place" data-testid="place-bar">
      {(ws.saveErr || ws.placeErr || ws.actionErr) && (
        <p className="promote-err" role="alert">
          {ws.placeErr || ws.actionErr || t('themes:wizard.saveErr', { detail: ws.saveErr })}
        </p>
      )}
      {ws.blocker && <p className="vocab-field-hint">{t(`themes:place.blocker.${ws.blocker}`)}</p>}
      {problemCount > 0 && <IdRows ws={ws} ids={ids.filter((l) => ws.problems[l.key])} />}
      {ws.thin > 0 && <p className="vocab-field-hint">{t('themes:review.thinNote', { n: ws.thin })}</p>}
      <details className="thm-chat-more" data-testid="place-details">
        <summary>{t(extend ? 'themes:place.leadExtend' : 'themes:place.lead', { label, concepts: ws.counts.concepts, relations: ws.counts.relations, questions: draft.questions.length })}</summary>
        {!extend && (
          <label className="vocab-field">
            <span>{t('themes:write.themeName')}</span>
            <input type="text" value={draft.label} placeholder={defaultLabel(draft.questions)} onChange={(e) => ws.setLabel(e.target.value)} />
          </label>
        )}
        {ids.length > 0 && (
          <>
            <div className="thm-sub-h">{t('themes:place.concepts')}</div>
            <IdRows ws={ws} ids={ids} />
          </>
        )}
        {real.relations.length > 0 && (
          <>
            <div className="thm-sub-h">{t('themes:place.relations')}</div>
            {real.relations.map((r, i) => (
              <div key={`${i}-${relId(r)}`}>
                ・{relationSentenceOf(real, r, lang, tf)}
                {r.reason && <span className="thm-reason"> — {r.reason}</span>}
              </div>
            ))}
          </>
        )}
      </details>
      <button
        type="button"
        className="btn btn--activity sky-place-go"
        disabled={ws.placing || ws.busy || problemCount > 0 || draft.questions.length === 0}
        data-testid="place-go"
        title={ws.unlinked.size > 0 ? t('themes:review.unlinkedNote', { n: ws.unlinked.size }) : undefined}
        onClick={() => void ws.place()}
      >
        {ws.placing ? t('themes:place.placing') : ws.counts.total > 0 ? t('themes:review.confirm', { n: ws.counts.total }) : t('themes:review.confirmNone')}
      </button>
    </div>
  )
}

/** 文の一覧（押すと線を選ぶ・✕で外す）と「手で足す」。会話の下の畳んだ道具。 */
export function SessionTools({
  ws,
  draft,
  lang,
  selectedKey,
  selectedEdge,
  onSelectEdge,
}: {
  ws: HomeWorkspace
  draft: ThemeDraft
  lang: string
  selectedKey: string | null
  selectedEdge: string | null
  onSelectEdge: (id: string | null) => void
}) {
  const { t } = useTranslation()
  const tf = t as unknown as TFn
  const [hand, setHand] = useState({ name: '', kind: 'class' as TermKind, en: '', aliases: '' })
  const add = async () => {
    if (await ws.addHand(hand)) setHand((h) => ({ ...h, name: '', en: '', aliases: '' }))
  }
  return (
    <>
      <details className="thm-chat-more">
        <summary>{t('themes:review.sentences', { n: draft.relations.length })}</summary>
        {draft.relations.length === 0 ? (
          <p className="ds-empty-note">{t('themes:review.noSentences')}</p>
        ) : (
          <ul className="thm-sentences" data-testid="theme-sentences">
            {draft.relations.map((r, i) => {
              const lit = (!!selectedKey && (r.from === selectedKey || r.to === selectedKey)) || selectedEdge === relId(r)
              const sentence = relationSentenceOf(draft, r, lang, tf)
              return (
                <li key={`${i}-${relId(r)}`} className={`thm-sentence${lit ? ' is-lit' : ''}${r.problem ? ' is-bad' : ''}`}>
                  <button type="button" className="thm-sentence-text" onClick={() => onSelectEdge(selectedEdge === relId(r) ? null : relId(r))}>
                    {sentence}
                  </button>
                  <button
                    type="button"
                    className="thm-x"
                    aria-label={t('themes:review.dismissAria', { sentence })}
                    title={t('themes:review.dismiss')}
                    onClick={() => void ws.removeRelationRow(i)}
                  >
                    ✕
                  </button>
                  {r.problem && <span className="promote-err thm-sentence-note">{t(`themes:review.problem.${r.problem}`)}</span>}
                  {r.reason && <span className="thm-reason">{r.reason}</span>}
                </li>
              )
            })}
          </ul>
        )}
      </details>
      <details className="thm-chat-more" data-testid="hand-add">
        <summary>{t('themes:review.add')}</summary>
        <div className="thm-hand-row">
          <input
            type="text"
            aria-label={t('themes:review.addName')}
            placeholder={t('themes:review.addNamePlaceholder')}
            value={hand.name}
            data-testid="hand-name"
            onChange={(e) => setHand((h) => ({ ...h, name: e.target.value }))}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void add()
              }
            }}
          />
          <select aria-label={t('themes:review.addKind')} value={hand.kind} onChange={(e) => setHand((h) => ({ ...h, kind: e.target.value as TermKind }))}>
            <option value="class">{t('themes:kind.class')}</option>
            <option value="property">{t('themes:kind.property')}</option>
          </select>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={ws.busy || !hand.name.trim() || hasConceptNamed(draft.concepts, hand.name)}
            data-testid="hand-go"
            onClick={() => void add()}
          >
            {t('themes:review.addGo')}
          </button>
        </div>
        <div className="thm-hand-row">
          <input type="text" aria-label={t('themes:review.addEn')} placeholder={t('themes:review.addEn')} value={hand.en} onChange={(e) => setHand((h) => ({ ...h, en: e.target.value }))} />
          <input
            type="text"
            aria-label={t('themes:review.addAliases')}
            placeholder={t('themes:review.addAliasesPlaceholder')}
            value={hand.aliases}
            onChange={(e) => setHand((h) => ({ ...h, aliases: e.target.value }))}
          />
        </div>
        <HandRelation ws={ws} draft={draft} lang={lang} />
      </details>
    </>
  )
}

/** AI の下書きが作れなかった／AI が未設定のときの札（もう一度読ませる・設定を開く）。 */
export function ReadStop({ ws }: { ws: HomeWorkspace }) {
  const { t } = useTranslation()
  const { openSettings } = useLlmSettings()
  if (ws.read.status === 'failed') {
    return (
      <section className="sky-card sky-card--session" role="alert" data-testid="theme-ai-failed">
        <h3>{t('themes:read.failed.title')}</h3>
        <p className="sky-hint">{t('themes:read.failed.body')}</p>
        <details className="kz-stop-detail">
          <summary>{t('themes:read.failed.detail')}</summary>
          <pre className="error">{ws.read.detail}</pre>
        </details>
        <div className="sky-about-actions">
          <button type="button" className="btn btn--accent btn--sm" onClick={ws.retryRead}>
            {t('themes:read.retry')}
          </button>
        </div>
      </section>
    )
  }
  if (ws.read.status === 'unconfigured') {
    return (
      <section className="sky-card sky-card--session" role="alert" data-testid="theme-ai-unconfigured">
        <h3>{t('themes:read.unconfigured.title')}</h3>
        <p className="sky-hint">{t('themes:read.unconfigured.body')}</p>
        <div className="sky-about-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => openSettings()}>
            {t('themes:read.openSettings')}
          </button>
        </div>
      </section>
    )
  }
  return null
}

/** AI が未設定のとき、問いを書くための小さな書き込み欄（会話の入力欄は閉じている）。手入力の下書きになる。 */
export function WriteForm({ ws, initial, placeholder }: { ws: HomeWorkspace; initial: string; placeholder: string }) {
  const { t } = useTranslation()
  const [text, setText] = useState(initial)
  const lines = text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
  return (
    <section className="sky-card sky-card--session" data-testid="write-form">
      <textarea
        className="consult-input"
        rows={3}
        value={text}
        placeholder={placeholder}
        aria-label={placeholder}
        data-testid="write-input"
        onChange={(e) => setText(e.target.value)}
      />
      <div className="sky-about-actions">
        <button
          type="button"
          className="btn btn--accent btn--sm"
          disabled={lines.length === 0 || ws.busy}
          data-testid="write-go"
          onClick={() => void ws.startFromText(text)}
        >
          {t('themes:write.manual')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={ws.cancelWriting}>
          {t('themes:wizard.close')}
        </button>
      </div>
    </section>
  )
}

/** 手で関係を足す（A・関係・B）。AI を使わない下書きの道で、概念どうしをつなぐ。 */
function HandRelation({ ws, draft, lang }: { ws: HomeWorkspace; draft: ThemeDraft; lang: string }) {
  const { t } = useTranslation()
  const [r, setR] = useState<{ from: string; rel: RelKind; to: string }>({ from: '', rel: 'lowers', to: '' })
  const names = draft.concepts
  return (
    <div className="thm-hand-row thm-hand-rel" data-testid="hand-relation">
      <select aria-label={t('themes:chat.edgeA')} value={r.from} data-testid="hand-rel-from" onChange={(e) => setR({ ...r, from: e.target.value })}>
        <option value="">{t('themes:chat.edgeA')}</option>
        {names.map((c) => (
          <option key={c.key} value={c.key}>
            {conceptName(c, lang)}
          </option>
        ))}
      </select>
      <select aria-label={t('themes:chat.edgeRel')} value={r.rel} data-testid="hand-rel-kind" onChange={(e) => setR({ ...r, rel: e.target.value as RelKind })}>
        {REL_KINDS.map((k) => (
          <option key={k} value={k}>
            {t(`themes:map.pillTitle.${k}`)}
          </option>
        ))}
      </select>
      <select aria-label={t('themes:chat.edgeB')} value={r.to} data-testid="hand-rel-to" onChange={(e) => setR({ ...r, to: e.target.value })}>
        <option value="">{t('themes:chat.edgeB')}</option>
        {names.map((c) => (
          <option key={c.key} value={c.key}>
            {conceptName(c, lang)}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        disabled={ws.busy || !r.from || !r.to || r.from === r.to}
        data-testid="hand-rel-go"
        onClick={() => void ws.addHandRelation(r).then((ok) => ok && setR({ ...r, from: '', to: '' }))}
      >
        {t('themes:review.addGo')}
      </button>
    </div>
  )
}

/** 新しく作る概念の ID の欄（問題のある行には理由を出す）。 */
function IdRows({ ws, ids }: { ws: HomeWorkspace; ids: { key: string; label: string; slug: string }[] }) {
  const { t } = useTranslation()
  return (
    <div className="thm-ids" data-testid="place-ids">
      {ids.map((l) => (
        <div key={l.key} className="thm-id">
          <span>{l.label}</span>
          <label className="thm-idfield">
            <span className="vocab-field-hint">{t('themes:place.id')}</span>
            <input
              type="text"
              aria-label={t('themes:place.idAria', { label: l.label })}
              className={ws.problems[l.key] ? 'thm-input--bad' : ''}
              value={l.slug}
              onChange={(e) => ws.updateSlug(l.key, e.target.value)}
            />
          </label>
          {ws.problems[l.key] === 'slug' && <span className="promote-err">{t('themes:place.idBad')}</span>}
          {ws.problems[l.key] === 'taken' && <span className="promote-err">{t('themes:place.idTaken')}</span>}
          {ws.problems[l.key] === 'duplicate' && <span className="promote-err">{t('themes:place.idDuplicate')}</span>}
        </div>
      ))}
    </div>
  )
}
