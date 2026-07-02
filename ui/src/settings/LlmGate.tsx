import { useTranslation } from 'react-i18next'
import './LlmGate.css'
import { useLlmSettings } from './context'

// Replaces the per-view API-key inputs: shows which model is active (with a link
// to change it) when ready, or a "configure a model" prompt that opens settings
// when not. Used by every LLM-invoking view so keys live in one place.
export function LlmGate() {
  const { t } = useTranslation('settings')
  const { isReady, activeModel, activeUsesServerKey, openSettings } = useLlmSettings()

  if (isReady && activeModel) {
    return (
      <div className="llm-gate llm-gate--ok">
        <span className="llm-gate-dot" aria-hidden="true" />
        <span className="llm-gate-text">
          {t('gate.using')}
          <strong className="llm-gate-model">{activeModel.name}</strong>
          {activeUsesServerKey && (
            <span className="llm-gate-serverkey">{t('gate.serverKey')}</span>
          )}
        </span>
        <button type="button" className="llm-gate-link" onClick={openSettings}>
          {t('gate.change')}
        </button>
      </div>
    )
  }

  return (
    <div className="llm-gate llm-gate--warn">
      <span className="llm-gate-text">
        {activeModel ? t('gate.noKey') : t('gate.noModel')}
      </span>
      <button type="button" className="btn llm-gate-cta" onClick={openSettings}>
        {t('gate.openSettings')}
      </button>
    </div>
  )
}
