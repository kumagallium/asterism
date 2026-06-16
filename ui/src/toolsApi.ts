// Client for the per-dataset *query-tools store* (the "検証済ツールを増やす仕組み").
//
// A dataset's typed, deterministic Ask tools live at registry/<id>/query_tools.yaml
// and are loaded by the same engine the repo example datasets use — so a tool
// SAVED here becomes a verified Ask/MCP tool for that dataset with no repo PR
// (P1). The workbench surfaces three moves over this store:
//
//   list/save/delete  GET/POST/DELETE /api/datasets/{id}/tools  (key-free; saving
//                     IS the human-vet gate — the backend re-validates read-only
//                     SELECT/ASK + safe {{placeholder}} binding, 400 if invalid).
//   propose           POST /api/datasets/{id}/tools/propose  (P2, key-gated): the
//                     LLM drafts ONE tool from a natural-language intent grounded
//                     in this dataset's vocabulary. The draft is RETURNED for human
//                     review/edit, never auto-saved.
//
// Same workbench API base as SparqlView / galleryApi (same-origin /api via the
// Vite proxy by default; VITE_API_URL overrides for separate hosting). Real data
// only — these go through /api (the proxy), so they are live even under the
// preview's mock demo mode (which only swaps the demo-agent surface).
import { authHeaders } from './authToken'
import { type LlmCredentials, llmHeaders } from './settings/store'
import i18n from './i18n'

const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

export type ParamType = 'string' | 'number' | 'integer' | 'iri' | 'enum'
export const PARAM_TYPES: ParamType[] = ['string', 'number', 'integer', 'iri', 'enum']

/** One declared parameter (a query_tools.yaml `parameters[]` entry). */
export interface QueryToolParam {
  name: string
  type: ParamType
  required?: boolean
  description?: string
  default?: string | number
  minimum?: number
  maximum?: number
  enum?: string[]
}

/** result.item: output_key -> SPARQL var name | {var, number}. */
export type ResultItem = Record<string, string | { var: string; number?: boolean }>

/** One declared query tool (a query_tools.yaml `tools[]` entry / the toolDict). */
export interface QueryTool {
  name: string
  title?: string
  description?: string
  parameters?: QueryToolParam[]
  query: string
  result?: { item?: ResultItem }
}

export interface ProposeResult {
  draft: QueryTool
  valid: boolean
  error: string | null
}

/** Result of running a saved tool: shaped rows + the read-only SPARQL it ran. */
export interface ToolRunResult {
  tool: string
  count: number
  items: Record<string, unknown>[]
  truncated: boolean
  sparql: string
}

async function asError(res: Response, op: string): Promise<Error> {
  // The api returns FastAPI's {detail: "..."}; fall back to raw text.
  const text = await res.text().catch(() => '')
  let detail = text
  try {
    const j = JSON.parse(text) as { detail?: unknown }
    if (j && typeof j.detail === 'string') detail = j.detail
  } catch {
    /* not JSON — keep raw text */
  }
  return new Error(`${op} failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
}

/** List a dataset's saved (verified) query tools. */
export async function listTools(datasetId: string): Promise<QueryTool[]> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/tools`)
  if (!res.ok) throw await asError(res, i18n.t('tools:error.ops.list'))
  return ((await res.json()) as { tools?: QueryTool[] }).tools ?? []
}

/** Save (upsert by name) one tool — the human-vet gate. 400 if the tool is invalid. */
export async function saveTool(datasetId: string, tool: QueryTool): Promise<QueryTool[]> {
  const res = await fetch(`${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/tools`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(tool),
  })
  if (!res.ok) throw await asError(res, i18n.t('tools:error.ops.save'))
  return ((await res.json()) as { tools?: QueryTool[] }).tools ?? []
}

/** Remove one tool by name. */
export async function deleteTool(datasetId: string, name: string): Promise<QueryTool[]> {
  const res = await fetch(
    `${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/tools/${encodeURIComponent(name)}`,
    { method: 'DELETE', headers: authHeaders() },
  )
  if (!res.ok) throw await asError(res, i18n.t('tools:error.ops.delete'))
  return ((await res.json()) as { tools?: QueryTool[] }).tools ?? []
}

/**
 * P2: ask the LLM (user-brought key, never stored) to draft ONE tool from a
 * natural-language intent, grounded in this dataset's vocabulary. The draft is
 * returned for human review/edit — it is NOT saved. `valid`/`error` report the
 * server-side parse_query_tools gate on the draft as-is.
 */
export async function proposeTool(
  datasetId: string,
  intent: string,
  creds: LlmCredentials | null,
): Promise<ProposeResult> {
  const res = await fetch(
    `${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/tools/propose`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...llmHeaders(creds), ...authHeaders() },
      body: JSON.stringify({ intent }),
    },
  )
  if (!res.ok) throw await asError(res, i18n.t('tools:error.ops.propose'))
  return (await res.json()) as ProposeResult
}

/**
 * Run a saved tool deterministically — typed, read-only, KEY-FREE (no LLM). The
 * server binds the typed args safely and runs over the canonical FROM-merge, the
 * same path the MCP surface exposes. Returns the shaped rows + the SPARQL it ran.
 */
export async function runTool(
  datasetId: string,
  name: string,
  args: Record<string, unknown>,
): Promise<ToolRunResult> {
  const res = await fetch(
    `${API_BASE}/api/datasets/${encodeURIComponent(datasetId)}/tools/${encodeURIComponent(name)}/run`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args }),
    },
  )
  if (!res.ok) throw await asError(res, i18n.t('tools:error.ops.run'))
  return (await res.json()) as ToolRunResult
}
