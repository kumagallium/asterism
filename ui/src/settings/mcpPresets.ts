import type { McpServerEntry } from './mcpServerForm'
import type { SourceProviderNames } from './mcpServersApi'

// 取り込み元のプリセット。表示から切り離したデータ（connectRecipes.ts と同じ性質）。
// Asterism 本体にアプリ固有のコードは置かず、ここに名前と道具名だけを持つ。
// 値の保存は McpServersTab の「つなぐ」を押したときだけ。

export interface McpPreset {
  id: string
  serverName: string
  command: string
  /** node の置き場所の候補（~ は展開できないので含めない） */
  nodeCandidates: string[]
  defaultRoot: (userHome: string) => string
  envName: string
  sourceProvider: SourceProviderNames
  scriptHint: string
}

export const MCP_PRESETS: McpPreset[] = [
  {
    id: 'graphium',
    serverName: 'graphium',
    command: 'node',
    nodeCandidates: ['/opt/homebrew/bin/node', '/usr/local/bin/node', '/usr/bin/node'],
    defaultRoot: (userHome) => `${userHome}/Documents/Graphium`,
    envName: 'GRAPHIUM_ROOT',
    sourceProvider: { list: 'list_media', get: 'get_media_file', add: 'add_material' },
    scriptHint: 'dist-mcp/graphium-mcp.mjs',
  },
]

export const GRAPHIUM_PRESET = MCP_PRESETS[0]

export interface PresetInput {
  nodePath: string
  scriptPath: string
  root: string
}

export function presetEntry(preset: McpPreset, input: PresetInput): Extract<McpServerEntry, { transport: 'stdio' }> {
  return {
    transport: 'stdio',
    command: input.nodePath.trim(),
    args: [input.scriptPath.trim()],
    env: { [preset.envName]: input.root.trim() },
    enabled: true,
  }
}

export type PresetState = 'off' | 'on' | 'partial'

export function presetState(
  preset: McpPreset,
  settings: {
    servers: Record<string, McpServerEntry>
    home: string
    provider: SourceProviderNames
  },
): PresetState {
  const entry = settings.servers[preset.serverName]
  const enabled = entry !== undefined && entry.enabled === true
  const isHome = settings.home === preset.serverName
  const p = settings.provider
  const q = preset.sourceProvider
  const providerOk = p.list === q.list && p.get === q.get && p.add === q.add
  if (enabled && isHome && providerOk) return 'on'
  if (enabled || isHome) return 'partial'
  return 'off'
}
