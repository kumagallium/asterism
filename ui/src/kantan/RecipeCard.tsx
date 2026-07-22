import { useTranslation } from 'react-i18next'

// The always-visible 5-step "recipe" of the kantan tier (ADR
// kantan-mode-two-tier-ux.md): ① put data in → ② AI reads it → ③ check →
// ④ try it (S7) → ⑤ publish (S8/S9). `current` lights the step the wizard is
// on (1-based); `currentDone` renders it as ✓ instead — the S9 完了 state.
const STEP_KEYS = [
  'kantan:recipe.step1',
  'kantan:recipe.step2',
  'kantan:recipe.step3',
  'kantan:recipe.step4',
  'kantan:recipe.step5',
] as const

export function RecipeCard({
  current,
  currentDone = false,
  onStepClick,
}: {
  current: 1 | 2 | 3 | 4 | 5
  /** True renders `current` as ✓ done with no "you are here" badge — the
   *  state after publishing lands (S9: all five steps read as done). */
  currentDone?: boolean
  /** When provided, step ① becomes a "back to the start" button (#9 escape
   *  hatch) — a guaranteed way back to the drop zone from any later step. ②+
   *  stay indicative (forward-only: no jumping to un-reached steps). Omitted →
   *  the recipe is purely a progress indicator (backward compatible). */
  onStepClick?: (step: 1 | 2 | 3 | 4 | 5) => void
}) {
  const { t } = useTranslation()
  return (
    <ol className="kz-recipe" aria-label={t('kantan:recipe.label')}>
      {STEP_KEYS.map((key, i) => {
        const n = (i + 1) as 1 | 2 | 3 | 4 | 5
        const active = n === current && !currentDone
        const done = n < current || (n === current && currentDone)
        // Minimal (#9): only ① is a live target, and only when we are past it.
        const clickable = !!onStepClick && n === 1 && !active
        const inner = (
          <>
            <span className="kz-recipe-num" aria-hidden="true">
              {done ? '✓' : n}
            </span>
            <span className="kz-recipe-text">{t(key)}</span>
            {active && <span className="kz-recipe-here">{t('kantan:recipe.here')}</span>}
          </>
        )
        return (
          <li
            key={key}
            className={`kz-recipe-step${active ? ' active' : ''}${done ? ' done' : ''}`}
            aria-current={active ? 'step' : undefined}
          >
            {clickable ? (
              <button
                type="button"
                className="kz-recipe-btn"
                onClick={() => onStepClick?.(n)}
                title={t('kantan:recipe.backToStart')}
              >
                {inner}
              </button>
            ) : (
              inner
            )}
          </li>
        )
      })}
    </ol>
  )
}
