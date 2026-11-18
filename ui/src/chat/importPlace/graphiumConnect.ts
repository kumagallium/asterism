// 「Graphium とつなぐ」の判定と手順（契約 pr14d2b §2.1）。設定タブの「つなぐ」と同じ関数（mcpPresets）を使う。
import { flushAppSettingsAndWait, getServerSetting, putAppSetting } from '../../appSettings'
import type { McpServerEntry } from '../../settings/mcpServerForm'
import {
  applyPreset,
  ensureKnowledgeTool,
  GRAPHIUM_PRESET,
  presetState,
  verifyPresetTools,
  type PresetInput,
  type PresetState,
} from '../../settings/mcpPresets'
import { locateMcpCommand, type SourceProviderNames } from '../../settings/mcpServersApi'

const EMPTY: SourceProviderNames = { list: '', get: '', add: '' }

/** 設定から今の取り込み元の状態を読む（knowledge の道具名が無いだけでは partial にしない）。 */
export function graphiumState(): PresetState {
  return presetState(GRAPHIUM_PRESET, {
    servers: getServerSetting<Record<string, McpServerEntry>>('mcpServers') ?? {},
    home: getServerSetting<{ server?: string } | null>('sourceHome')?.server ?? '',
    provider: { ...EMPTY, ...(getServerSetting<Partial<SourceProviderNames>>('sourceProvider') ?? {}) },
  })
}

/** 設定に Graphium の stdio の登録があれば、その値（つなぎ直しに使う）。 */
export function savedGraphiumInput(): PresetInput | null {
  const e = (getServerSetting<Record<string, McpServerEntry>>('mcpServers') ?? {})[GRAPHIUM_PRESET.serverName]
  if (!e || e.transport !== 'stdio') return null
  const input = { nodePath: e.command ?? '', scriptPath: e.args?.[0] ?? '', root: e.env?.[GRAPHIUM_PRESET.envName] ?? '' }
  return input.nodePath && input.scriptPath && input.root ? input : null
}

/** 設定に登録してある MCP サーバの名前（写し直せる取り込み元）。 */
export function registeredServers(): string[] {
  return Object.keys(getServerSetting<Record<string, McpServerEntry>>('mcpServers') ?? {})
}

export type ConnectFailure = { code: 'form' | 'node' | 'tool'; tool?: string } | { code: 'http'; detail: string }

export interface ConnectDeps {
  locate: typeof locateMcpCommand
  apply: typeof applyPreset
  verify: typeof verifyPresetTools
  ensure: typeof ensureKnowledgeTool
}
export const realDeps: ConnectDeps = { locate: locateMcpCommand, apply: applyPreset, verify: verifyPresetTools, ensure: ensureKnowledgeTool }

/**
 * 取り込みの前の準備: つながっていなければ設定に書いて道具を確かめる。そのあと `knowledge` の道具名を足す。
 * 成功なら null、失敗なら理由（画面が文にして、設定を開く道を添える）。
 */
export async function prepareGraphium(
  state: PresetState,
  form: { script: string; root: string },
  deps: ConnectDeps = realDeps,
): Promise<ConnectFailure | null> {
  // 窓口の入力で新しく書いたとき、つながらなかったら元に戻す（壊れた登録が残ると入力欄が消え、直せなくなる）
  let undo: (() => Promise<void>) | null = null
  try {
    if (state !== 'on') {
      let input = savedGraphiumInput()
      if (!input) {
        if (!form.script.trim() || !form.root.trim()) return { code: 'form' }
        const found = (await deps.locate(GRAPHIUM_PRESET.command, GRAPHIUM_PRESET.nodeCandidates)).found
        if (!found) return { code: 'node' }
        input = { nodePath: found, scriptPath: form.script.trim(), root: form.root.trim() }
      }
      if (!savedGraphiumInput()) {
        const prev = [
          ['mcpServers', getServerSetting<unknown>('mcpServers') ?? {}],
          ['sourceHome', getServerSetting<unknown>('sourceHome') ?? null],
          ['sourceProvider', getServerSetting<unknown>('sourceProvider') ?? null],
        ] as const
        undo = async () => {
          for (const [k, v] of prev) putAppSetting(k, v)
          await flushAppSettingsAndWait()
        }
      }
      deps.apply(GRAPHIUM_PRESET, input)
      const v = await deps.verify(GRAPHIUM_PRESET)
      if (!v.ok) {
        await undo?.()
        return { code: 'tool', tool: v.tool }
      }
    }
    await deps.ensure(GRAPHIUM_PRESET)
    return null
  } catch (e) {
    await undo?.().catch(() => {})
    return { code: 'http', detail: e instanceof Error ? e.message : String(e) }
  }
}
