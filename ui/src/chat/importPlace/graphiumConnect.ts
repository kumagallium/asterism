// 「Graphium とつなぐ」の判定と手順（契約 pr14d2b §2.1）。設定タブの「つなぐ」と同じ関数（mcpPresets）を使う。
// 既に `graphium` の登録がある人の設定は書き換えない（足すのは knowledge の道具名だけ）。
import { flushAppSettingsAndWait, getServerSetting, putAppSetting } from '../../appSettings'
import type { McpServerEntry } from '../../settings/mcpServerForm'
import { applyPreset, expandHome, GRAPHIUM_PRESET, presetEntry } from '../../settings/mcpPresets'
import { fetchMcpServerTools, locateMcpCommand, type McpToolInfo, type SourceProviderNames } from '../../settings/mcpServersApi'

const EMPTY: SourceProviderNames = { list: '', get: '', add: '' }
const NAME = GRAPHIUM_PRESET.serverName

const servers = () => getServerSetting<Record<string, McpServerEntry>>('mcpServers') ?? {}

/** 設定に `graphium` の登録があるか（窓口の「つなぐ／取り込む」はこれで決める）。 */
export function isRegistered(): boolean {
  return servers()[NAME] !== undefined
}

/** 「写し直す」を出してよい取り込み元の名前: プリセットのサーバ名で、登録があり、有効なときだけ。 */
export function rerunnableServers(): string[] {
  const e = servers()[NAME]
  return e !== undefined && e.enabled === true ? [NAME] : []
}

export type ConnectFailure = { code: 'form' | 'node' | 'tool' | 'old'; tool?: string } | { code: 'http'; detail: string }

/** 設定の読み書き（試験では差し替える）。 */
export interface SettingsIO {
  get: <T>(key: string) => T | undefined
  put: (key: string, value: unknown) => void
  flush: () => Promise<void>
}
export interface ConnectDeps {
  locate: typeof locateMcpCommand
  apply: typeof applyPreset
  tools: (name: string) => Promise<McpToolInfo[]>
  io: SettingsIO
}
export const realDeps: ConnectDeps = {
  locate: locateMcpCommand,
  apply: applyPreset,
  tools: fetchMcpServerTools,
  io: { get: getServerSetting, put: putAppSetting, flush: flushAppSettingsAndWait },
}

/** この操作が書きうる設定のキー（失敗したら全部元に戻す）。 */
const WRITTEN = ['mcpServers', 'sourceHome', 'sourceProvider', 'sendOriginalsToHome'] as const

/**
 * 取り込みの前の準備。
 * - 登録があるとき: 設定は書き換えない。knowledge の道具名が無ければ足すだけ。
 * - 登録が無いとき: 入力欄の値で登録を足す。sourceHome が空ならプリセットを全部当て、別のサーバが居れば登録と knowledge だけ足す。
 * そのあと、knowledge の道具がサーバにあるか確かめる（古い Graphium は始めずに止める）。
 * 成功なら null、失敗なら理由（画面が文にして、設定を開く道を添える）。失敗したら、この操作で書いたものは元に戻す。
 */
export async function prepareGraphium(
  form: { script: string; root: string },
  userHome: string,
  deps: ConnectDeps = realDeps,
): Promise<ConnectFailure | null> {
  const { io } = deps
  const prev = WRITTEN.map((k) => [k, io.get<unknown>(k)] as const)
  let wrote = false
  const put = (k: string, v: unknown) => {
    wrote = true
    io.put(k, v)
  }
  const undo = async () => {
    if (!wrote) return
    for (const [k, v] of prev) io.put(k, v)
    await io.flush()
  }
  try {
    const reg = io.get<Record<string, McpServerEntry>>('mcpServers') ?? {}
    const cur = io.get<Partial<SourceProviderNames>>('sourceProvider') ?? {}
    const knowledge = cur.knowledge || GRAPHIUM_PRESET.sourceProvider.knowledge || ''
    let fullApply = false
    if (reg[NAME] === undefined) {
      if (!form.script.trim() || !form.root.trim()) return { code: 'form' }
      const found = (await deps.locate(GRAPHIUM_PRESET.command, GRAPHIUM_PRESET.nodeCandidates)).found
      if (!found) return { code: 'node' }
      const input = { nodePath: found, scriptPath: expandHome(form.script.trim(), userHome), root: expandHome(form.root.trim(), userHome) }
      const home = io.get<{ server?: string } | null>('sourceHome')?.server ?? ''
      if (!home) {
        wrote = true // apply は内部で書く
        deps.apply(GRAPHIUM_PRESET, input)
        fullApply = true
      } else {
        // 別のサーバが取り込み元の人の sourceHome と list/get/add は変えない（登録と knowledge だけ足す）
        put('mcpServers', { ...reg, [NAME]: presetEntry(GRAPHIUM_PRESET, input) })
        put('sourceProvider', { ...EMPTY, ...cur, knowledge })
      }
    } else if (!cur.knowledge && knowledge) {
      put('sourceProvider', { ...EMPTY, ...cur, knowledge })
    }
    await io.flush() // 遅延書き込みより先に確かめに行かない
    const names = new Set((await deps.tools(NAME)).map((t) => t.name))
    if (fullApply && !names.has(GRAPHIUM_PRESET.sourceProvider.list)) {
      await undo()
      return { code: 'tool', tool: GRAPHIUM_PRESET.sourceProvider.list }
    }
    if (!names.has(knowledge)) {
      await undo()
      return { code: 'old', tool: knowledge }
    }
    return null
  } catch (e) {
    await undo().catch(() => {})
    return { code: 'http', detail: e instanceof Error ? e.message : String(e) }
  }
}
