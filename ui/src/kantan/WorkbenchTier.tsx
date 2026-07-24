import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { DetailTab } from '../GalleryView'
import { type RedesignTarget, WorkbenchView } from '../WorkbenchView'
import { KantanWizard } from './KantanWizard'

// Two-tier entry for データを追加 (ADR kantan-mode-two-tier-ux.md): the kantan
// wizard is the default; the full workbench remains untouched as the detail
// tier. The tiers are NEVER mounted together — WorkbenchView's mount-only job
// resume effect is tab-global, so double-mounting would double-resume.

const TIER_STORAGE = 'asterism.workbench.tier'
type Tier = 'kantan' | 'detail'

function loadTier(): Tier {
  try {
    return localStorage.getItem(TIER_STORAGE) === 'detail' ? 'detail' : 'kantan'
  } catch {
    return 'kantan'
  }
}

// Same sessionStorage key WorkbenchView/KantanWizard persist their in-flight
// LLM job under: while one is saved, switching tiers is locked (both tiers
// resume it on mount, so a mid-job switch could adopt a job of the wrong kind).
function hasSavedJob(): boolean {
  try {
    return sessionStorage.getItem('asterism.workbench.job') !== null
  } catch {
    return false
  }
}

export function WorkbenchTier({
  redesignTarget,
  onRedesignConsumed,
  onOpenDataset,
  onOpenAsk,
  onCreateCrosswalk,
}: {
  redesignTarget?: RedesignTarget | null
  onRedesignConsumed?: () => void
  onOpenDataset?: (id: string, tab?: DetailTab) => void
  /** Opens the Ask view with a question prefilled (the kantan S9 chips). */
  onOpenAsk?: (question: string) => void
  /** Opens the guided "connect your data" flow (offered on S9). */
  onCreateCrosswalk?: () => void
}) {
  const { t } = useTranslation()
  const [tier, setTier] = useState<Tier>(loadTier)
  const [kantanBusy, setKantanBusy] = useState(false)
  const [jobSaved, setJobSaved] = useState(hasSavedJob)
  // "構造から見直す" (kantan → detail): the wizard re-emits its current design
  // as a RedesignTarget so WorkbenchView opens it exactly like a catalog
  // redesign — same consumption path, dataset identity preserved.
  const [detailTarget, setDetailTarget] = useState<RedesignTarget | null>(null)

  // sessionStorage writes don't trigger renders — poll cheaply while mounted so
  // the toggle locks/unlocks as jobs start and finish on either tier.
  useEffect(() => {
    const id = window.setInterval(() => setJobSaved(hasSavedJob()), 1500)
    return () => window.clearInterval(id)
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(TIER_STORAGE, tier)
    } catch {
      /* non-fatal */
    }
  }, [tier])

  // A redesign (カタログの「見直す」) opens in the user's CURRENT tier — the
  // kantan re-check flow (S6 column meanings onward) is the default; the full
  // structural review stays one click away via the wizard's 構造から見直す.
  // People who built in the simple tier must not be dropped into the detail
  // workbench just to fix a column meaning.

  // The wizard hands over to the detail tier with its (possibly refined)
  // design as a redesign target. Adjust-during-render is not needed here —
  // this runs from a click handler.
  function reopenInDetail(target: RedesignTarget) {
    setDetailTarget(target)
    setTier('detail')
  }

  const locked = jobSaved || kantanBusy

  return (
    <div className="kz-tier">
      <div className="kz-tier-bar">
        <button
          type="button"
          className="btn btn--ghost btn--sm kz-tier-toggle"
          onClick={() => setTier(tier === 'kantan' ? 'detail' : 'kantan')}
          disabled={locked}
          title={locked ? t('kantan:tier.busy') : undefined}
        >
          {tier === 'kantan' ? t('kantan:tier.toDetail') : t('kantan:tier.toKantan')}
        </button>
      </div>
      {tier === 'kantan' ? (
        <KantanWizard
          onBusyChange={setKantanBusy}
          onHandoffToDetail={() => setTier('detail')}
          onOpenDataset={onOpenDataset}
          onOpenAsk={onOpenAsk}
          redesignTarget={redesignTarget}
          onRedesignConsumed={onRedesignConsumed}
          onRedesignDetail={reopenInDetail}
          onCreateCrosswalk={onCreateCrosswalk}
        />
      ) : (
        <WorkbenchView
          redesignTarget={redesignTarget ?? detailTarget}
          onRedesignConsumed={() => {
            onRedesignConsumed?.()
            setDetailTarget(null)
          }}
          onOpenDataset={onOpenDataset}
        />
      )}
    </div>
  )
}
