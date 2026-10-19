import { useEffect, useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { PickEnd } from '../lineChoice'
import { httpStatusOf } from '../lineChoice'
import { listTerms } from '../vocabApi'
import {
  type DecideChoice,
  type DecidePreview,
  NONE,
  effectText,
  leadText,
  optionLabel,
  orderCandidates,
  reasonText,
  relationChoices,
  sentence,
} from './decide'
import { decide, dismiss, previewDecision } from './decideApi'
import './decide.css'

// 「ひとこと決める」（星・種類・項目・用語のどれからでも）。設計: ui-redesign design.md §5。
//
//   「〔subject〕は [候補 ▾]」＋ ラジオ（一種です／同じものです／当てはまるものは無い）。
//   **初期選択なし**。ラジオか候補が変わるたび帰結を試算して 1 行で出す（保存はしない）。
//   「そう決める」で初めて保存（関係を 1 本決める）。「当てはまるものは無い」は提案を外す。
//   「あとで決める」は何も保存せずに閉じる。

export interface DecidedInfo {
  subject: PickEnd
  target: PickEnd
  choice: DecideChoice
}

type PreviewState =
  | { key: string; status: 'ok'; preview: DecidePreview }
  | { key: string; status: 'error' }

export function DecidePanel({
  subject,
  candidates,
  dismissKey,
  onDecided,
  onLater,
}: {
  /** 決める側（星・種類・項目）。 */
  subject: PickEnd
  /** 行き先の候補。無ければ、既にある用語（共有のことば）を読んで出す。 */
  candidates?: PickEnd[]
  /** 「当てはまるものは無い」を覚える主語の鍵。既定は `subject.iri`（公開前の列・種類は画面が鍵を渡す）。 */
  dismissKey?: string
  /** 保存できた（関係を決めた／提案を外した）。 */
  onDecided?: (info: DecidedInfo) => void
  /** 「あとで決める」。何も保存しない。 */
  onLater?: () => void
}) {
  const { t } = useTranslation()
  const groupName = useId()
  const [loaded, setLoaded] = useState<PickEnd[] | null>(null)
  const [targetIri, setTargetIri] = useState('')
  const [choice, setChoice] = useState<DecideChoice | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState<DecidedInfo | null>(null)
  const [previewState, setPreviewState] = useState<PreviewState | null>(null)

  // 候補を渡されないときだけ、既にある用語を読む（読めなければ候補なし — 行き止まりにはしない）。
  useEffect(() => {
    if (candidates) return
    let off = false
    listTerms()
      .then((terms) => {
        if (off) return
        setLoaded(
          terms.map((term) => ({
            id: term.iri,
            iri: term.iri,
            label: term.label,
            role: 'shared' as const,
            termKind: term.kind,
          })),
        )
      })
      .catch(() => !off && setLoaded([]))
    return () => {
      off = true
    }
  }, [candidates])

  const ordered = useMemo(
    () => orderCandidates(subject, candidates ?? loaded ?? []),
    [subject, candidates, loaded],
  )
  const target = ordered.find((c) => c.iri === targetIri) ?? ordered[0]
  const relations = target ? relationChoices(subject, target) : []
  // 候補を替えて今の選択が選べなくなったら、選んでいないことにする（勝手に別の関係へ寄せない）。
  const picked: DecideChoice | null =
    choice === null || choice === NONE || relations.includes(choice) ? choice : null

  const previewKey = target && picked && picked !== NONE ? `${subject.iri}|${target.iri}|${picked}` : ''
  useEffect(() => {
    if (!previewKey || !target || !picked || picked === NONE) return
    let stale = false
    previewDecision(subject.iri, target.iri, picked)
      .then((preview) => !stale && setPreviewState({ key: previewKey, status: 'ok', preview }))
      .catch(() => !stale && setPreviewState({ key: previewKey, status: 'error' }))
    return () => {
      stale = true
    }
    // target / picked は previewKey に畳まれている。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey, subject.iri])
  const current = previewState && previewState.key === previewKey ? previewState : null

  let effect = t('decide:effect.idle')
  if (picked === NONE) effect = t('decide:effect.none')
  else if (picked && previewKey) {
    if (!current) effect = t('decide:effect.loading')
    else if (current.status === 'error') effect = t('decide:effect.error')
    else if (!current.preview.valid) effect = reasonText(current.preview.reason, t)
    else effect = effectText(current.preview, t)
  }
  const blocked = current?.status === 'ok' && !current.preview.valid
  const canDecide = !busy && !!target && picked !== null && !blocked

  async function submit() {
    if (!target || picked === null || !canDecide) return
    setBusy(true)
    setErr('')
    try {
      if (picked === NONE) await dismiss(dismissKey ?? subject.iri, target.iri)
      else await decide(subject.iri, target.iri, picked)
      const info = { subject, target, choice: picked }
      setDone(info)
      onDecided?.(info)
    } catch (e) {
      const status = httpStatusOf(e)
      setErr(
        status === 409
          ? t('decide:reason.cycle')
          : status === 422
            ? t('decide:reason.invalid')
            : t('decide:error.generic', { detail: e instanceof Error && e.message ? `: ${e.message}` : '' }),
      )
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <div className="decide" data-testid="decide-done">
        <p className="decide-done" role="status">
          {done.choice === NONE
            ? t('decide:done.none', { subject: done.subject.label, target: done.target.label })
            : t('decide:done.decided', {
                sentence: sentence(done.subject.label, done.target.label, done.choice, t),
              })}
        </p>
        <p className="decide-meta">
          <a className="link-btn" href="#/decisions">
            {t('decide:done.link')}
          </a>
        </p>
      </div>
    )
  }

  return (
    <div className="decide" data-testid="decide-panel">
      <h4 className="decide-h">{t('decide:title')}</h4>
      {ordered.length === 0 ? (
        <p className="decide-meta" data-testid="decide-no-candidates">
          {candidates || loaded ? t('decide:noCandidates') : t('decide:loading')}
        </p>
      ) : (
        <>
          <div className="decide-sentence">
            <span>{leadText(subject, target, t)}</span>
            <select
              className="decide-select"
              aria-label={t('decide:targetAria')}
              value={target?.iri ?? ''}
              onChange={(e) => setTargetIri(e.target.value)}
            >
              {ordered.map((c) => (
                <option key={c.iri} value={c.iri}>
                  {c.label}
                  {t(`decide:role.${c.role}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="decide-choice" role="radiogroup" aria-label={t('decide:choiceAria')}>
            {[...relations, NONE].map((r) => (
              <label key={r}>
                <input
                  type="radio"
                  name={groupName}
                  value={r}
                  checked={picked === r}
                  onChange={() => setChoice(r as DecideChoice)}
                />
                <span>{optionLabel(r as DecideChoice, t)}</span>
              </label>
            ))}
          </div>
          <div className="decide-effect" role="status" aria-live="polite" data-testid="decide-effect">
            {effect}
          </div>
          {err && (
            <p className="decide-err" role="alert">
              {err}
            </p>
          )}
          <div className="decide-actions">
            <button
              type="button"
              className="btn btn--accent btn--sm"
              disabled={!canDecide}
              onClick={() => void submit()}
            >
              {busy ? t('decide:deciding') : t('decide:go')}
            </button>
            {onLater && (
              <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={onLater}>
                {t('decide:later')}
              </button>
            )}
            <span className="decide-meta">{t('decide:savedNote')}</span>
          </div>
        </>
      )}
    </div>
  )
}
