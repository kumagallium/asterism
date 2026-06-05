import mermaid from 'mermaid'
import { useEffect, useState } from 'react'

mermaid.initialize({ startOnLoad: false, theme: 'default' })

let _seq = 0

/**
 * Render a Mermaid diagram from its source. Falls back to the raw source on error.
 *
 * The rendered SVG is kept in state and injected via `dangerouslySetInnerHTML`
 * (React-owned), NOT imperatively via `ref.current.innerHTML`. Imperative DOM
 * mutation inside a React-controlled node can desync React's fiber tree and make
 * a later unmount throw "removeChild ... not a child" — letting React own the
 * markup keeps mount/unmount clean.
 */
export function Mermaid({ chart }: { chart: string }) {
  const [svg, setSvg] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    const id = `mermaid-${_seq++}`
    mermaid
      .render(id, chart)
      .then(({ svg }) => {
        // setState only at the async boundary (react-hooks/set-state-in-effect).
        if (!cancelled) {
          setSvg(svg)
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
    return <pre className="mermaid-fallback">{chart}</pre>
  }
  if (svg == null) {
    return <div className="mermaid" />
  }
  return <div className="mermaid" dangerouslySetInnerHTML={{ __html: svg }} />
}
