import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './i18n'
import App from './App.tsx'
import { initUpdater } from './desktop/updater.ts'
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
//
// The reload is never silent: it can land mid-wizard (the AI is narrating, an
// ingest is running), where a screen that resets with no explanation reads as
// "my work vanished". So leave a note for the next load — App's StaleChunkBanner
// shows it once and clears it. A second failure means the deploy really is
// broken; the same banner then says so instead of the tab quietly doing nothing.
window.addEventListener('vite:preloadError', (event) => {
  const KEY = 'asterism.staleChunkReloaded'
  const NOTICE = 'asterism.staleChunkNotice'
  try {
    if (sessionStorage.getItem(KEY)) return // already tried — the banner explains it
    sessionStorage.setItem(KEY, '1')
    sessionStorage.setItem(NOTICE, '1')
  } catch {
    return // no sessionStorage means no loop guard — never auto-reload blind
  }
  event.preventDefault()
  window.location.reload()
})

// デスクトップ版（Tauri の窓）だけ: 起動 5 秒後と 24 時間ごとに更新を確認し、
// 見つかれば画面上部の UpdateBanner に出す。ブラウザ/web 配備では no-op。
initUpdater()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <SettingsProvider>
        <App />
      </SettingsProvider>
    </ErrorBoundary>
  </StrictMode>,
)
