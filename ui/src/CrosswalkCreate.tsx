import { useEffect, useRef, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import type { JobHandle } from './api'
import {
  type BuildResult,
  buildPerspective,
  type CrosswalkPerspective,
  type DiscoverCandidate,
  type DiscoverResult,
  discoverCrosswalks,
} from './crosswalkApi'
import { askQuestionsFor, conceptLabel, flagKey, foldingGain, sameAsKey } from './crosswalkLabels'
import { uniqueCrosswalkId } from './crosswalkMint'
import { plainError } from './kantan/errorMessages'

/**
 * Making a connection, reduced to one decision (kantan-mode ADR K2/K13).
 *
 * Opening this scans the published datasets for values that ACTUALLY overlap and lays
 * the results out as cards with the evidence — which data connects, on what, how many
 * values match, and the real spellings side by side. Everything that carries no
 * meaning for a person (the concept key, the hub's words, what counts as the same
 * value, the id) is derived by the server; the human picks a card and confirms.
 *
 * Four steps: pick → confirm → build → done. The confirm is deliberate: a connection
 * is a claim about what two datasets have in common, and the ADR requires that claim
 * to pass a human (`crosswalk-hub.md` — the hub is derived data needing a review
 * gate). It is one tap, with nothing left to fill in.
 */
type Phase = 'scanning' | 'pick' | 'confirm' | 'building' | 'done'

export function CrosswalkCreate({
  perspectives,
  onCancel,
  onBuilt,
  onOpenManual,
  onAddData,
  onOpenAsk,
}: {
  /** The crosswalks that already exist — used to avoid silently replacing one. */
  perspectives: CrosswalkPerspective[]
  onCancel: () => void
  /** A connection was built: the parent reloads its list. */
  onBuilt: () => void
  /** Escape hatch to the detail tier, optionally seeded with a candidate. */
  onOpenManual: (candidate?: DiscoverCandidate) => void
  onAddData?: () => void
  onOpenAsk?: (question: string) => void
}) {
  const { t } = useTranslation()
  const [phase, setPhase] = useState<Phase>('scanning')
  const [result, setResult] = useState<DiscoverResult | null>(null)
  const [scanErr, setScanErr] = useState('')
  const [progress, setProgress] = useState('')
  const [picked, setPicked] = useState<DiscoverCandidate | null>(null)
  const [name, setName] = useState('')
  const [buildErr, setBuildErr] = useState('')
  const [built, setBuilt] = useState<BuildResult | null>(null)
  const job = useRef<JobHandle | null>(null)

  // Scan on mount. The handle is closed on unmount so leaving the screen stops the
  // stream (the server-side job also cancels — nobody waits for a scan nobody reads).
  useEffect(() => {
    let off = false
    discoverCrosswalks({
      onDone: (r) => {
        if (off) return
        setResult(r)
        setPhase('pick')
      },
      onError: (m) => {
        if (off) return
        setScanErr(m)
        setPhase('pick')
      },
      onRunning: (data) => {
        if (off) return
        const nameOf = typeof data.name === 'string' ? data.name : ''
        setProgress(nameOf)
      },
    })
      .then((h) => {
        if (off) h.cancel().catch(() => h.close())
        else job.current = h
      })
      .catch((e) => {
        if (off) return
        setScanErr(e instanceof Error ? e.message : String(e))
        setPhase('pick')
      })
    return () => {
      off = true
      job.current?.cancel().catch(() => job.current?.close())
      job.current = null
    }
  }, [])

  const existingIds = perspectives.map((p) => p.perspective_id)
  const candidates = (result?.candidates ?? [])
    .filter((c) => (c.participants?.length ?? 0) >= 2)
    .sort((a, b) => b.matched - a.matched)
  const scanned = result?.scanned

  function pick(c: DiscoverCandidate) {
    setPicked(c)
    setName(t('crosswalk:create.defaultName', { label: conceptLabel(c.concept) }))
    setBuildErr('')
    setPhase('confirm')
  }

  async function build() {
    if (!picked) return
    setPhase('building')
    setBuildErr('')
    try {
      // ALWAYS a named crosswalk with an explicit id. The no-name path
      // (`buildCrosswalk`) overwrites the legacy default one — never reachable here.
      const id = uniqueCrosswalkId(picked.perspective_id, existingIds)
      setBuilt(await buildPerspective(id, picked.build_config, name.trim() || picked.name))
      setPhase('done')
      onBuilt()
    } catch (e) {
      setBuildErr(e instanceof Error ? e.message : String(e))
      setPhase('confirm')
    }
  }

  // --- scanning ----------------------------------------------------------------
  if (phase === 'scanning') {
    return (
      <div className="xw-create">
        <section className="kz-card">
          <h3 className="kz-title">{t('crosswalk:create.scanning')}</h3>
          <p className="kz-note">{t('crosswalk:create.scanningSub')}</p>
          {progress && (
            <p className="loading-row">
              <span className="spinner" />
              {t('crosswalk:create.scanningAt', { name: progress })}
            </p>
          )}
          <div className="xw-cand-grid" aria-busy="true">
            <div className="xw-cand-skel" />
            <div className="xw-cand-skel" />
          </div>
          <div className="kz-actions">
            <button type="button" className="btn btn--ghost" onClick={onCancel}>
              {t('crosswalk:create.cancel')}
            </button>
          </div>
        </section>
      </div>
    )
  }

  // --- done --------------------------------------------------------------------
  if (phase === 'done' && built && picked) {
    return (
      <div className="xw-create">
        <section className="kz-card kz-done">
          <h3 className="kz-done-title">✓ {t('crosswalk:create.done.title')}</h3>
          <p className="kz-note">
            <Trans
              i18nKey="crosswalk:create.done.stat"
              values={{
                shared: built.shared_total,
                count: built.participants_used.length,
              }}
              components={[<strong />, <strong />]}
            />
          </p>
          {onOpenAsk && (
            <>
              <p className="kz-note">{t('crosswalk:create.done.askLead')}</p>
              <div className="kz-q-options">
                {askQuestionsFor(picked).map((q) => {
                  const text = t(q.key, q.values)
                  return (
                    <button
                      key={q.key}
                      type="button"
                      className="kz-pill"
                      onClick={() => onOpenAsk(text)}
                    >
                      {text}
                    </button>
                  )
                })}
              </div>
              <p className="kz-note">{t('crosswalk:create.done.askHint')}</p>
            </>
          )}
          <hr className="kz-divider" />
          <div className="kz-actions">
            <button type="button" onClick={onCancel}>
              {t('crosswalk:create.done.seeBtn')}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => {
                setPicked(null)
                setBuilt(null)
                setPhase('pick')
              }}
            >
              {t('crosswalk:create.done.againBtn')}
            </button>
          </div>
        </section>
      </div>
    )
  }

  // --- confirm / building ------------------------------------------------------
  if ((phase === 'confirm' || phase === 'building') && picked) {
    const busy = phase === 'building'
    const idTaken = existingIds.includes(picked.perspective_id)
    const err = buildErr ? plainError(buildErr) : null
    return (
      <div className="xw-create">
        <section className="kz-card">
          <h3 className="kz-title">{t('crosswalk:create.confirm.title')}</h3>

          <div className="xw-confirm-row">
            <span className="xw-confirm-label">{t('crosswalk:create.confirm.whatHead')}</span>
            <span className="xw-confirm-value">
              {picked.participants.map((p) => p.name).join(' ・ ')}
            </span>
          </div>
          <div className="xw-confirm-row">
            <span className="xw-confirm-label">{t('crosswalk:create.confirm.onHead')}</span>
            <span className="xw-confirm-value">{conceptLabel(picked.concept)}</span>
          </div>
          <div className="xw-confirm-row">
            <span className="xw-confirm-label">{t('crosswalk:create.confirm.matchHead')}</span>
            <span className="xw-confirm-value">
              {t('crosswalk:create.card.count', { count: picked.matched })}
            </span>
          </div>

          <p className="kz-note">{t(sameAsKey(picked.normalizer))}</p>

          <label className="xw-confirm-name">
            <span className="xw-confirm-label">{t('crosswalk:create.confirm.nameLabel')}</span>
            <input
              type="text"
              className="xw-key-input"
              value={name}
              disabled={busy}
              placeholder={t('crosswalk:create.confirm.namePlaceholder')}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <p className="kz-note">{t('crosswalk:create.confirm.nameHint')}</p>
          {idTaken && <p className="kz-note kz-caution">{t('crosswalk:create.confirm.idTaken')}</p>}
          <p className="kz-note kz-promise">{t('crosswalk:create.confirm.promise')}</p>

          {err && (
            <p className="promote-err">
              {err.title ? `${err.title} — ` : ''}
              {t(err.body)}
            </p>
          )}
          {busy && <p className="kz-note">{t('crosswalk:create.building.line')}</p>}

          <div className="kz-actions">
            <button type="button" disabled={busy} onClick={build}>
              {busy ? t('crosswalk:create.building.title') : t('crosswalk:create.confirm.build')}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              onClick={() => setPhase('pick')}
            >
              {t('crosswalk:create.confirm.back')}
            </button>
          </div>
        </section>
      </div>
    )
  }

  // --- pick --------------------------------------------------------------------
  const tooFew = (scanned?.datasets.length ?? 0) < 2 && !scanErr
  return (
    <div className="xw-create">
      <div className="ds-subhead">
        {t('crosswalk:create.head')}
        <span className="xw-hint-inline">{t('crosswalk:create.lead')}</span>
      </div>

      {scanErr && (
        <div className="state-block">
          <p className="state-title">{t('crosswalk:create.failed.title')}</p>
          <p className="state-sub">{t(plainError(scanErr).body)}</p>
          <p className="state-sub">{t('crosswalk:create.failed.sub')}</p>
          <div className="kz-actions">
            <button type="button" onClick={() => onOpenManual()}>
              {t('crosswalk:create.failed.manualBtn')}
            </button>
            <button type="button" className="btn btn--ghost" onClick={onCancel}>
              {t('crosswalk:create.cancel')}
            </button>
          </div>
        </div>
      )}

      {!scanErr && tooFew && (
        <div className="state-block">
          <p className="state-title">{t('crosswalk:create.tooFew.title')}</p>
          <p className="state-sub">
            {t('crosswalk:create.tooFew.sub', { count: scanned?.datasets.length ?? 0 })}
          </p>
          <div className="kz-actions">
            {onAddData && (
              <button type="button" onClick={onAddData}>
                {t('crosswalk:create.tooFew.addBtn')}
              </button>
            )}
            <button type="button" className="btn btn--ghost" onClick={onCancel}>
              {t('crosswalk:create.tooFew.seeBtn')}
            </button>
          </div>
        </div>
      )}

      {!scanErr && !tooFew && candidates.length === 0 && (
        <div className="state-block">
          <p className="state-title">{t('crosswalk:create.none.title')}</p>
          <p className="state-sub">{t('crosswalk:create.none.sub')}</p>
          <div className="kz-actions">
            <button type="button" onClick={() => onOpenManual()}>
              {t('crosswalk:create.none.manualBtn')}
            </button>
            {onAddData && (
              <button type="button" className="btn btn--ghost" onClick={onAddData}>
                {t('crosswalk:create.none.addBtn')}
              </button>
            )}
          </div>
        </div>
      )}

      {candidates.length > 0 && (
        <div className="xw-cand-grid">
          {candidates.map((c) => (
            <CandidateCard
              key={c.id}
              candidate={c}
              onPick={() => pick(c)}
              onAdjust={() => onOpenManual(c)}
            />
          ))}
        </div>
      )}

      {/* Bounds the scan hit, said out loud: "nothing more to find" and "we stopped
          looking" must never look the same. */}
      {scanned?.candidates_truncated && (
        <p className="xw-hint-inline">
          {t('crosswalk:create.truncated', { shown: candidates.length })}
        </p>
      )}

      <div className="kz-actions">
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => onOpenManual()}>
          {t('crosswalk:create.bandManual')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
          {t('crosswalk:create.cancel')}
        </button>
      </div>
    </div>
  )
}

/** One candidate: what would connect, and the real values that prove it. */
function CandidateCard({
  candidate,
  onPick,
  onAdjust,
}: {
  candidate: DiscoverCandidate
  onPick: () => void
  onAdjust: () => void
}) {
  const { t } = useTranslation()
  const gain = foldingGain(candidate)
  const examples = candidate.samples.slice(0, 5)
  return (
    <div className="xw-cand-card">
      <div className="xw-cand-head">
        <span className="xw-cand-label">{conceptLabel(candidate.concept)}</span>
        <span className="xw-cand-count">
          {t('crosswalk:create.card.count', { count: candidate.matched })}
        </span>
      </div>

      {examples.length > 0 && (
        <>
          <p className="xw-cand-sub">{t('crosswalk:create.card.examplesHead')}</p>
          <div className="xw-cand-vals">
            {examples.map((s) => (
              <code className="xw-cand-val" key={s.key}>
                {Object.values(s.raw)[0] ?? s.key}
              </code>
            ))}
          </div>
        </>
      )}

      <p className="xw-cand-sub">{t('crosswalk:create.card.partHead')}</p>
      <div className="xw-cand-parts">
        {candidate.participants.map((p) => (
          <div className="xw-cand-part" key={p.dataset_id}>
            <span className="xw-cand-part-name">{p.name}</span>
            {/* The same value as each side actually spells it — the moment the
                candidate becomes obvious (Bi₂Te₃ here, Bi2Te3 there). */}
            {examples[0]?.raw[p.dataset_id] && (
              <span className="xw-cand-part-sample">
                {t('crosswalk:create.card.partSample', {
                  sample: examples[0].raw[p.dataset_id],
                })}
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Two sentences, never one nested in the other: the "what counts as the same"
          line is a full sentence, so interpolating it mid-clause reads as a run-on. */}
      <p className="xw-cand-note">{t(sameAsKey(candidate.normalizer))}</p>
      {gain && (
        <p className="xw-cand-note">
          {t('crosswalk:create.card.foldingGain', { strict: gain.strict, chosen: gain.chosen })}
        </p>
      )}

      {candidate.flags.map(flagKey).map(
        (key) =>
          key && (
            <p className="xw-cand-caution" key={key}>
              ⚠ {t(key)}
            </p>
          ),
      )}

      <div className="xw-cand-actions">
        <button type="button" className="xw-cand-pick" onClick={onPick}>
          {t('crosswalk:create.card.pick')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onAdjust}>
          {t('crosswalk:create.card.adjust')}
        </button>
      </div>
    </div>
  )
}
