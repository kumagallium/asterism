import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { DetailTab } from '../GalleryView'
import { type RedesignTarget, WorkbenchView } from '../WorkbenchView'
import { type GrowFocus, KantanWizard } from './KantanWizard'

// Two-tier entry for データを追加 (ADR kantan-mode-two-tier-ux.md): the kantan
// wizard is the default; the full workbench remains untouched as the detail
// tier. The tiers are NEVER mounted together — WorkbenchView's mount-only job
// resume effect is tab-global, so double-mounting would double-resume.

// sessionStorage, NOT localStorage: an explicit switch holds for the rest of
// this visit, and the next visit always starts in かんたんモード (ADR K1). One
// curious click on 詳細モードへ used to make the expert workbench the permanent
// default for 「データを追加」 and for the catalog's 「見直す」 (DETAIL-GAP-01).
const TIER_STORAGE = 'asterism.workbench.tier'
type Tier = 'kantan' | 'detail'

function loadTier(): Tier {
  try {
    return sessionStorage.getItem(TIER_STORAGE) === 'detail' ? 'detail' : 'kantan'
  } catch {
    return 'kantan'
  }
}

// Same sessionStorage keys WorkbenchView/KantanWizard persist their in-flight
// LLM job under: while one is saved, switching tiers is locked (both tiers
// resume it on mount, so a mid-job switch could adopt a job of the wrong kind).
// The second key is the kantan tier's own (its S3 skeleton job and its AI fix /
// reflect, which the detail tier must never adopt); both are duplicated by
// value, as before.
function hasSavedJob(): boolean {
  try {
    return (
      sessionStorage.getItem('asterism.workbench.job') !== null ||
      sessionStorage.getItem('asterism.kantan.job') !== null
    )
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
  /** `focus` says which of S9's two grow exits was taken (add / replace), so
   *  the catalog can land on that control; receivers may ignore it. */
  onOpenDataset?: (id: string, tab?: DetailTab, focus?: GrowFocus) => void
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
  // The wizard fills this with its "prepare the detail-tier handoff" writer.
  // The toggle below switches tiers without going through the wizard's own
  // 詳細モードで確認する, so it has to run the same preparation — the snapshot is
  // no longer written on every design change (DETAIL-GAP-V3).
  const handoffRef = useRef<(() => boolean) | null>(null)

  function toggleTier() {
    if (tier === 'detail') {
      setTier('kantan')
      return
    }
    // The writer answers false when the reader declined to replace unfinished
    // work saved in the detail tier: staying put is then the whole point.
    if (handoffRef.current && !handoffRef.current()) return
    setTier('detail')
  }

  // sessionStorage writes don't trigger renders — poll cheaply while mounted so
  // the toggle locks/unlocks as jobs start and finish on either tier.
  useEffect(() => {
    const id = window.setInterval(() => setJobSaved(hasSavedJob()), 1500)
    return () => window.clearInterval(id)
  }, [])

  useEffect(() => {
    try {
      sessionStorage.setItem(TIER_STORAGE, tier)
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
        <div className="kz-actions">
          {/* Standing sentences, not hover titles. The detail tier opens on
              conversion rules and check tables; whoever came down from the
              wizard has to be told, on screen, that the way back is right
              there (DETAIL-GAP-03). And a toggle that refuses to move says
              why — a `title` is invisible on a touch screen, so the button
              just looked broken (DETAIL-GAP-17). */}
          {tier === 'detail' && <p className="kz-note">{t('kantan:tier.detailStanding')}</p>}
          {locked && <p className="kz-note">{t('kantan:tier.busy')}</p>}
          <button
            type="button"
            className="btn btn--ghost btn--sm kz-tier-toggle"
            onClick={toggleTier}
            disabled={locked}
            // Say what is on the other side and that it is reversible: "詳細"
            // alone reads as "more about MY data" to a first-timer
            // (DETAIL-GAP-02).
            title={
              locked
                ? t('kantan:tier.busy')
                : tier === 'kantan'
                  ? t('kantan:tier.detailHint')
                  : t('kantan:tier.kantanHint')
            }
          >
            {tier === 'kantan' ? t('kantan:tier.toDetail') : t('kantan:tier.toKantan')}
          </button>
        </div>
      </div>
      {tier === 'kantan' ? (
        <KantanWizard
          onBusyChange={setKantanBusy}
          onHandoffToDetail={() => setTier('detail')}
          handoffRef={handoffRef}
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
