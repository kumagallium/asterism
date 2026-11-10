import { useTranslation } from 'react-i18next'
import { setAppearance, useAppearance } from '../appearance'

export function AppearanceSelect() {
  const { t } = useTranslation('settings')
  const appearance = useAppearance()
  return (
    <label className="settings-appearance">
      <span>{t('appearance.label')}</span>
      <select value={appearance} onChange={(event) => setAppearance(event.target.value === 'light' ? 'light' : 'dark')}>
        <option value="dark">{t('appearance.dark')}</option>
        <option value="light">{t('appearance.light')}</option>
      </select>
    </label>
  )
}
