import { useTranslation } from 'react-i18next'

// The always-visible 5-step "recipe" of the kantan tier (ADR
// kantan-mode-two-tier-ux.md): ① put data in → ② AI reads it → ③ check →
// ④ try it → ⑤ publish. `current` lights the step the wizard is on (1-based);
// steps 4-5 belong to later screens (S5+) and are never current in this PR.
const STEP_KEYS = [
  'kantan:recipe.step1',
  'kantan:recipe.step2',
  'kantan:recipe.step3',
  'kantan:recipe.step4',
  'kantan:recipe.step5',
] as const

export function RecipeCard({ current }: { current: 1 | 2 | 3 | 4 | 5 }) {
  const { t } = useTranslation()
  return (
    <ol className="kz-recipe" aria-label={t('kantan:recipe.label')}>
      {STEP_KEYS.map((key, i) => {
        const n = i + 1
        const active = n === current
        const done = n < current
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
