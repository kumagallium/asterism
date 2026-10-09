// 地図で選んだ 2 つの丸のあいだに「線を引く」小さなフォーム
// （ADR upper-structure-shared-terms.md §2.5.1 3・契約 handoff §6 Step 4.4）。
//
// 関係は閉集合から、両端の kind で絞る（`lineChoice` — 純関数）。確定は
// POST /api/crosswalk/align（`align`）。409（循環）・422（未鋳造／種類と項目の混在）は
// 文言で言い分ける。LLM は呼ばない。配置は変えない（フォームは地図の外に出る）。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { align } from './crosswalkApi'
import { httpStatusOf, lineChoice, relationsFor, type AlignRelation, type PickEnd } from './lineChoice'

export interface LineFormCq {
  tool_name: string
  title: string
}

export function LineForm({
  a,
  b,
  cqs = [],
  onDone,
  onCancel,
}: {
  /** 選んだ丸（選んだ順）。向きは既定で 狭い方 → 広い方（データセット → 共有 → 標準）。 */
  a: PickEnd
  b: PickEnd
  /** 線が答える問いの選択肢（共有のことばの CQ。任意）。 */
  cqs?: LineFormCq[]
  /** 線を引けた（呼び手が地図を読み直し、選択を空にする）。 */
  onDone: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [swapped, setSwapped] = useState(false)
  const [picked, setPicked] = useState<AlignRelation | null>(null)
  const [cq, setCq] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const base = lineChoice(a, b)
  const source = swapped ? base.target : base.source
  const target = swapped ? base.source : base.target
  const relations = relationsFor(source.termKind, target.termKind)
  const same = source.iri === target.iri
  // 選んだ関係が今の向きで引けないとき（向きを入れ替えた後など）は先頭に戻す。
  const relation = picked && relations.includes(picked) ? picked : (relations[0] ?? null)
  const canSubmit = !busy && !same && relation !== null

  const endText = (e: PickEnd) => {
    const role = t(`vocabmap:line.role.${e.role}`)
    const kind = e.termKind === 'unknown' ? '' : t(`vocabmap:line.termKind.${e.termKind}`)
    return kind && kind !== role ? `${role} · ${kind}` : role
  }

  const submit = async () => {
    if (!canSubmit || relation === null) return
    setBusy(true)
    setErr(null)
    try {
      await align(source.iri, target.iri, relation, undefined, undefined, cq || undefined)
      onDone()
    } catch (e) {
      const status = httpStatusOf(e)
      const detail = e instanceof Error && e.message ? `: ${e.message}` : ''
      setErr(
        status === 409
          ? t('vocabmap:line.error.cycle')
          : status === 422
            ? t('vocabmap:line.error.invalid')
            : t('vocabmap:line.error.generic', { detail }),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="line-form" role="group" aria-label={t('vocabmap:line.title')}>
      <h4 className="line-form-title">{t('vocabmap:line.title')}</h4>
      <div className="line-form-ends">
        <span className="line-form-end" title={source.iri}>
          {source.label}
          <small>{endText(source)}</small>
        </span>
        <span className="line-form-arrow" aria-hidden>
          →
        </span>
        <span className="line-form-end" title={target.iri}>
          {target.label}
          <small>{endText(target)}</small>
        </span>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => setSwapped((v) => !v)}
          disabled={!base.canSwap || busy}
        >
          {t('vocabmap:line.swap')}
        </button>
      </div>

      {same ? (
        <p className="line-form-err">{t('vocabmap:line.error.same')}</p>
      ) : relations.length === 0 ? (
        <p className="line-form-err">{t('vocabmap:line.error.mixed')}</p>
      ) : (
        <fieldset className="line-form-field" style={{ border: 'none', padding: 0, margin: 0 }}>
          <legend style={{ padding: 0 }}>{t('vocabmap:line.relation')}</legend>
          <div className="line-form-rels">
            {relations.map((r) => (
              <label key={r} className="line-form-rel">
                <input
                  type="radio"
                  name="line-form-relation"
                  value={r}
                  checked={relation === r}
                  onChange={() => setPicked(r)}
                />
                {t(`vocabmap:line.relations.${r}`)}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      {cqs.length > 0 && (
        <label className="line-form-field">
          {t('vocabmap:line.cq')}
          <select value={cq} onChange={(e) => setCq(e.target.value)} disabled={busy}>
            <option value="">{t('vocabmap:line.cqNone')}</option>
            {cqs.map((c) => (
              <option key={c.tool_name} value={c.tool_name}>
                {c.title}
              </option>
            ))}
          </select>
        </label>
      )}

      {err && (
        <p className="line-form-err" role="alert">
          {err}
        </p>
      )}
      <div className="line-form-actions">
        <button type="button" className="btn btn--accent btn--sm" disabled={!canSubmit} onClick={submit}>
          {busy ? t('vocabmap:line.submitting') : t('vocabmap:line.submit')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={onCancel}>
          {t('vocabmap:line.cancel')}
        </button>
      </div>
    </div>
  )
}
