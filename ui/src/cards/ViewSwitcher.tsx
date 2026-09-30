// 「見せ方」の切替 UI（契約メモ §1.4）。固定表（`presentation.ts`）の外には
// 出ない — 候補が 1 つしか無い切替（マーク／縦横／色分け）は出さない。
import { useTranslation } from 'react-i18next'
import { allowedPresentations, currentPresentation } from './presentation'
import type { Presentation } from './presentation'
import type { Row, ToolContract } from './viewSpec'
import './view.css'

export interface ViewSwitcherProps {
  tool: ToolContract
  rows: Row[]
  presentation: Presentation | undefined
  onChange: (next: Presentation) => void
  onReset: () => void
}

export function ViewSwitcher({ tool, rows, presentation, onChange, onReset }: ViewSwitcherProps) {
  const { t } = useTranslation('cards')
  const options = allowedPresentations(tool, rows)
  const current = currentPresentation(tool, rows, presentation)
  const showMarks = options.marks.length > 1
  const showSwap = options.swapXY
  const showColor = options.colorBy !== null
  if (!current || (!showMarks && !showSwap && !showColor)) return null

  const patch = (next: Partial<Presentation>): Presentation => ({
    mark: current.mark,
    swapXY: current.swapXY,
    colorBy: current.colorBy,
    ...next,
  })

  return (
    <div className="view-switch">
      {showMarks && (
        <div className="view-switch-item">
          <span className="view-switch-label">{t('view.label')}</span>
          <div className="view-switch-group" role="group" aria-label={t('view.label')}>
            {options.marks.map((mark) => (
              <button
                key={mark}
                type="button"
                className={current.mark === mark ? 'view-switch-btn view-switch-btn--on' : 'view-switch-btn'}
                aria-pressed={current.mark === mark}
                onClick={() => onChange(patch({ mark }))}
              >
                {t(`view.${mark}`)}
              </button>
            ))}
          </div>
        </div>
      )}
      {showSwap && (
        <button
          type="button"
          className={current.swapXY ? 'view-switch-btn view-switch-btn--on' : 'view-switch-btn'}
          aria-pressed={current.swapXY}
          onClick={() => onChange(patch({ swapXY: !current.swapXY }))}
        >
          {t('view.swap')}
        </button>
      )}
      {showColor && options.colorBy && (
        <div className="view-switch-item">
          <span className="view-switch-label">{t('view.color_by')}</span>
          <div className="view-switch-group" role="group" aria-label={t('view.color_by')}>
            <button
              type="button"
              className={
                current.colorBy === options.colorBy.key ? 'view-switch-btn view-switch-btn--on' : 'view-switch-btn'
              }
              aria-pressed={current.colorBy === options.colorBy.key}
              onClick={() => onChange(patch({ colorBy: options.colorBy!.key }))}
            >
              {options.colorBy.label}
            </button>
            <button
              type="button"
              className={current.colorBy === null ? 'view-switch-btn view-switch-btn--on' : 'view-switch-btn'}
              aria-pressed={current.colorBy === null}
              onClick={() => onChange(patch({ colorBy: null }))}
            >
              {t('view.color_none')}
            </button>
          </div>
        </div>
      )}
      <button type="button" className="link-btn view-switch-reset" onClick={onReset}>
        {t('view.reset')}
      </button>
    </div>
  )
}
