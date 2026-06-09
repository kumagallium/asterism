import { useEffect, useState } from 'react'
import { getCatalogDatasets } from './galleryApi'
import { ToolRunner } from './ToolRunner'
import { listTools, type QueryTool } from './toolsApi'

interface DatasetTools {
  id: string
  name: string
  tools: QueryTool[]
}

/**
 * Ask-side surface for the verified, deterministic tools: every dataset's saved
 * query tools, runnable RIGHT HERE with no API key and no LLM. This closes the
 * gap where a researcher's own saved tool only routed through the key-gated LLM
 * path — the typed/deterministic path (product_direction) should not need a key.
 * Real data only (via /api): the list comes from the live datasets + their stored
 * query_tools; running uses the same key-free /tools/{name}/run endpoint as the
 * catalog. Renders nothing when there are no verified tools (or the api is down).
 */
export function VerifiedTools() {
  const [groups, setGroups] = useState<DatasetTools[] | null>(null)
  const [openTool, setOpenTool] = useState<string | null>(null) // `${datasetId}::${name}`

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const datasets = (await getCatalogDatasets()).filter((d) => d.live)
        const groups = await Promise.all(
          datasets.map(async (d) => {
            const id = d.live!.meta.id
            try {
              return { id, name: d.name, tools: await listTools(id) }
            } catch {
              return { id, name: d.name, tools: [] as QueryTool[] }
            }
          }),
        )
        if (!cancelled) setGroups(groups.filter((g) => g.tools.length > 0))
      } catch {
        if (!cancelled) setGroups([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  if (!groups || groups.length === 0) return null

  const total = groups.reduce((n, g) => n + g.tools.length, 0)
  return (
    <details className="verified-tools">
      <summary>
        検証済みツールを直接実行（<strong>キー不要・LLM 不要</strong> · {total}）
      </summary>
      <p className="verified-tools-hint">
        人が検証した型付きツールを、自然文を介さず<strong>そのまま実行</strong>できます（決定論・引用つき・キー不要）。
      </p>
      {groups.map((g) => (
        <div key={g.id} className="verified-tools-group">
          <div className="ds-subhead">{g.name}</div>
          {g.tools.map((t) => {
            const key = `${g.id}::${t.name}`
            const open = openTool === key
            return (
              <div key={t.name} className="tool-card">
                <div className="tool-card-head">
                  <code className="tool-name">{t.name}</code>
                  {t.title && <span className="tool-title">{t.title}</span>}
                  <span className="tool-card-actions">
                    <button
                      type="button"
                      className="btn btn--soft btn--sm"
                      onClick={() => setOpenTool(open ? null : key)}
                    >
                      {open ? '閉じる' : '実行'}
                    </button>
                  </span>
                </div>
                {t.description && <p className="tool-desc">{t.description}</p>}
                {open && <ToolRunner datasetId={g.id} tool={t} />}
              </div>
            )
          })}
        </div>
      ))}
    </details>
  )
}
