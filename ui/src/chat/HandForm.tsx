// AI 無しの手で足すフォーム（PR 11a・右の図の下）。AI が設定されていないあいだだけ出す。
// 1 行目: 名前・種別・「足す」→ add_concept。2 行目: A・関係・B・「足す」→ add_relation。返事はチャットに手番として出る。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { REL_KINDS } from '../themes/themeDraft'
import type { GraphOp, SetAnswerBody, SkyDraft } from './chatApi'

export interface HandFormProps {
  /** いまの未確認の図（概念の一覧）。無ければ関係の行は出さない。 */
  draft: SkyDraft | null
  /** 次に足す概念の key（`h<n>`）。 */
  nextKey: string
  busy: boolean
  /** 差分を送る。送れたら true。 */
  onSubmit: (ops: GraphOp[]) => Promise<boolean>
  /** 既にある問いの答え方を決める。送れたら true。 */
  onAnswer?: (body: SetAnswerBody) => Promise<boolean>
}

/** 答え方の 8 つの方法（AI 無しで決められる形）。`id` は `op` か `op:direction`。 */
const WAYS = [
  { id: 'factors:lowers', mode: 'knowledge', op: 'factors', direction: 'lowers', label: 'answer.option.lowers', need: 'any' },
  { id: 'factors:raises', mode: 'knowledge', op: 'factors', direction: 'raises', label: 'answer.option.raises', need: 'any' },
  { id: 'factors:affects', mode: 'knowledge', op: 'factors', direction: 'affects', label: 'answer.option.affects', need: 'any' },
  { id: 'parts', mode: 'knowledge', op: 'parts', direction: '', label: 'answer.option.parts', need: 'any' },
  { id: 'kinds', mode: 'knowledge', op: 'kinds', direction: '', label: 'answer.option.kinds', need: 'any' },
  { id: 'count', mode: 'data', op: 'count', direction: '', label: 'answer.option.count', need: 'class' },
  { id: 'range', mode: 'data', op: 'range', direction: '', label: 'answer.option.range', need: 'property' },
  { id: 'top', mode: 'data', op: 'top', direction: '', label: 'answer.option.top', need: 'property' },
] as const

export function HandForm({ draft, nextKey, busy, onSubmit, onAnswer }: HandFormProps) {
  const { t } = useTranslation('chat')
  const { t: tt } = useTranslation('themes')
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'class' | 'property'>('class')
  const [from, setFrom] = useState('')
  const [rel, setRel] = useState<string>(REL_KINDS[0])
  const [to, setTo] = useState('')
  const concepts = draft?.concepts ?? []
  const has = (k: string) => concepts.some((c) => c.key === k)
  const a = (has(from) ? from : '') || concepts[0]?.key || ''
  const questions = draft?.questions ?? []
  const [qSel, setQSel] = useState('')
  const [waySel, setWaySel] = useState<string>(WAYS[0].id)
  const [targetSel, setTargetSel] = useState('')
  const way = WAYS.find((w) => w.id === waySel) ?? WAYS[0]
  const targets = concepts.filter((c) => way.need === 'any' || c.kind === way.need)
  const question = questions.includes(qSel) ? qSel : (questions[0] ?? '')
  const target = targets.some((c) => c.key === targetSel) ? targetSel : (targets[0]?.key ?? '')
  const b = (has(to) ? to : '') || concepts.find((c) => c.key !== a)?.key || ''

  async function addConcept() {
    const label = name.trim()
    if (!label || busy) return
    const ok = await onSubmit([{ op: 'add_concept', key: nextKey, label_ja: label, label_en: '', aliases: [], kind, use: 'new', reason: '' }])
    if (ok) setName('')
  }
  async function addRelation() {
    if (!a || !b || a === b || busy) return
    await onSubmit([{ op: 'add_relation', from: a, rel, to: b, reason: '' }])
  }

  async function decide() {
    if (!onAnswer || !question || !target || busy) return
    const body: SetAnswerBody =
      way.mode === 'knowledge'
        ? { question, mode: 'knowledge', op: way.op, target, direction: way.direction }
        : way.op === 'count'
          ? { question, mode: 'data', op: 'count', kind: target }
          : { question, mode: 'data', op: way.op, property: target }
    await onAnswer(body)
  }

  return (
    <div className="hand-form" data-testid="hand-form">
      <span className="hand-lead">{t('hand.lead')}</span>
      <form
        className="hand-row"
        onSubmit={(e) => {
          e.preventDefault()
          void addConcept()
        }}
      >
        <input className="hand-input" data-testid="hand-name" value={name} maxLength={80} placeholder={t('hand.name')} aria-label={t('hand.name')} onChange={(e) => setName(e.target.value)} disabled={busy} />
        <select className="hand-select" data-testid="hand-kind" value={kind} aria-label={t('hand.kind')} onChange={(e) => setKind(e.target.value as 'class' | 'property')} disabled={busy}>
          <option value="class">{tt('kind.class')}</option>
          <option value="property">{tt('kind.property')}</option>
        </select>
        <button type="submit" className="btn btn--sm" data-testid="hand-add" disabled={busy || name.trim() === ''}>
          {t('hand.add')}
        </button>
      </form>
      {concepts.length >= 2 && (
        <form
          className="hand-row"
          onSubmit={(e) => {
            e.preventDefault()
            void addRelation()
          }}
        >
          <select className="hand-select" data-testid="hand-rel-from" value={a} aria-label={t('hand.from')} onChange={(e) => setFrom(e.target.value)} disabled={busy}>
            {concepts.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
          <select className="hand-select" data-testid="hand-rel-kind" value={rel} aria-label={t('hand.rel')} onChange={(e) => setRel(e.target.value)} disabled={busy}>
            {REL_KINDS.map((r) => (
              <option key={r} value={r}>
                {tt(`map.pillTitle.${r}`)}
              </option>
            ))}
          </select>
          <select className="hand-select" data-testid="hand-rel-to" value={b} aria-label={t('hand.to')} onChange={(e) => setTo(e.target.value)} disabled={busy}>
            {concepts.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
          <button type="submit" className="btn btn--sm" data-testid="hand-rel-add" disabled={busy || !a || !b || a === b}>
            {t('hand.add')}
          </button>
        </form>
      )}
      {questions.length > 0 && onAnswer && (
        <form
          className="hand-row"
          onSubmit={(e) => {
            e.preventDefault()
            void decide()
          }}
        >
          <span className="hand-lead">{t('hand.answer')}</span>
          <select className="hand-select" data-testid="hand-answer-q" value={question} aria-label={t('hand.question')} onChange={(e) => setQSel(e.target.value)} disabled={busy}>
            {questions.map((q) => (
              <option key={q} value={q}>
                {q}
              </option>
            ))}
          </select>
          <select className="hand-select" data-testid="hand-answer-way" value={way.id} aria-label={t('hand.way')} onChange={(e) => setWaySel(e.target.value)} disabled={busy}>
            {WAYS.map((w) => (
              <option key={w.id} value={w.id}>
                {tt(w.label)}
              </option>
            ))}
          </select>
          <select className="hand-select" data-testid="hand-answer-target" value={target} aria-label={t('hand.target')} onChange={(e) => setTargetSel(e.target.value)} disabled={busy}>
            {targets.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
          <button type="submit" className="btn btn--sm" data-testid="hand-answer" disabled={busy || !question || !target}>
            {t('hand.decide')}
          </button>
        </form>
      )}
    </div>
  )
}
