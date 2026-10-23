import type { SourceProviderNames } from './mcpServersApi'

// 登録フォームの「例」。表示から切り離したデータ（connectRecipes.ts と同じ性質）。
// 選んでもフォームに入るだけで、保存はしない。パスは書き換える前提のプレースホルダ。

export interface McpServerExample {
  id: string
  name: string
  transport: 'stdio'
  command: string
  args: string[]
  env: Record<string, string>
  sourceProvider: SourceProviderNames
}

export const MCP_SERVER_EXAMPLES: McpServerExample[] = [
  {
    id: 'graphium',
    name: 'graphium',
    transport: 'stdio',
    command: '/usr/local/bin/node',
    args: ['/path/to/Graphium/dist-mcp/graphium-mcp.mjs'],
    env: { GRAPHIUM_ROOT: '/path/to/your/Graphium-vault' },
    sourceProvider: { list: 'list_media', get: 'get_media_file', add: 'add_material' },
  },
]
