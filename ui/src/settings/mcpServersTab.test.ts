import { describe, expect, it } from 'vitest'
import { GRAPHIUM_PRESET, presetEntry, presetState } from './mcpPresets'
import {
  buildEntryFromForm,
  looksSecret,
  normalizeEntry,
  parseArgsLines,
  parseEnvLines,
  parsePastedJson,
  type McpFormInput,
  urlCarriesSecret,
} from './mcpServerForm'
import { isSecretKeyName } from '../appSettings'

const base: McpFormInput = {
  name: 'graphium',
  transport: 'stdio',
  command: '/usr/local/bin/node',
  args: 'a.mjs\n\n  --x  \n',
  env: 'GRAPHIUM_ROOT=/p\nEMPTY=',
  url: '',
}

describe('mcp presets', () => {
  const P = GRAPHIUM_PRESET
  const entry = presetEntry(P, { nodePath: '/usr/local/bin/node', scriptPath: '/g/dist-mcp/graphium-mcp.mjs', root: '/v' })
  const provider = P.sourceProvider
  const none = { list: '', get: '', add: '' }
  it('Graphium のプリセットは取り込み元の 3 道具名を持つ', () => {
    expect(P.serverName).toBe('graphium')
    expect(P.envName).toBe('GRAPHIUM_ROOT')
    expect(provider).toEqual({ list: 'list_media', get: 'get_media_file', add: 'add_material' })
    expect(P.defaultRoot('/Users/a')).toBe('/Users/a/Documents/Graphium')
  })
  it('presetEntry は有効な stdio 1 件で、検証を通り秘密の警告もない', () => {
    expect(entry).toEqual({
      transport: 'stdio',
      command: '/usr/local/bin/node',
      args: ['/g/dist-mcp/graphium-mcp.mjs'],
      env: { GRAPHIUM_ROOT: '/v' },
      enabled: true,
    })
    const r = normalizeEntry(P.serverName, entry)
    expect(r.errors).toEqual([])
    expect(r.warnings).toEqual([])
  })
  it('presetState: off / on / partial', () => {
    expect(presetState(P, { servers: {}, home: '', provider: none })).toBe('off')
    expect(presetState(P, { servers: { graphium: entry }, home: 'graphium', provider })).toBe('on')
    expect(presetState(P, { servers: { graphium: entry }, home: '', provider })).toBe('partial')
    expect(presetState(P, { servers: { graphium: entry }, home: 'graphium', provider: none })).toBe('partial')
    expect(presetState(P, { servers: {}, home: 'graphium', provider })).toBe('partial')
    const off = { ...entry, enabled: false }
    expect(presetState(P, { servers: { graphium: off }, home: '', provider })).toBe('off')
  })
})

describe('line parsing', () => {
  it('args は空行を捨てて前後の空白を取る', () => {
    expect(parseArgsLines(base.args)).toEqual(['a.mjs', '--x'])
  })
  it('env は最初の = で分け、不正な行を bad に集める', () => {
    const r = parseEnvLines('A=b=c\nnoequal\n1X=y\n=v')
    expect(r.env).toEqual({ A: 'b=c' })
    expect(r.bad).toEqual(['noequal', '1X=y', '=v'])
  })
})

describe('buildEntryFromForm', () => {
  it('stdio は enabled:false の 1 件になる', () => {
    const r = buildEntryFromForm(base)
    expect(r.errors).toEqual([])
    expect(r.entry).toEqual({
      transport: 'stdio',
      command: '/usr/local/bin/node',
      args: ['a.mjs', '--x'],
      env: { GRAPHIUM_ROOT: '/p', EMPTY: '' },
      enabled: false,
    })
  })
  it('http は url を検証する', () => {
    expect(buildEntryFromForm({ ...base, transport: 'http', url: 'ftp://x' }).errors[0].code).toBe('badUrl')
    expect(buildEntryFromForm({ ...base, transport: 'http', url: ' http://127.0.0.1:9/mcp ' }).entry).toEqual({
      transport: 'http',
      url: 'http://127.0.0.1:9/mcp',
      enabled: false,
    })
  })
  it('名前の規則', () => {
    for (const bad of ['', '-a', 'a b', 'あ', 'a'.repeat(65)]) {
      expect(buildEntryFromForm({ ...base, name: bad }).errors[0].code).toBe('badName')
    }
    expect(buildEntryFromForm({ ...base, name: 'a'.repeat(64) }).entry).not.toBeNull()
    expect(buildEntryFromForm({ ...base, name: 'my_token' }).errors[0].code).toBe('secretName')
  })
  it('command が空・env の行が不正ならエラー', () => {
    expect(buildEntryFromForm({ ...base, command: ' ' }).errors[0].code).toBe('noCommand')
    expect(buildEntryFromForm({ ...base, env: 'oops' }).errors[0].code).toBe('badEnvLine')
  })
  it('秘密に見える env 名は警告（保存は止めない）', () => {
    const r = buildEntryFromForm({ ...base, env: 'API_KEY=x\nkey=y\nGRAPHIUM_ROOT=/p\nKEYBOARD=us' })
    expect(r.entry).not.toBeNull()
    expect(r.warnings.map((w) => w.params?.name)).toEqual(['API_KEY', 'key'])
  })
})

describe('looksSecret', () => {
  it('key は完全一致だけ、ほかは部分一致', () => {
    expect(looksSecret('KEY')).toBe(true)
    expect(looksSecret('KEYBOARD')).toBe(false)
    expect(looksSecret('MY_api_key')).toBe(true)
    expect(looksSecret('GITHUB_TOKEN')).toBe(true)
    expect(looksSecret('DB_PASSWD')).toBe(true)
    expect(looksSecret('GRAPHIUM_ROOT')).toBe(false)
  })
})

describe('normalizeEntry / parsePastedJson', () => {
  it('headers と auth は拒否する', () => {
    for (const f of ['headers', 'auth']) {
      const r = normalizeEntry('s', { transport: 'http', url: 'http://x/mcp', [f]: {} })
      expect(r.entry).toBeNull()
      expect(r.errors[0]).toEqual({ code: 'forbiddenField', params: { field: f } })
    }
  })
  it('enabled の指定は貼り付けでも false に落とす', () => {
    const p = parsePastedJson(
      JSON.stringify({ mcpServers: { a: { transport: 'http', url: 'http://x/mcp', enabled: true } } }),
      '',
    )
    expect(p.items[0].result.entry).toMatchObject({ enabled: false })
  })
  it('1 件だけの貼り付けは名前欄の名前を使う', () => {
    const p = parsePastedJson('{"transport":"stdio","command":"node"}', 'one')
    expect(p.items[0].name).toBe('one')
    expect(p.items[0].result.entry).toMatchObject({ command: 'node', args: [], env: {} })
  })
  it('壊れた JSON・空の mcpServers', () => {
    expect(parsePastedJson('{', 'x').error?.code).toBe('badJson')
    expect(parsePastedJson('{"mcpServers":{}}', 'x').error?.code).toBe('emptyServers')
    expect(parsePastedJson('[]', 'x').error?.code).toBe('notObject')
  })
})

describe('レビュー（2026-10-23）で足した穴', () => {
  it('貼り付けた JSON の cwd を落とさない', () => {
    const r = normalizeEntry('g', { transport: 'stdio', command: 'node', cwd: '/w' })
    expect(r.entry).toMatchObject({ transport: 'stdio', cwd: '/w' })
  })
  it('userinfo や秘密に見える query の URL は保存前に断る', () => {
    expect(urlCarriesSecret('http://u:p@127.0.0.1/mcp')).toBe(true)
    expect(urlCarriesSecret('http://127.0.0.1/mcp?token=a')).toBe(true)
    expect(urlCarriesSecret('http://127.0.0.1/mcp?lang=ja')).toBe(false)
    expect(normalizeEntry('s', { transport: 'http', url: 'http://127.0.0.1/mcp?token=a' }).errors[0].code).toBe('urlSecret')
  })
  it('appSettings の秘密判定は api と同じ基準（keycloak・KEYBOARD は残る）', () => {
    expect(isSecretKeyName('keycloak')).toBe(false)
    expect(isSecretKeyName('KEYBOARD')).toBe(false)
    expect(isSecretKeyName('key')).toBe(true)
    expect(isSecretKeyName('PASSWD_FILE')).toBe(true)
  })
})
