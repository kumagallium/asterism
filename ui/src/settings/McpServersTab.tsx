import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { flushAppSettingsAndWait, getServerSetting, isAppSettingsServerMode, putAppSetting } from '../appSettings'
import {
  buildEntryFromForm,
  envToLines,
  parsePastedJson,
  type McpFormIssue,
  type McpServerEntry,
} from './mcpServerForm'
import { GRAPHIUM_PRESET, presetEntry, presetState } from './mcpPresets'
import {
  fetchMcpServers,
  fetchMcpServerTools,
  locateMcpCommand,
  type McpRecentCall,
  type McpToolInfo,
  type SourceProviderNames,
} from './mcpServersApi'

// MCP サーバ タブ — 他アプリの MCP サーバを登録し、取り込み元（表のファイルの持ち主）に
// 選ぶ（ADR source-switch-mcp-client.md）。設定の保存はここの putAppSetting だけ。
// サーバ側は settings.json を書かない。

type Servers = Record<string, McpServerEntry>

const EMPTY_PROVIDER: SourceProviderNames = { list: '', get: '', add: '' }

export function McpServersTab() {
  const { t } = useTranslation('settings')
  const writable = isAppSettingsServerMode()

  const [servers, setServers] = useState<Servers>(() => getServerSetting<Servers>('mcpServers') ?? {})
  const [home, setHome] = useState<string>(
    () => getServerSetting<{ server?: string } | null>('sourceHome')?.server ?? '',
  )
  const [provider, setProvider] = useState<SourceProviderNames>(() => ({
    ...EMPTY_PROVIDER,
    ...(getServerSetting<Partial<SourceProviderNames>>('sourceProvider') ?? {}),
  }))
  const [sendOriginals, setSendOriginals] = useState<boolean>(
    () => getServerSetting<boolean>('sendOriginalsToHome') ?? true,
  )
  const [recent, setRecent] = useState<McpRecentCall[]>([])
  const [tools, setTools] = useState<Record<string, McpToolInfo[] | string>>({})

  // 追加フォーム
  const [name, setName] = useState('')
  const [transport, setTransport] = useState<'stdio' | 'http'>('stdio')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [env, setEnv] = useState('')
  const [url, setUrl] = useState('')
  const [pasted, setPasted] = useState('')
  const [errors, setErrors] = useState<McpFormIssue[]>([])
  const [warnings, setWarnings] = useState<McpFormIssue[]>([])

  // Graphium プリセットの節
  const [located, setLocated] = useState<{ found: string | null; userHome: string } | null>(null)
  const [openForm, setOpenForm] = useState(false)
  const [fScript, setFScript] = useState<string | null>(null)
  const [fRoot, setFRoot] = useState<string | null>(null)
  const [fNode, setFNode] = useState<string | null>(null)
  const [connectMsg, setConnectMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const reloadRecent = useCallback(() => {
    fetchMcpServers()
      .then((r) => setRecent(r.recent))
      .catch(() => setRecent([]))
  }, [])

  useEffect(() => {
    if (writable) reloadRecent()
  }, [writable, reloadRecent])

  useEffect(() => {
    if (!writable) return
    locateMcpCommand(GRAPHIUM_PRESET.command, GRAPHIUM_PRESET.nodeCandidates)
      .then((r) => setLocated({ found: r.found, userHome: r.user_home }))
      .catch(() => setLocated(null))
  }, [writable])

  if (!writable) {
    return (
      <div className="connect-tab">
        <section className="serverkeys storage-section">
          <h4 className="serverkeys-title">{t('mcp.title')}</h4>
          <p className="field-help">{t('mcp.browserOnly')}</p>
        </section>
      </div>
    )
  }

  const issueText = (i: McpFormIssue) => t(`mcp.issue.${i.code}`, i.params)

  function saveServers(next: Servers) {
    setServers(next)
    putAppSetting('mcpServers', next)
  }

  function addServer() {
    const res = buildEntryFromForm({ name, transport, command, args, env, url })
    setErrors(res.errors)
    setWarnings(res.warnings)
    if (!res.entry) return
    const key = name.trim()
    if (servers[key] && !window.confirm(t('mcp.overwrite', { name: key }))) return
    saveServers({ ...servers, [key]: res.entry })
    resetForm()
  }

  function addPasted() {
    const parsed = parsePastedJson(pasted, name)
    if (parsed.error) {
      setErrors([parsed.error])
      setWarnings([])
      return
    }
    const errs: McpFormIssue[] = []
    const warns: McpFormIssue[] = []
    const next = { ...servers }
    for (const { name: n, result } of parsed.items) {
      if (!result.entry) {
        errs.push(...result.errors.map((e) => ({ ...e, params: { ...e.params, name: n || '?' } })))
        continue
      }
      warns.push(...result.warnings)
      next[n] = result.entry
    }
    setErrors(errs)
    setWarnings(warns)
    if (errs.length !== 0) return
    // フォームからの追加と同じく、同名の上書きは確かめてから（コマンドが黙って置き換わらないように）
    const clobbered = parsed.items.filter(({ name: n, result }) => result.entry && servers[n]).map(({ name: n }) => n)
    if (clobbered.length > 0 && !window.confirm(t('mcp.overwrite', { name: clobbered.join(', ') }))) return
    saveServers(next)
    setPasted('')
  }

  function resetForm() {
    setName('')
    setCommand('')
    setArgs('')
    setEnv('')
    setUrl('')
    setErrors([])
    setWarnings([])
  }

  function fillExample() {
    const ex = presetEntry(GRAPHIUM_PRESET, {
      nodePath: '/usr/local/bin/node',
      scriptPath: `/path/to/Graphium/${GRAPHIUM_PRESET.scriptHint}`,
      root: '/path/to/your/Graphium-vault',
    })
    setName(GRAPHIUM_PRESET.serverName)
    setTransport('stdio')
    setCommand(ex.command)
    setArgs(ex.args.join('\n'))
    setEnv(envToLines(ex.env))
    setErrors([])
    setWarnings([])
  }

  function setEnabled(key: string, enabled: boolean) {
    saveServers({ ...servers, [key]: { ...servers[key], enabled } as McpServerEntry })
  }

  function remove(key: string) {
    if (!window.confirm(t('mcp.confirmDelete', { name: key }))) return
    const next = { ...servers }
    delete next[key]
    saveServers(next)
    if (home === key) changeHome('')
  }

  async function showTools(key: string) {
    try {
      const list = await fetchMcpServerTools(key)
      setTools((p) => ({ ...p, [key]: list }))
    } catch (e) {
      setTools((p) => ({ ...p, [key]: e instanceof Error ? e.message : String(e) }))
    }
    reloadRecent()
  }

  function changeHome(server: string) {
    setHome(server)
    putAppSetting('sourceHome', server ? { server } : null)
  }

  function changeProvider(patch: Partial<SourceProviderNames>) {
    const next = { ...provider, ...patch }
    setProvider(next)
    putAppSetting('sourceProvider', next)
  }

  const names = Object.keys(servers)

  const pState = presetState(GRAPHIUM_PRESET, { servers, home, provider })
  const existing = servers[GRAPHIUM_PRESET.serverName]
  const existingStdio = existing && existing.transport === 'stdio' ? existing : undefined
  const scriptValue = fScript ?? existingStdio?.args?.[0] ?? ''
  const rootValue =
    fRoot ??
    existingStdio?.env?.[GRAPHIUM_PRESET.envName] ??
    (located ? GRAPHIUM_PRESET.defaultRoot(located.userHome) : '')
  const nodeValue = fNode ?? existingStdio?.command ?? located?.found ?? ''
  // 画面ではホームを ~ に省略する（長いフルパスで驚かせない）。保存する値はフルパスのまま。
  const userHome = located?.userHome ?? ''
  const tilde = (p: string) => (userHome && (p === userHome || p.startsWith(userHome + '/')) ? '~' + p.slice(userHome.length) : p)
  // 入力に ~/ が来たらフルパスに戻す（起動時に ~ は展開されない）
  const expand = (p: string) => (userHome && p.startsWith('~/') ? userHome + p.slice(1) : p)
  const formInput = { nodePath: expand(nodeValue.trim()), scriptPath: expand(scriptValue.trim()), root: expand(rootValue.trim()) }
  const formReady = nodeValue.trim() !== '' && scriptValue.trim() !== '' && rootValue.trim() !== ''
  const showPresetForm = pState !== 'on' && (openForm || pState === 'partial')
  const fullCommand = (e: { command: string; args: string[]; env: Record<string, string> }) =>
    [e.command, ...e.args].map(tilde).join(' ')

  async function verifyPreset() {
    setBusy(true)
    try {
      await flushAppSettingsAndWait() // 遅延書き込みより先に確かめに行かない
      const list = await fetchMcpServerTools(GRAPHIUM_PRESET.serverName)
      const has = list.some((x) => x.name === GRAPHIUM_PRESET.sourceProvider.list)
      setConnectMsg({
        ok: has,
        text: has
          ? t('mcp.preset.okTools', { tool: GRAPHIUM_PRESET.sourceProvider.list })
          : t('mcp.preset.noTool', { tool: GRAPHIUM_PRESET.sourceProvider.list }),
      })
    } catch (e) {
      setConnectMsg({ ok: false, text: e instanceof Error ? e.message : String(e) })
    }
    setBusy(false)
    reloadRecent()
  }

  async function connectPreset() {
    if (!formReady) return
    const entry = presetEntry(GRAPHIUM_PRESET, formInput)
    saveServers({ ...servers, [GRAPHIUM_PRESET.serverName]: entry })
    changeHome(GRAPHIUM_PRESET.serverName)
    setProvider({ ...GRAPHIUM_PRESET.sourceProvider })
    putAppSetting('sourceProvider', GRAPHIUM_PRESET.sourceProvider)
    if (getServerSetting<boolean>('sendOriginalsToHome') === undefined || getServerSetting<boolean>('sendOriginalsToHome') === null) {
      setSendOriginals(true)
      putAppSetting('sendOriginalsToHome', true)
    }
    setOpenForm(false)
    await verifyPreset()
  }

  function togglePreset(checked: boolean) {
    setConnectMsg(null)
    if (checked) {
      setOpenForm(true)
      return
    }
    setOpenForm(false)
    if (existing) saveServers({ ...servers, [GRAPHIUM_PRESET.serverName]: { ...existing, enabled: false } as McpServerEntry })
    if (home === GRAPHIUM_PRESET.serverName) changeHome('')
  }

  const originalsCheck = (
    <>
      <label className="mcp-check">
        <input
          type="checkbox"
          disabled={!home}
          checked={sendOriginals}
          onChange={(e) => {
            setSendOriginals(e.target.checked)
            putAppSetting('sendOriginalsToHome', e.target.checked)
          }}
        />
        {t('mcp.originalsLabel')}
      </label>
      {!home && <p className="field-help">{t('mcp.originalsNoHome')}</p>}
    </>
  )
  const detailsOpen = pState === 'off' && names.length >= 1

  return (
    <div className="connect-tab">
      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('mcp.preset.title')}</h4>
        <p className="field-help">{t('mcp.preset.intro')}</p>
        <label className="mcp-check">
          <input type="checkbox" checked={pState === 'on'} onChange={(e) => togglePreset(e.target.checked)} />
          {t('mcp.preset.title')}
        </label>
        {pState === 'on' && existingStdio && (
          <>
            <p className="field-help">{t('mcp.preset.connected')}</p>
            <code className="about-value mcp-command">{fullCommand(existingStdio)}</code>
            <code className="about-value mcp-command">{`${GRAPHIUM_PRESET.envName}=${tilde(existingStdio.env?.[GRAPHIUM_PRESET.envName] ?? '')}`}</code>
            <div className="serverkey-controls">
              <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void verifyPreset()}>
                {t('mcp.preset.verify')}
              </button>
            </div>
          </>
        )}
        {showPresetForm && (
          <>
            <div className="mcp-form">
              <label className="about-label">{t('mcp.preset.script')}</label>
              <input
                value={scriptValue}
                onChange={(e) => setFScript(e.target.value)}
                placeholder={`/path/to/Graphium/${GRAPHIUM_PRESET.scriptHint}`}
              />
              <label className="about-label">{t('mcp.preset.root')}</label>
              <input value={rootValue} onChange={(e) => setFRoot(e.target.value)} />
              <label className="about-label">{t('mcp.preset.node')}</label>
              <input value={nodeValue} onChange={(e) => setFNode(e.target.value)} placeholder="/usr/local/bin/node" />
              {located && !located.found && nodeValue === '' && (
                <p className="field-help">{t('mcp.preset.nodeMissing')}</p>
              )}
            </div>
            <p className="field-help">{t('mcp.preset.command')}</p>
            <code className="about-value mcp-command">{`${tilde(nodeValue)} ${tilde(scriptValue)}`.trim()}</code>
            <code className="about-value mcp-command">{`${GRAPHIUM_PRESET.envName}=${tilde(rootValue)}`}</code>
            <div className="serverkey-controls">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={!formReady || busy}
                onClick={() => void connectPreset()}
              >
                {t('mcp.preset.connect')}
              </button>
            </div>
          </>
        )}
        {connectMsg && (
          <p className={connectMsg.ok ? 'field-help' : 'field-help field-error'}>{connectMsg.text}</p>
        )}
        {originalsCheck}
      </section>

      <details open={detailsOpen} key={detailsOpen ? 'open' : 'closed'}>
        <summary className="about-label">{t('mcp.preset.detailsTitle')}</summary>
      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('mcp.title')}</h4>
        <p className="field-help">{t('mcp.intro')}</p>

        <h5 className="mcp-subtitle">{t('mcp.listTitle')}</h5>
        {names.length === 0 && <p className="field-help">{t('mcp.empty')}</p>}
        {names.map((key) => {
          const s = servers[key]
          const toolState = tools[key]
          return (
            <div className="serverkey-row" key={key}>
              <div className="serverkey-head">
                <span className="serverkey-name">{key}</span>
                <span className="about-label">
                  {s.transport === 'stdio' ? t('mcp.transportStdio') : t('mcp.transportHttp')}
                </span>
              </div>
              {s.transport === 'stdio' ? (
                <>
                  <code className="about-value mcp-command">{[s.command, ...(s.args ?? [])].map(tilde).join(' ')}</code>
                  {Object.entries(s.env ?? {}).map(([k, v]) => (
                    <code className="about-value mcp-command" key={k}>{`${k}=${tilde(v)}`}</code>
                  ))}
                </>
              ) : (
                <code className="about-value mcp-command">{s.url}</code>
              )}
              <div className="serverkey-controls">
                <label className="mcp-check">
                  <input
                    type="checkbox"
                    checked={s.enabled === true}
                    onChange={(e) => setEnabled(key, e.target.checked)}
                  />
                  {t('mcp.enabled')}
                </label>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={s.enabled !== true}
                  onClick={() => void showTools(key)}
                >
                  {t('mcp.showTools')}
                </button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => remove(key)}>
                  {t('mcp.delete')}
                </button>
              </div>
              {typeof toolState === 'string' && <p className="field-help field-error">{toolState}</p>}
              {Array.isArray(toolState) && (
                <ul className="mcp-tools">
                  {toolState.length === 0 && <li className="field-help">{t('mcp.noTools')}</li>}
                  {toolState.map((tool) => (
                    <li key={tool.name}>
                      <code>{tool.name}</code>
                      {tool.description ? <span className="field-help"> {tool.description}</span> : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </section>

      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('mcp.addTitle')}</h4>
        <div className="mcp-form">
          <label className="about-label">{t('mcp.name')}</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="graphium" />
          <label className="about-label">{t('mcp.transport')}</label>
          <select value={transport} onChange={(e) => setTransport(e.target.value as 'stdio' | 'http')}>
            <option value="stdio">{t('mcp.transportStdio')}</option>
            <option value="http">{t('mcp.transportHttp')}</option>
          </select>
          {transport === 'stdio' ? (
            <>
              <label className="about-label">{t('mcp.command')}</label>
              <input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="/usr/local/bin/node" />
              <label className="about-label">{t('mcp.args')}</label>
              <textarea rows={3} value={args} onChange={(e) => setArgs(e.target.value)} />
              <label className="about-label">{t('mcp.env')}</label>
              <textarea rows={3} value={env} onChange={(e) => setEnv(e.target.value)} placeholder="NAME=value" />
            </>
          ) : (
            <>
              <label className="about-label">{t('mcp.url')}</label>
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://127.0.0.1:9999/mcp" />
            </>
          )}
        </div>
        <div className="serverkey-controls">
          <button type="button" className="btn btn--ghost btn--sm" onClick={addServer}>
            {t('mcp.save')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={fillExample}>
            {t('mcp.fillExample')}
          </button>
        </div>

        <label className="about-label">{t('mcp.pasteLabel')}</label>
        <textarea
          rows={4}
          className="mcp-paste"
          value={pasted}
          onChange={(e) => setPasted(e.target.value)}
          placeholder='{"mcpServers": {"…": {"transport": "stdio", "command": "…"}}}'
        />
        <div className="serverkey-controls">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={pasted.trim().length === 0}
            onClick={addPasted}
          >
            {t('mcp.pasteSave')}
          </button>
        </div>
        <p className="field-help">{t('mcp.newDisabled')}</p>
        {errors.map((e, i) => (
          <p className="field-help field-error" key={`e${i}`}>
            {issueText(e)}
          </p>
        ))}
        {warnings.map((w, i) => (
          <p className="field-help field-warn" key={`w${i}`}>
            {issueText(w)}
          </p>
        ))}
      </section>

      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('mcp.sourceTitle')}</h4>
        <label className="about-label">{t('mcp.homeLabel')}</label>
        <select className="mcp-select" value={home} onChange={(e) => changeHome(e.target.value)}>
          <option value="">{t('mcp.homeNone')}</option>
          {names.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <p className="field-help">{t('mcp.providerHelp')}</p>
        <div className="mcp-form">
          {(['list', 'get', 'add'] as const).map((k) => (
            <div className="mcp-provider-field" key={k}>
              <label className="about-label">{t(`mcp.provider.${k}`)}</label>
              <input value={provider[k]} onChange={(e) => changeProvider({ [k]: e.target.value })} />
            </div>
          ))}
        </div>
        <div className="serverkey-controls">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => changeProvider(GRAPHIUM_PRESET.sourceProvider)}
          >
            {t('mcp.fillExample')}
          </button>
        </div>
      </section>

      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('mcp.originalsTitle')}</h4>
        {originalsCheck}
      </section>

      <section className="serverkeys storage-section">
        <h4 className="serverkeys-title">{t('mcp.recentTitle')}</h4>
        {recent.length === 0 ? (
          <p className="field-help">{t('mcp.recentEmpty')}</p>
        ) : (
          <table className="mcp-recent">
            <thead>
              <tr>
                <th>{t('mcp.recent.server')}</th>
                <th>{t('mcp.recent.tool')}</th>
                <th>{t('mcp.recent.result')}</th>
                <th>{t('mcp.recent.at')}</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((r, i) => (
                <tr key={`${r.at}-${i}`}>
                  <td>{r.server}</td>
                  <td>
                    <code>{r.tool}</code>
                  </td>
                  <td>
                    {r.ok
                      ? r.count != null
                        ? t('mcp.recent.okCount', { count: r.count })
                        : t('mcp.recent.ok')
                      : t('mcp.recent.fail', { reason: r.error ?? '' })}
                  </td>
                  <td>{r.at.replace('T', ' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      </details>
    </div>
  )
}
