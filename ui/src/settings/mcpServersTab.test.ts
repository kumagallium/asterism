import { describe, expect, it } from 'vitest'
import { MCP_SERVER_EXAMPLES } from './mcpServerExamples'
import {
  buildEntryFromForm,
  looksSecret,
  normalizeEntry,
  parseArgsLines,
  parseEnvLines,
  parsePastedJson,
  type McpFormInput,
} from './mcpServerForm'

const base: McpFormInput = {
  name: 'graphium',
  transport: 'stdio',
  command: '/usr/local/bin/node',
  args: 'a.mjs\n\n  --x  \n',
  env: 'GRAPHIUM_ROOT=/p\nEMPTY=',
  url: '',
}

describe('mcp server examples', () => {
  it('Graphium の例は stdio で、取り込み元の 3 道具名を持つ', () => {
    const ex = MCP_SERVER_EXAMPLES.find((e) => e.id === 'graphium')
    expect(ex?.transport).toBe('stdio')
    expect(ex?.command).toBe('/usr/local/bin/node')
    expect(ex?.env).toHaveProperty('GRAPHIUM_ROOT')
    expect(ex?.sourceProvider).toEqual({ list: 'list_media', get: 'get_media_file', add: 'add_material' })
  })
  it('例は秘密の名前を含まず、そのまま検証を通る', () => {
    for (const ex of MCP_SERVER_EXAMPLES) {
      const r = normalizeEntry(ex.name, ex)
      expect(r.errors).toEqual([])
      expect(r.warnings).toEqual([])
    }
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
