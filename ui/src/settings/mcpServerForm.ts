// フォーム入力 → settings.json の mcpServers 1 件、への変換（純関数）。
// 検証規則はバックエンド api/src/asterism_api/mcp_client.py の parse_server と揃える。
// 文言は持たず、画面側が i18n で引けるコード（code + params）を返す。

export type McpFormIssue = { code: string; params?: Record<string, string> }

export type McpServerEntry =
  | { transport: 'stdio'; command: string; args: string[]; env: Record<string, string>; enabled: boolean; cwd?: string }
  | { transport: 'http'; url: string; enabled: boolean }

export interface McpFormInput {
  name: string
  transport: 'stdio' | 'http'
  command: string
  args: string // 1 行に 1 つ
  env: string // 1 行に NAME=値
  url: string
}

export const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/
const ENV_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/
const SECRET_MARKERS = ['apikey', 'api_key', 'token', 'secret', 'password', 'passwd', 'credential']

/** バックエンドの _looks_secret と同じ判定。 */
export function looksSecret(key: string): boolean {
  const lowered = key.toLowerCase()
  return lowered === 'key' || SECRET_MARKERS.some((m) => lowered.includes(m))
}

/** URL に userinfo（user:pass@）や秘密に見える名前の query が入っていると、繋げなかった
 *  ときの文言や「直近の呼び出し」にそのまま出る（レビュー L3）。保存前に断る。 */
export function urlCarriesSecret(url: string): boolean {
  let u: URL
  try {
    u = new URL(url)
  } catch {
    return false
  }
  if (u.username || u.password) return true
  for (const k of u.searchParams.keys()) if (looksSecret(k)) return true
  return false
}

export function parseArgsLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0)
}

/** `NAME=値` の行を分解する。= の無い行・名前が不正な行は bad に集める。 */
export function parseEnvLines(text: string): {
  env: Record<string, string>
  bad: string[]
} {
  const env: Record<string, string> = {}
  const bad: string[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const i = line.indexOf('=')
    const key = i > 0 ? line.slice(0, i).trim() : ''
    if (!key || !ENV_NAME_RE.test(key)) {
      bad.push(line)
      continue
    }
    env[key] = line.slice(i + 1)
  }
  return { env, bad }
}

export function envToLines(env: Record<string, string> | undefined): string {
  return Object.entries(env ?? {})
    .map(([k, v]) => `${k}=${v}`)
    .join('\n')
}

export interface BuildResult {
  entry: McpServerEntry | null
  errors: McpFormIssue[]
  warnings: McpFormIssue[]
}

/** 1 件の生の設定（JSON 貼り付け等）を検証して正規化する。新規は enabled:false。 */
export function normalizeEntry(name: string, raw: unknown): BuildResult {
  const errors: McpFormIssue[] = []
  const warnings: McpFormIssue[] = []
  const fail = (code: string, params?: Record<string, string>): BuildResult => ({
    entry: null,
    errors: [...errors, { code, params }],
    warnings,
  })
  if (!NAME_RE.test(name)) return fail('badName')
  if (looksSecret(name)) return fail('secretName')
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return fail('notObject')
  const r = raw as Record<string, unknown>
  for (const f of ['headers', 'auth']) {
    if (f in r) return fail('forbiddenField', { field: f })
  }
  if (r.transport !== 'stdio' && r.transport !== 'http') return fail('badTransport')
  const enabled = typeof r.enabled === 'boolean' ? r.enabled : false

  if (r.transport === 'http') {
    const url = typeof r.url === 'string' ? r.url.trim() : ''
    if (!/^https?:\/\//.test(url)) return fail('badUrl')
    if (urlCarriesSecret(url)) return fail('urlSecret')
    return { entry: { transport: 'http', url, enabled }, errors, warnings }
  }

  const command = typeof r.command === 'string' ? r.command.trim() : ''
  if (!command) return fail('noCommand')
  const args = r.args ?? []
  if (!Array.isArray(args) || !args.every((a) => typeof a === 'string')) return fail('badArgs')
  const env = (r.env ?? {}) as unknown
  if (
    typeof env !== 'object' ||
    env === null ||
    Array.isArray(env) ||
    !Object.values(env).every((v) => typeof v === 'string')
  ) {
    return fail('badEnv')
  }
  for (const k of Object.keys(env)) {
    if (!ENV_NAME_RE.test(k)) return fail('badEnvName', { name: k })
    if (looksSecret(k)) warnings.push({ code: 'secretEnv', params: { name: k } })
  }
  const cwd = typeof r.cwd === 'string' && r.cwd.trim() ? r.cwd.trim() : undefined
  return {
    entry: {
      transport: 'stdio',
      command,
      args: args as string[],
      env: { ...(env as Record<string, string>) },
      enabled,
      ...(cwd ? { cwd } : {}),
    },
    errors,
    warnings,
  }
}

/** フォームの入力 → 設定 1 件。新規なので enabled は常に false。 */
export function buildEntryFromForm(f: McpFormInput): BuildResult {
  const name = f.name.trim()
  if (f.transport === 'http') {
    return normalizeEntry(name, { transport: 'http', url: f.url, enabled: false })
  }
  const { env, bad } = parseEnvLines(f.env)
  if (bad.length > 0) {
    return { entry: null, errors: [{ code: 'badEnvLine', params: { line: bad[0] } }], warnings: [] }
  }
  return normalizeEntry(name, {
    transport: 'stdio',
    command: f.command,
    args: parseArgsLines(f.args),
    env,
    enabled: false,
  })
}

/** 貼り付けた JSON → [{name, result}]。`{mcpServers:{...}}` 全体か、1 件（name は引数）を受ける。 */
export function parsePastedJson(
  text: string,
  fallbackName: string,
): { items: { name: string; result: BuildResult }[]; error: McpFormIssue | null } {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return { items: [], error: { code: 'badJson' } }
  }
  if (typeof data !== 'object' || data === null || Array.isArray(data)) {
    return { items: [], error: { code: 'notObject' } }
  }
  const obj = data as Record<string, unknown>
  const wrapped = obj.mcpServers
  if (typeof wrapped === 'object' && wrapped !== null && !Array.isArray(wrapped)) {
    const items = Object.entries(wrapped as Record<string, unknown>).map(([name, raw]) => ({
      name,
      // 貼り付けは常に無効で取り込む（有効化は一覧で確かめてから）
      result: normalizeEntry(name, withDisabled(raw)),
    }))
    return { items, error: items.length ? null : { code: 'emptyServers' } }
  }
  const name = fallbackName.trim()
  return { items: [{ name, result: normalizeEntry(name, withDisabled(obj)) }], error: null }
}

function withDisabled(raw: unknown): unknown {
  return typeof raw === 'object' && raw !== null && !Array.isArray(raw)
    ? { ...(raw as Record<string, unknown>), enabled: false }
    : raw
}
