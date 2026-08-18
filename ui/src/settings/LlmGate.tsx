import { useTranslation } from 'react-i18next'
import './LlmGate.css'
import { useLlmSettings } from './context'

// Replaces the per-view API-key inputs: says AI is not set up yet (with a way to
// fix that), or — in the detailed views that want it — which model is running.
//
// `plain` is for the shared screens: once AI works there is nothing for the user
// to decide, so the ready state renders nothing at all rather than a standing
// reminder of model names. The unset state always renders: that one is the
// user's next step, on every screen.
export function LlmGate({ plain = false }: { plain?: boolean }) {
  const { t } = useTranslation('settings')
  const { isReady, activeModel, activeUsesServerKey, openSettings } = useLlmSettings()

  if (isReady) {
    if (plain) return null
    return (
      <div className="llm-gate llm-gate--ok">
        <span className="llm-gate-dot" aria-hidden="true" />
        <span className="llm-gate-text">
          {activeModel ? (
            <>
              {t('gate.using')}
              <strong className="llm-gate-model">{activeModel.name}</strong>
            </>
          ) : (
            t('gate.usingServerSetup')
          )}
          {activeModel && activeUsesServerKey && (
            <span className="llm-gate-serverkey">{t('gate.serverKey')}</span>
          )}
        </span>
        <button type="button" className="llm-gate-link" onClick={() => openSettings('ai')}>
          {t('gate.change')}
        </button>
      </div>
    )
  }

  return (
    <div className="llm-gate llm-gate--warn">
      <span className="llm-gate-text">{t('gate.notSetUp')}</span>
      <button type="button" className="btn llm-gate-cta" onClick={() => openSettings('ai')}>
        {t('gate.openSettings')}
      </button>
    </div>
  )
}
