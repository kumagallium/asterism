import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './i18n'
import App from './App.tsx'
import { ErrorBoundary } from './ErrorBoundary.tsx'
import { SettingsProvider } from './settings/SettingsContext.tsx'

// Stale-deploy self-heal. A lazy chunk that fails to import mid-session almost
// always means a redeploy replaced the hashed assets while this tab kept the
// pre-deploy shell (observed live 2026-07-23: the ZEM 構造図 fell back to
// "図を表示できません" because the old mermaid classDiagram chunk no longer
// existed). Vite dispatches `vite:preloadError` for exactly this case — reload
// ONCE to pick up the new shell. The sessionStorage guard keeps a genuinely
// broken deploy from reload-looping; per-session (not per-URL) is deliberate:
// one deploy invalidates every chunk, and the tab's next successful load makes
// the stale flag irrelevant.
window.addEventListener('vite:preloadError', (event) => {
  const KEY = 'asterism.staleChunkReloaded'
  if (sessionStorage.getItem(KEY)) return // already tried — surface the failure
  sessionStorage.setItem(KEY, '1')
  event.preventDefault()
  window.location.reload()
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <SettingsProvider>
        <App />
      </SettingsProvider>
    </ErrorBoundary>
  </StrictMode>,
)
