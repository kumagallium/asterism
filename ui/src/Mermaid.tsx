import mermaid from 'mermaid'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { normalizeMermaidDialects } from './mermaidNormalize'

// `suppressErrorRendering` stops mermaid from injecting its "bomb icon" error
// SVG into the DOM when a diagram fails to parse — we render our own readable
// fallback instead (dogfood 2026-07-08: AI-generated diagram.md that Mermaid 11
// rejects showed bomb icons across the catalog / workbench).
//
// テーマ: 既定の 'default'（紫）は forest 単一テーマと不調和なので、'base' に
// index.css のトークン実値を割り当てる（SVG 内へ焼き込まれるため CSS 変数は
// 使えない — 値はガイドライン §3 の表と同じもの）。
mermaid.initialize({
  startOnLoad: false,
  theme: 'base',
  suppressErrorRendering: true,
  themeVariables: {
    fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
    primaryColor: '#e5f0e6', // --entity-soft: クラス箱の地
    primaryBorderColor: '#c7d4c4', // --border-strong
    primaryTextColor: '#16241a', // --fg
    lineColor: '#54695b', // --muted: 関係線
    textColor: '#33453a', // --body
    tertiaryColor: '#f4f8f1', // --surface-alt: 補助面
  },
})

let _seq = 0

// Mermaid loads each diagram type as a lazy chunk, so `parse` can reject for a
// reason that has nothing to do with the diagram: the chunk import itself
// failed (typically a stale pre-deploy shell requesting asset hashes that no
// longer exist — observed live 2026-07-23, the ZEM 構造図). Match the three
// engines' dynamic-import failure messages so that case gets a "reload me"
// note instead of the misleading syntax fallback (which invites debugging a
// perfectly valid diagram). The self-heal reload in main.tsx usually fires
// first; this is the manual way out when its one-shot guard is spent.
// Chrome: "Failed to fetch dynamically imported module: <url>"
// Firefox: "error loading dynamically imported module: <url>"
// Safari:  "Importing a module script failed."
const CHUNK_LOAD_ERROR_RE = /dynamically imported module|Importing a module script/i

// Mermaid sizes every class box by MEASURING its label text, so it must not run
// before the font those labels will actually be drawn in has arrived. Ours
// (IBM Plex Mono, index.html → Google Fonts with `display=swap`) is a webfont:
// until it lands the browser paints the fallback, and the fallback is far
// narrower — measured live on the ZEM diagram, the same member line is 250px in
// the fallback vs 346px in IBM Plex Mono, so the whole diagram came out
// 345px wide where it needed 434px. `swap` then replaces the font inside boxes
// that were sized for the fallback: class names clip ("MaterialSamp") and long
// member lines wrap and centre — the "崩れ" the user reported.
//
// One shared promise: the wait resolves once per page, not once per diagram,
// and every later render (a catalog with several diagrams, a re-render on
// chart change) takes the already-resolved path. `document.fonts.ready`
// settles even when the font FAILS to load, so this cannot hang the render on
// an offline/blocked font host; the timeout is belt-and-braces for engines
// where the promise is unreliable, and losing the race only reproduces the old
// behaviour.
const FONT_WAIT_MS = 3000
let _fontsReady: Promise<unknown> | null = null

function fontsReady(): Promise<unknown> {
  const fonts = typeof document === 'undefined' ? undefined : document.fonts
  if (!fonts) return Promise.resolve()
  _fontsReady ??= Promise.race([
    // `load` forces the face to actually be fetched — `ready` alone can settle
    // before a font no element has used yet is requested. Size/weight here only
    // select the face; mermaid's own font-size governs the rendered metrics.
    Promise.resolve(fonts.load("400 16px 'IBM Plex Mono'"))
      .catch(() => undefined)
      .then(() => fonts.ready),
    new Promise((resolve) => setTimeout(resolve, FONT_WAIT_MS)),
  ])
  return _fontsReady
}

/**
 * Render a Mermaid diagram from its source. Falls back to the raw source on error.
 *
 * We PRE-VALIDATE with `mermaid.parse(...)` before calling `render`, so an
 * AI-generated diagram that Mermaid can't render never reaches `render` — we
 * degrade to a friendly note + the raw source, and no broken error graphic is
 * ever inserted into the page (`suppressErrorRendering` in the init above).
 * parse is called WITHOUT `suppressErrors`: that option collapses every
 * failure — a chunk that failed to load included — into the same `false`,
 * which turned a stale-deploy load failure into the misleading "syntax"
 * fallback (verified live 2026-07-23, ZEM 構造図). Classifying the thrown
 * error is the only way to tell the two apart.
 *
 * The rendered SVG is kept in state and injected via `dangerouslySetInnerHTML`
 * (React-owned), NOT imperatively via `ref.current.innerHTML`. Imperative DOM
 * mutation inside a React-controlled node can desync React's fiber tree and make
 * a later unmount throw "removeChild ... not a child" — letting React own the
 * markup keeps mount/unmount clean.
 */
export function Mermaid({ chart }: { chart: string }) {
  const { t } = useTranslation('misc')
  const [svg, setSvg] = useState<string | null>(null)
  const [failure, setFailure] = useState<'syntax' | 'load' | null>(null)

  useEffect(() => {
    let cancelled = false
    const id = `mermaid-${_seq++}`
    const normalized = normalizeMermaidDialects(chart)
    fontsReady()
      .then(() => mermaid.parse(normalized))
      .then(() => mermaid.render(id, normalized))
      .then((result) => {
        // setState only at the async boundary (react-hooks/set-state-in-effect).
        if (result && !cancelled) {
          setSvg(result.svg)
          setFailure(null)
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const message = err instanceof Error ? err.message : String(err)
        setFailure(CHUNK_LOAD_ERROR_RE.test(message) ? 'load' : 'syntax')
      })
    return () => {
      cancelled = true
    }
  }, [chart])

  if (failure === 'load') {
    // Not a diagram problem — don't show the source (that reads as "your
    // design is broken"); offer the fix instead.
    return (
      <div className="mermaid-fallback">
        <p className="mermaid-fallback-note">{t('mermaid.loadFailed')}</p>
        <button type="button" onClick={() => window.location.reload()}>
          {t('mermaid.reload')}
        </button>
      </div>
    )
  }
  if (failure === 'syntax') {
    return (
      <div className="mermaid-fallback">
        <p className="mermaid-fallback-note">{t('mermaid.renderFailed')}</p>
        <pre>{chart}</pre>
      </div>
    )
  }
  if (svg == null) {
    return <div className="mermaid" />
  }
  return <div className="mermaid" dangerouslySetInnerHTML={{ __html: svg }} />
}
