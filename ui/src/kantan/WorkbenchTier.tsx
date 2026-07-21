import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
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
}: {
  redesignTarget?: RedesignTarget | null
  onRedesignConsumed?: () => void
  onOpenDataset?: (id: string) => void
}) {
  const { t } = useTranslation()
  const [tier, setTier] = useState<Tier>(loadTier)
  const [kantanBusy, setKantanBusy] = useState(false)
  const [jobSaved, setJobSaved] = useState(hasSavedJob)

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

  // A redesign (カタログの「見直す」) always opens the DETAIL tier — the stored
  // design review lives there. Adjust-during-render (same pattern as
  // WorkbenchView's seededTarget) so WorkbenchView mounts on this very render
  // pass and consumes the target.
  if (redesignTarget && tier !== 'detail') setTier('detail')

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
        />
      ) : (
        <WorkbenchView
          redesignTarget={redesignTarget}
          onRedesignConsumed={onRedesignConsumed}
          onOpenDataset={onOpenDataset}
        />
      )}
    </div>
  )
}
