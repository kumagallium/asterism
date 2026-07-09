import mermaid from 'mermaid'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { normalizeMermaidDialects } from './mermaidNormalize'

// `suppressErrorRendering` stops mermaid from injecting its "bomb icon" error
// SVG into the DOM when a diagram fails to parse — we render our own readable
// fallback instead (dogfood 2026-07-08: AI-generated diagram.md that Mermaid 11
// rejects showed bomb icons across the catalog / workbench).
mermaid.initialize({ startOnLoad: false, theme: 'default', suppressErrorRendering: true })

let _seq = 0

/**
 * Render a Mermaid diagram from its source. Falls back to the raw source on error.
 *
 * We PRE-VALIDATE with `mermaid.parse(..., { suppressErrors: true })` before
 * calling `render`. `parse` resolves to `false` on invalid syntax instead of
 * throwing, so an AI-generated diagram that Mermaid can't render never reaches
 * `render` — we degrade to a friendly note + the raw source, and no broken
 * error graphic is ever inserted into the page.
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
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    const id = `mermaid-${_seq++}`
    const normalized = normalizeMermaidDialects(chart)
    mermaid
      .parse(normalized, { suppressErrors: true })
      .then((ok) => {
        // Invalid syntax — skip render() entirely so mermaid never injects its
        // error graphic; the fallback below shows the source instead.
        if (!ok) {
          if (!cancelled) setFailed(true)
          return undefined
        }
        return mermaid.render(id, normalized)
      })
      .then((result) => {
        // setState only at the async boundary (react-hooks/set-state-in-effect).
        if (result && !cancelled) {
          setSvg(result.svg)
          setFailed(false)
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [chart])

  if (failed) {
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
