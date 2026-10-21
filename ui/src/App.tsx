// 殻は 3 領域のチャット（PR 11a・design.md §19）。左＝話の一覧／中央＝チャット／右＝図。
// 旧タブ（星図・質問・データ・知りたいこと・見る …）と左ナビ・上部バー・右下の相談ドロワーは外した
// （旧 component のファイルは残し、11e で消す）。ここに残るのは上の帯（更新・移った先・古い画面・止まった）と殻の差し込みだけ。
import { useEffect, useLayoutEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './App.css'
import { ChatApp } from './chat/ChatApp'
import { BackendDownBanner } from './desktop/BackendDownBanner'
import { UpdateBanner } from './desktop/UpdateBanner'

// 旧 component（cards/* など）が `import type { Route } from '../App'` で読む型。11e で旧 component と一緒に消す。
export type { Route } from './legacyRoute'

/**
 * The desktop shell adds `?port_fallback=1` to the window URL when its usual
 * entrance (port 8765) was taken by ANOTHER program, so this run is on a
 * different port and its settings will not be the ones the user set up before.
 * The shell already says so before launching; this is the reminder while working.
 * Nothing to decide here — so this is a note, not a dialog. (It never appears
 * when the other side is Asterism itself: the shell then reuses that window.)
 */
function PortFallbackBanner() {
  const { t } = useTranslation()
  const [shown, setShown] = useState(() => {
    try {
      return new URLSearchParams(window.location.search).get('port_fallback') === '1'
    } catch {
      return false
    }
  })
  if (!shown) return null
  return (
    <div className="update-banner" role="status" aria-live="polite">
      <span className="update-banner-text">{t('portFallback.text')}</span>
      <span className="update-banner-actions">
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setShown(false)}>
          {t('portFallback.dismiss')}
        </button>
      </span>
    </div>
  )
}

/**
 * Read (and immediately clear) the note main.tsx leaves before it reloads a tab
 * whose chunks a deploy replaced. Done once at module load, not inside the
 * component: StrictMode renders twice in dev, and a flag consumed in a state
 * initializer would be gone by the render React keeps.
 */
const STALE_CHUNK_RELOADED = (() => {
  try {
    if (sessionStorage.getItem('asterism.staleChunkNotice')) {
      sessionStorage.removeItem('asterism.staleChunkNotice')
      return true
    }
  } catch {
    /* no sessionStorage — nothing to announce */
  }
  return false
})()

/**
 * Why the screen just reset. A deploy swaps the hashed chunks under a tab that
 * is still running the pre-deploy shell, so main.tsx reloads once to pick up the
 * new one — which mid-wizard looks like the work disappearing. Say it happened,
 * once, then get out of the way. If a chunk still fails to load AFTER that
 * reload, the deploy really is broken: the same banner then says so and offers
 * the reload, instead of leaving buttons that quietly do nothing.
 */
function StaleChunkBanner() {
  const { t } = useTranslation()
  const [state, setState] = useState<'none' | 'reloaded' | 'failed'>(
    STALE_CHUNK_RELOADED ? 'reloaded' : 'none',
  )
  useEffect(() => {
    const onPreloadError = () => {
      // main.tsx's listener runs first (registered at module load). On the FIRST
      // failure it leaves the notice and reloads — a reload takes a moment,
      // during which this banner would sit there claiming the update failed. So
      // treat "a notice is pending" as "a reload is on its way" and stay quiet;
      // the note is consumed at the next load and becomes the 'reloaded' line.
      try {
        if (sessionStorage.getItem('asterism.staleChunkNotice')) return
      } catch {
        /* no sessionStorage — main.tsx never auto-reloads, so this IS the failure */
      }
      setState('failed')
    }
    window.addEventListener('vite:preloadError', onPreloadError)
    return () => window.removeEventListener('vite:preloadError', onPreloadError)
  }, [])
  if (state === 'none') return null
  const failed = state === 'failed'
  return (
    <div className="update-banner" role="status" aria-live="polite">
      <span className="update-banner-text">
        {failed ? t('staleChunk.failed') : t('staleChunk.reloaded')}
      </span>
      <span className="update-banner-actions">
        {failed ? (
          <button type="button" className="btn btn--sm" onClick={() => window.location.reload()}>
            {t('staleChunk.reload')}
          </button>
        ) : (
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setState('none')}>
            {t('staleChunk.dismiss')}
          </button>
        )}
      </span>
    </div>
  )
}

function App() {
  const { t, i18n } = useTranslation()

  // Keep the document title and <html lang> in sync with the chosen language.
  // Before paint (useLayoutEffect, not useEffect): index.html ships lang="ja",
  // so an English reader would otherwise have the first frame — what a screen
  // reader picks its voice from — announced as Japanese.
  useLayoutEffect(() => {
    document.title = t('docTitle')
    document.documentElement.lang = i18n.language.startsWith('en') ? 'en' : 'ja'
  }, [t, i18n.language])

  return (
    // .app-frame: 縦積み＝上に帯（更新・古い画面・止まった）、下に 3 領域の殻。
    <div className="app-frame">
      <StaleChunkBanner />
      <PortFallbackBanner />
      <UpdateBanner />
      <BackendDownBanner />
      <ChatApp />
    </div>
  )
}

export default App
