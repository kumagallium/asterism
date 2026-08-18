import { useCallback, useEffect, useRef, useState } from 'react'
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
import {
  askQuestionsFor,
  conceptDisplay,
  crosswalkError,
  flagKey,
  foldingGain,
  sameAsKey,
} from './crosswalkLabels'
import { uniqueCrosswalkId } from './crosswalkMint'
import { useLlmSettings } from './settings/context'

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
  /** A connection was built: the parent reloads its list and selects the new one (so
   * "see this connection" shows the one just made, not whatever was open before). */
  onBuilt: (perspectiveId: string) => void
  /** Escape hatch to the detail tier, optionally seeded with a candidate. */
  onOpenManual: (candidate?: DiscoverCandidate) => void
  onAddData?: () => void
  onOpenAsk?: (question: string) => void
}) {
  const { t } = useTranslation()
  const { openSettings } = useLlmSettings()
  const [phase, setPhase] = useState<Phase>('scanning')
  const [result, setResult] = useState<DiscoverResult | null>(null)
  const [scanErr, setScanErr] = useState('')
  const [progress, setProgress] = useState('')
  const [picked, setPicked] = useState<DiscoverCandidate | null>(null)
  const [name, setName] = useState('')
  const [buildErr, setBuildErr] = useState('')
  const [built, setBuilt] = useState<BuildResult | null>(null)
  const job = useRef<JobHandle | null>(null)
  const gone = useRef(false)

  /** Start the scan. Stable and free of synchronous state writes, so the mount effect
   * and the "search again" button can share one path. */
  const beginScan = useCallback(() => {
    gone.current = false
    job.current?.cancel().catch(() => job.current?.close())
    job.current = null
    discoverCrosswalks({
      onDone: (r) => {
        if (gone.current) return
        setResult(r)
        setPhase('pick')
      },
      onError: (m) => {
        if (gone.current) return
        setScanErr(m)
        setPhase('pick')
      },
      onRunning: (data) => {
        if (gone.current) return
        const nameOf = typeof data.name === 'string' ? data.name : ''
        setProgress(nameOf)
      },
    })
      .then((h) => {
        if (gone.current) h.cancel().catch(() => h.close())
        else job.current = h
      })
      .catch((e) => {
        if (gone.current) return
        setScanErr(e instanceof Error ? e.message : String(e))
        setPhase('pick')
      })
  }, [])

  // Scan on mount. The handle is closed on unmount so leaving the screen stops the
  // stream (the server-side job also cancels — nobody waits for a scan nobody reads).
  useEffect(() => {
    beginScan()
    return () => {
      gone.current = true
      job.current?.cancel().catch(() => job.current?.close())
      job.current = null
    }
  }, [beginScan])

  /** "Search again": a scan that failed for a passing reason (a blip, a slow store)
   * must be retryable without leaving the screen. */
  function rescan() {
    setScanErr('')
    setProgress('')
    setResult(null)
    setPhase('scanning')
    beginScan()
  }

  const existingIds = perspectives.map((p) => p.perspective_id)
  const candidates = (result?.candidates ?? [])
    .filter((c) => (c.participants?.length ?? 0) >= 2)
    .sort((a, b) => b.matched - a.matched)
  const scanned = result?.scanned

  /** What this candidate connects on, in words — the label when there is one, else
   * "the value found in both". The ascii key never reaches the screen. */
  function labelOf(c: DiscoverCandidate): string {
    return conceptDisplay(c) ?? t('crosswalk:create.sharedValueLabel')
  }

  function pick(c: DiscoverCandidate) {
    setPicked(c)
    // The default name is derived, not asked for (K13): the field's own words when we
    // have them, the two datasets' names when we don't.
    const label = conceptDisplay(c)
    setName(
      label
        ? t('crosswalk:create.defaultName', { label })
        : t('crosswalk:create.defaultNamePair', {
            a: c.participants[0]?.name ?? '',
            b: c.participants[1]?.name ?? '',
          }),
    )
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
      onBuilt(id)
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
    // A participant that dropped out (unpublished / withdrawn since) is SAID, not
    // silently subtracted from the count.
    const skipped = built.participants_skipped
      .map((s) => picked.participants.find((p) => p.dataset_id === s.dataset_id)?.name || s.label)
      .filter(Boolean)
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
          {skipped.length > 0 && (
            <p className="kz-note kz-caution">
              {t('crosswalk:create.done.skipped', {
                names: skipped.join(t('crosswalk:create.confirm.join')),
              })}
            </p>
          )}
          {onOpenAsk && (
            <>
              <p className="kz-note">{t('crosswalk:create.done.askLead')}</p>
              <div className="kz-q-options">
                {askQuestionsFor(picked, labelOf(picked)).map((q) => {
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
    const err = buildErr ? crosswalkError(buildErr) : null
    return (
      <div className="xw-create">
        <section className="kz-card">
          <h3 className="kz-title">{t('crosswalk:create.confirm.title')}</h3>

          <div className="xw-confirm-row">
            <span className="xw-confirm-label">{t('crosswalk:create.confirm.whatHead')}</span>
            <span className="xw-confirm-value">
              {picked.participants
                .map((p) =>
                  p.predicate_label
                    ? t('crosswalk:create.confirm.partField', {
                        name: p.name,
                        field: p.predicate_label,
                      })
                    : p.name,
                )
                .join(t('crosswalk:create.confirm.join'))}
            </span>
          </div>
          <div className="xw-confirm-row">
            <span className="xw-confirm-label">{t('crosswalk:create.confirm.onHead')}</span>
            <span className="xw-confirm-value" title={picked.concept}>
              {labelOf(picked)}
            </span>
          </div>
          <div className="xw-confirm-row">
            <span className="xw-confirm-label">{t('crosswalk:create.confirm.matchHead')}</span>
            <span className="xw-confirm-value">
              {t('crosswalk:create.card.count', { count: picked.matched })}
            </span>
          </div>

          <p className="kz-note">{t(sameAsKey(picked.normalizer))}</p>

          {/* The cautions from the card, repeated where the claim is actually
              confirmed — this is the screen where they change someone's mind. */}
          {picked.flags.map(flagKey).map(
            (key) =>
              key && (
                <p className="kz-note kz-caution" key={key}>
                  ⚠ {t(key)}
                </p>
              ),
          )}

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
            <div className="state-block">
              <p className="state-title">{t(err.title)}</p>
              <p className="state-sub">{t(err.body)}</p>
              {err.hint === 'settings' && (
                <div className="kz-actions">
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => openSettings('server-token')}
                  >
                    {t('crosswalk:create.settingsBtn')}
                  </button>
                </div>
              )}
              <details className="kz-stop-detail">
                <summary>{t('crosswalk:create.details')}</summary>
                <pre className="error">{buildErr}</pre>
              </details>
            </div>
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
  const scanPlain = scanErr ? crosswalkError(scanErr) : null
  return (
    <div className="xw-create">
      {/* The heading and its instruction belong to a list of candidates. With none on
          screen they contradict the state block right below them, so they only show
          when there is actually something to pick. */}
      {candidates.length > 0 && (
        <>
          <div className="ds-subhead">{t('crosswalk:create.head')}</div>
          <p className="kz-note">{t('crosswalk:create.lead')}</p>
        </>
      )}

      {scanPlain && (
        <div className="state-block">
          <p className="state-title">{t(scanPlain.title)}</p>
          <p className="state-sub">{t(scanPlain.body)}</p>
          <div className="kz-actions">
            <button type="button" onClick={rescan}>
              {t('crosswalk:create.failed.retryBtn')}
            </button>
            {scanPlain.hint === 'settings' && (
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => openSettings('server-token')}
              >
                {t('crosswalk:create.settingsBtn')}
              </button>
            )}
            <button type="button" className="btn btn--ghost" onClick={() => onOpenManual()}>
              {t('crosswalk:create.failed.manualBtn')}
            </button>
            <button type="button" className="btn btn--ghost" onClick={onCancel}>
              {t('crosswalk:create.cancel')}
            </button>
          </div>
          <details className="kz-stop-detail">
            <summary>{t('crosswalk:create.details')}</summary>
            <pre className="error">{scanErr}</pre>
          </details>
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

      {/* Nothing overlapped. The next step that actually helps is MORE data — the
          manual form asks for a concept key, a normalizer and a name, which is the
          detail tier, so it stays available but stops being the recommended move. */}
      {!scanErr && !tooFew && candidates.length === 0 && (
        <div className="state-block">
          <p className="state-title">{t('crosswalk:create.none.title')}</p>
          <p className="state-sub">{t('crosswalk:create.none.sub')}</p>
          <div className="kz-actions">
            {onAddData && (
              <button type="button" onClick={onAddData}>
                {t('crosswalk:create.none.addBtn')}
              </button>
            )}
            <button type="button" className="btn btn--ghost" onClick={() => onOpenManual()}>
              {t('crosswalk:create.none.manualBtn')}
            </button>
            <button type="button" className="btn btn--ghost" onClick={onCancel}>
              {t('crosswalk:create.cancel')}
            </button>
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

      {/* Only when there are candidates: every other state carries its own single
          primary action, and a second "choose the combination myself" underneath it
          made two identical buttons compete on one screen. */}
      {candidates.length > 0 && (
        <div className="kz-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => onOpenManual()}>
            {t('crosswalk:create.bandManual')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
            {t('crosswalk:create.cancel')}
          </button>
        </div>
      )}
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
        <span className="xw-cand-label" title={candidate.concept}>
          {conceptDisplay(candidate) ?? t('crosswalk:create.sharedValueLabel')}
        </span>
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
            {/* WHICH field, and the same value as each side actually spells it — the
                moment the candidate becomes obvious (Bi₂Te₃ here, Bi2Te3 there), and
                the only way to notice the wrong column was picked. */}
            {examples[0]?.raw[p.dataset_id] && (
              <span className="xw-cand-part-sample" title={p.predicate}>
                {p.predicate_label
                  ? t('crosswalk:create.card.partField', {
                      field: p.predicate_label,
                      sample: examples[0].raw[p.dataset_id],
                    })
                  : t('crosswalk:create.card.partSample', {
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
