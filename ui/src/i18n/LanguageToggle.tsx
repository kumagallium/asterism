import { useTranslation } from 'react-i18next'
import { SUPPORTED_LNGS } from './index'

// 日本語ファーストの言語トグル。ja/en の2値。選択は LanguageDetector が
// localStorage('asterism.lang') に保存するので、リロードしても保たれる。
export function LanguageToggle() {
  const { i18n, t } = useTranslation()
  const current = i18n.language.startsWith('en') ? 'en' : 'ja'

  return (
    <div className="lang-toggle" role="group" aria-label={t('langToggle.label')}>
      {SUPPORTED_LNGS.map((lng) => (
        <button
          key={lng}
          type="button"
          className={`lang-toggle-btn${current === lng ? ' active' : ''}`}
          aria-pressed={current === lng}
          onClick={() => {
            if (current !== lng) i18n.changeLanguage(lng)
          }}
        >
          {t(`langToggle.${lng}`)}
        </button>
      ))}
    </div>
  )
}
