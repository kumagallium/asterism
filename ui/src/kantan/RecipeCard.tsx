import { useTranslation } from 'react-i18next'

// The always-visible 5-step "recipe" of the kantan tier (ADR
// kantan-mode-two-tier-ux.md): ① put data in → ② AI reads it → ③ check →
// ④ try it → ⑤ publish. `current` lights the step the wizard is on (1-based);
// steps 4-5 (S7-S9 screens) are never current yet — after ③ is confirmed the
// wizard hands over to the dataset screen (`currentDone`).
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
}: {
  current: 1 | 2 | 3 | 4 | 5
  /** True renders `current` as ✓ done with no "you are here" badge — the state
   *  after ③ is confirmed while ④⑤ continue on the dataset screen (S6). */
  currentDone?: boolean
}) {
  const { t } = useTranslation()
  return (
    <ol className="kz-recipe" aria-label={t('kantan:recipe.label')}>
      {STEP_KEYS.map((key, i) => {
        const n = i + 1
        const active = n === current && !currentDone
        const done = n < current || (n === current && currentDone)
        return (
          <li
            key={key}
            className={`kz-recipe-step${active ? ' active' : ''}${done ? ' done' : ''}`}
            aria-current={active ? 'step' : undefined}
          >
            <span className="kz-recipe-num" aria-hidden="true">
              {done ? '✓' : n}
            </span>
            <span className="kz-recipe-text">{t(key)}</span>
            {active && <span className="kz-recipe-here">{t('kantan:recipe.here')}</span>}
          </li>
        )
      })}
    </ol>
  )
}
