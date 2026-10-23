import { authHeaders } from '../authToken'

// 他のクライアントと同じ API ベース（既定は同一オリジン /api・別ホスト配備は VITE_API_URL）
const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? '').replace(/\/+$/, '')

// MCP サーバ設定タブの HTTP 口（api/src/asterism_api/mcp_client_routes.py）。
// 設定の書き込みはここではなく appSettings.putAppSetting だけが行う。

export interface McpServerInfo {
  name: string
  transport: 'stdio' | 'http'
  enabled: boolean
  command?: string
  args?: string[]
  env?: Record<string, string>
  cwd?: string
  url?: string
}

export interface SourceProviderNames {
  list: string
  get: string
  add: string
}

export interface McpRecentCall {
  server: string
  tool: string
  ok: boolean
  count: number | null
  at: string
  error: string | null
}

export interface McpServersResponse {
  servers: McpServerInfo[]
  invalid: { name: string; error: string }[]
  source_home: string | null
  source_provider: SourceProviderNames
  send_originals_to_home: boolean
  max_source_file_bytes: number
  recent: McpRecentCall[]
}

export interface McpToolInfo {
  name: string
  description?: string | null
  input_schema?: unknown
}

async function failure(res: Response): Promise<Error> {
  let detail = `HTTP ${res.status}`
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail) detail = body.detail
  } catch {
    // 本文が JSON でなければ HTTP 状態のまま
  }
  return new Error(detail)
}

export async function fetchMcpServers(): Promise<McpServersResponse> {
  const res = await fetch(`${API_BASE}/api/mcp-servers`, { headers: { ...authHeaders() } })
  if (!res.ok) throw await failure(res)
  return (await res.json()) as McpServersResponse
}

export async function fetchMcpServerTools(name: string): Promise<McpToolInfo[]> {
  const res = await fetch(`${API_BASE}/api/mcp-servers/${encodeURIComponent(name)}/tools`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: '{}',
  })
  if (!res.ok) throw await failure(res)
  const data = (await res.json()) as { tools?: McpToolInfo[] }
  return data.tools ?? []
}

/** node など実行ファイルの場所を探す（読むだけ・起動しない）。単一ユーザーでなければ 404。 */
export async function locateMcpCommand(
  command: string,
  candidates: string[],
): Promise<{ found: string | null; user_home: string }> {
  const res = await fetch(`${API_BASE}/api/mcp-servers/locate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ command, candidates }),
  })
  if (!res.ok) throw await failure(res)
  return (await res.json()) as { found: string | null; user_home: string }
}
