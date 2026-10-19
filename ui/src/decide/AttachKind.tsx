import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { PickEnd } from '../lineChoice'
import type { SharedTerm } from '../vocabApi'
import { DecidePanel } from './DecidePanel'
import { normalizeName } from './decide'

/** 用語に入れる候補（データセットの種類・項目）。`datasetName` は選ぶときの手がかり。 */
export type AttachChoice = PickEnd & { datasetName?: string }

/**
 * 「この用語に種類（項目）を入れる」。全データセットの種類（項目）から 1 つ選び、その 1 つについて
 * 「ひとこと決める」（行き先はこの用語に固定）。名前が完全一致するものを先頭に出す。
 */
export function AttachKind({
  term,
  choices,
  onDecided,
  onClose,
}: {
  term: SharedTerm
  /** 選べる種類・項目（用語の種類に合うものだけ渡す）。 */
  choices: AttachChoice[]
  onDecided: () => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [pickedIri, setPickedIri] = useState('')
  const ordered = useMemo(() => {
    const names = new Set([term.label, term.label_en ?? ''].filter(Boolean).map(normalizeName))
    const exact = (c: AttachChoice) => names.has(normalizeName(c.label))
    return choices
      .map((c, i) => ({ c, i }))
      .sort(
        (a, b) =>
          Number(exact(b.c)) - Number(exact(a.c)) ||
          a.c.label.localeCompare(b.c.label, 'ja') ||
          a.i - b.i,
      )
      .map((x) => x.c)
  }, [choices, term.label, term.label_en])
  const subject = ordered.find((c) => c.iri === pickedIri) ?? ordered[0]
  const target: PickEnd = {
    id: term.iri,
    iri: term.iri,
    label: term.label,
    role: 'shared',
    termKind: term.kind,
  }
  const what = t(`decide:attach.${term.kind}`)

  return (
    <div className="decide" data-testid="attach-kind">
      {ordered.length === 0 || !subject ? (
        <>
          <p className="decide-meta">{t('decide:attach.none', { what })}</p>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
            {t('decide:attach.close')}
          </button>
        </>
      ) : (
        <>
          <label className="decide-sentence">
            <span>{t('decide:attach.pick', { what })}</span>
            <select
              className="decide-select"
              value={subject.iri}
              onChange={(e) => setPickedIri(e.target.value)}
            >
              {ordered.map((c) => (
                <option key={c.iri} value={c.iri}>
                  {c.datasetName ? `${c.label}（${c.datasetName}）` : c.label}
                </option>
              ))}
            </select>
          </label>
          <DecidePanel
            key={subject.iri}
            subject={subject}
            candidates={[target]}
            onDecided={onDecided}
            onLater={onClose}
          />
        </>
      )}
    </div>
  )
}
