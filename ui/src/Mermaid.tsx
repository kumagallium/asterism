import mermaid from 'mermaid'
import { useEffect, useRef, useState } from 'react'

mermaid.initialize({ startOnLoad: false, theme: 'default' })

let _seq = 0

/** Render a Mermaid diagram from its source. Falls back to the raw source on error. */
export function Mermaid({ chart }: { chart: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    const id = `mermaid-${_seq++}`
    mermaid
      .render(id, chart)
      .then(({ svg }) => {
        if (!cancelled && ref.current) {
          ref.current.innerHTML = svg
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
  return <div className="mermaid" ref={ref} />
}
