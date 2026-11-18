import { flushAppSettingsAndWait, getServerSetting, putAppSetting } from '../appSettings'
import type { McpServerEntry } from './mcpServerForm'
import { fetchMcpServerTools, locateMcpCommand, type SourceProviderNames } from './mcpServersApi'

// 取り込み元のプリセット。表示から切り離したデータ（connectRecipes.ts と同じ性質）。
// Asterism 本体にアプリ固有のコードは置かず、ここに名前と道具名だけを持つ。
// 値の保存は McpServersTab の「つなぐ」を押したときだけ。

export interface McpPreset {
  id: string
  serverName: string
  command: string
  /** 起動ファイルの置き場所の候補（~ は裏側が展開する）。入っているアプリの中にある。 */
  scriptCandidates: string[]
  /** vault（ノートを置くフォルダ）の既定の場所の候補。 */
  rootCandidates: string[]
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
    // Graphium のアプリが macOS 版で配る場所（Windows・Linux の置き場は分かる範囲に無いので macOS だけ）
    scriptCandidates: [
      '/Applications/Graphium.app/Contents/Resources/mcp/graphium-mcp.mjs',
      '~/Applications/Graphium.app/Contents/Resources/mcp/graphium-mcp.mjs',
    ],
    rootCandidates: ['~/Documents/Graphium'],
    // アプリが同梱する node を先頭に（Node を別に入れなくても動く）
    nodeCandidates: [
      '/Applications/Graphium.app/Contents/Resources/sidecar/node',
      '~/Applications/Graphium.app/Contents/Resources/sidecar/node',
      '/opt/homebrew/bin/node', '/usr/local/bin/node', '/usr/bin/node', '~/.nvm/versions/node/*/bin/node'],
    defaultRoot: (userHome) => `${userHome}/Documents/Graphium`,
    envName: 'GRAPHIUM_ROOT',
    sourceProvider: { list: 'list_media', get: 'get_media_file', add: 'add_material', knowledge: 'export_asterism_knowledge' },
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

/** 起動ファイル・node・vault の在りかを機械で探す（設定タブと取り込みの窓口が共有）。見つからなければ null。 */
export async function locatePreset(preset: McpPreset): Promise<{ node: string | null; script: string | null; root: string | null; userHome: string }> {
  const r = await locateMcpCommand(preset.command, preset.nodeCandidates, {
    fileCandidates: preset.scriptCandidates,
    dirCandidates: preset.rootCandidates,
  })
  return { node: r.found, script: r.file ?? null, root: r.dir ?? null, userHome: r.user_home }
}

/** 入力の `~/` をフルパスに直す（起動時に ~ は展開されない。設定タブと取り込みの窓口が共有）。 */
export function expandHome(p: string, userHome: string): string {
  return userHome && p.startsWith('~/') ? userHome + p.slice(1) : p
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

/** 「つなぐ」で設定に書く（設定タブの「つなぐ」と取り込みの画面が共有）。書いた結果の値を返す。 */
export function applyPreset(
  preset: McpPreset,
  input: PresetInput,
): { servers: Record<string, McpServerEntry>; provider: SourceProviderNames } {
  const servers = { ...(getServerSetting<Record<string, McpServerEntry>>('mcpServers') ?? {}), [preset.serverName]: presetEntry(preset, input) }
  // 利用者が自分で入れた knowledge の道具名は上書きしない
  const cur = getServerSetting<Partial<SourceProviderNames>>('sourceProvider') ?? {}
  const provider = { ...preset.sourceProvider, knowledge: cur.knowledge || preset.sourceProvider.knowledge }
  putAppSetting('mcpServers', servers)
  putAppSetting('sourceHome', { server: preset.serverName })
  putAppSetting('sourceProvider', provider)
  const sendOriginals = getServerSetting<boolean>('sendOriginalsToHome')
  if (sendOriginals === undefined || sendOriginals === null) putAppSetting('sendOriginalsToHome', true)
  return { servers, provider }
}

/** 書いた設定をサーバに反映してから、道具の一覧に取り込み元の道具があるか確かめる。 */
export async function verifyPresetTools(preset: McpPreset): Promise<{ ok: boolean; tool: string }> {
  await flushAppSettingsAndWait() // 遅延書き込みより先に確かめに行かない
  const list = await fetchMcpServerTools(preset.serverName)
  return { ok: list.some((x) => x.name === preset.sourceProvider.list), tool: preset.sourceProvider.list }
}

/** 取り込む前に、`sourceProvider.knowledge` が無ければ足して保存する（ほかの道具名は触らない）。 */
export async function ensureKnowledgeTool(preset: McpPreset): Promise<void> {
  const cur = getServerSetting<Partial<SourceProviderNames>>('sourceProvider') ?? {}
  const tool = preset.sourceProvider.knowledge
  if (!tool || cur.knowledge) return
  putAppSetting('sourceProvider', { list: '', get: '', add: '', ...cur, knowledge: tool })
  await flushAppSettingsAndWait()
}
