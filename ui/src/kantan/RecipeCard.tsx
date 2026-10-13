import { useTranslation } from 'react-i18next'

// The always-visible "recipe" of the kantan tier (ADR kantan-mode-two-tier-ux.md):
// ① put data in → ② AI reads it → ③ what the columns mean (+ the ☑ links) →
// ④ pick the number → ⑤ confirm the shape → ⑥ try it (S7) → ⑦ publish (S8/S9)
// (ADR skeleton-from-easy-judgments, upper-structure-shared-terms §2.5.2).
// `current` lights the step the wizard is on (1-based); `currentDone` renders it
// as ✓ instead — the S9 完了 state.
//
// ③ and ④ were one step ("確かめる") until a run showed why they cannot be: they
// are two of the ADR's human gates, they are two separate screens, and a
// single label left every "…に戻る" button pointing at a step whose name was not
// on the card. Each step now names exactly one screen (2026-08-19 review).
//
// ④ is asked only when a file has 2+ number candidates. Otherwise it is folded:
// the number stays in the bar (always seven steps) but is drawn faint, with a
// short note of what happened instead (`step4Fold`).
const STEP_KEYS = [
  'kantan:recipe.step1',
  'kantan:recipe.step2',
  'kantan:recipe.step3',
  'kantan:recipe.step4',
  'kantan:recipe.step5',
  'kantan:recipe.step6',
  'kantan:recipe.step7',
] as const

export type RecipeStep = 1 | 2 | 3 | 4 | 5 | 6 | 7

export function RecipeCard({
  current,
  currentDone = false,
  onStepClick,
  step4Fold,
}: {
  current: RecipeStep
  /** True renders `current` as ✓ done with no "you are here" badge — the
   *  state after publishing lands (S9: every step reads as done). */
  currentDone?: boolean
  /** When provided, step ① becomes a "back to the start" button (#9 escape
   *  hatch) — a guaranteed way back to the drop zone from any later step. ②+
   *  stay indicative (forward-only: no jumping to un-reached steps). Omitted →
   *  the recipe is purely a progress indicator (backward compatible). */
  onStepClick?: (step: RecipeStep) => void
  /** ④「番号を選ぶ」を畳んだ理由: `auto` = 候補が 1 つで自動で決まった／
   *  `placeholder` = 候補が無く仮置き（⑤で確認）。無ければ④は通常どおり。 */
  step4Fold?: 'auto' | 'placeholder'
}) {
  const { t } = useTranslation()
  return (
    <ol className="kz-recipe" aria-label={t('kantan:recipe.label')}>
      {STEP_KEYS.map((key, i) => {
        const n = (i + 1) as RecipeStep
        const active = n === current && !currentDone
        const done = n < current || (n === current && currentDone)
        // Minimal (#9): only ① is a live target, and only when we are past it.
        const clickable = !!onStepClick && n === 1 && !active
        const fold = n === 4 ? step4Fold : undefined
        const inner = (
          <>
            <span className="kz-recipe-num" aria-hidden="true">
              {done ? '✓' : n}
            </span>
            <span className="kz-recipe-text">{t(key)}</span>
            {fold && (
              <span className="kz-recipe-note" style={{ fontSize: '0.85em' }}>
                {t(fold === 'auto' ? 'kantan:recipe.step4Auto' : 'kantan:recipe.step4Placeholder')}
              </span>
            )}
            {active && <span className="kz-recipe-here">{t('kantan:recipe.here')}</span>}
            {/* Standing label, not a tooltip: the escape hatch looked exactly
                like the four indicative steps, so the only hint that ① could be
                pressed was a hover title no touch screen ever shows
                (KZ-A-34). */}
            {clickable && (
              <span className="kz-recipe-here">↺ {t('kantan:recipe.backToStartShort')}</span>
            )}
          </>
        )
        return (
          <li
            key={key}
            className={`kz-recipe-step${active ? ' active' : ''}${done ? ' done' : ''}${fold ? ' kz-recipe-step--folded' : ''}`}
            style={fold ? { opacity: 0.55 } : undefined}
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
