// AI 無しの手で足すフォーム（PR 11a・右の図の下）。AI が設定されていないあいだだけ出す。
// 1 行目: 名前・種別・「足す」→ add_concept。2 行目: A・関係・B・「足す」→ add_relation。返事はチャットに手番として出る。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { REL_KINDS } from '../themes/themeDraft'
import type { GraphOp, SkyDraft } from './chatApi'

export interface HandFormProps {
  /** いまの未確認の図（概念の一覧）。無ければ関係の行は出さない。 */
  draft: SkyDraft | null
  /** 次に足す概念の key（`h<n>`）。 */
  nextKey: string
  busy: boolean
  /** 差分を送る。送れたら true。 */
  onSubmit: (ops: GraphOp[]) => Promise<boolean>
}

export function HandForm({ draft, nextKey, busy, onSubmit }: HandFormProps) {
  const { t } = useTranslation('chat')
  const { t: tt } = useTranslation('themes')
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'class' | 'property'>('class')
  const [from, setFrom] = useState('')
  const [rel, setRel] = useState<string>(REL_KINDS[0])
  const [to, setTo] = useState('')
  const concepts = draft?.concepts ?? []
  const a = from || concepts[0]?.key || ''
  const b = to || concepts.find((c) => c.key !== a)?.key || ''

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
        <input className="hand-input" value={name} maxLength={80} placeholder={t('hand.name')} aria-label={t('hand.name')} onChange={(e) => setName(e.target.value)} disabled={busy} />
        <select className="hand-select" value={kind} aria-label={t('hand.kind')} onChange={(e) => setKind(e.target.value as 'class' | 'property')} disabled={busy}>
          <option value="class">{tt('kind.class')}</option>
          <option value="property">{tt('kind.property')}</option>
        </select>
        <button type="submit" className="btn btn--sm" disabled={busy || name.trim() === ''}>
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
          <select className="hand-select" value={a} aria-label={t('hand.from')} onChange={(e) => setFrom(e.target.value)} disabled={busy}>
            {concepts.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
          <select className="hand-select" value={rel} aria-label={t('hand.rel')} onChange={(e) => setRel(e.target.value)} disabled={busy}>
            {REL_KINDS.map((r) => (
              <option key={r} value={r}>
                {tt(`map.pillTitle.${r}`)}
              </option>
            ))}
          </select>
          <select className="hand-select" value={b} aria-label={t('hand.to')} onChange={(e) => setTo(e.target.value)} disabled={busy}>
            {concepts.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
          <button type="submit" className="btn btn--sm" disabled={busy || !a || !b || a === b}>
            {t('hand.add')}
          </button>
        </form>
      )}
    </div>
  )
}
