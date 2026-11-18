// 各状態の描画（renderToStaticMarkup）と、押したときの配線（関数として呼んだ要素の onClick を直に押す）。
import type { ReactElement, ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'ja' } }),
}))

import '../../i18n'
import { prepareGraphium, type ConnectDeps } from './graphiumConnect'
import { GraphiumCard, ImportNotice, ImportProgress, ImportResultCard, ImportSources, type SourcesProps } from './ImportViews'
import { result } from './testData'
import type { ImportSource } from './knowledgeImportApi'

type El = ReactElement<Record<string, unknown>>
const isEl = (n: unknown): n is El => !!n && typeof n === 'object' && 'props' in (n as object)
function byTestId(node: ReactNode, id: string, out: El[] = []): El[] {
  if (Array.isArray(node)) for (const c of node) byTestId(c, id, out)
  else if (isEl(node)) {
    if (node.props['data-testid'] === id) out.push(node)
    byTestId(node.props.children as ReactNode, id, out)
  }
  return out
}
const press = (el: El) => (el.props.onClick as () => void)()
const noop = () => {}

const src = (over: Partial<ImportSource> = {}): ImportSource => ({
  source: 'graphium',
  title: null,
  dataset_id: 'd1',
  copied_at: '2026-11-18T09:40:00Z',
  counts: { claims: 1515, topics: 405, sources: 0, terms: 104 },
  relations: { shown: 231, terms: 104, candidates: 0, unconfirmed: 231, confirmed: 0 },
  running: null,
  ...over,
})
const srcProps = (over: Partial<SourcesProps> = {}): SourcesProps => ({
  sources: [src()],
  lang: 'ja',
  jobSource: null,
  rerunnable: ['graphium'],
  confirming: null,
  busy: false,
  onRerun: noop,
  onAskRemove: noop,
  onRemove: noop,
  ...over,
})

describe('Graphium の窓口', () => {
  const card = (over = {}) => ({ connected: false, busy: false, error: null, form: null, onForm: noop, onGo: noop, onOpenSettings: noop, ...over })
  it('つながっていない／いる で押す文言が変わり、押すと onGo', () => {
    const onGo = vi.fn()
    const off = GraphiumCard(card({ onGo }))
    press(byTestId(off, 'import-graphium-go')[0])
    expect(onGo).toHaveBeenCalledTimes(1)
    expect(renderToStaticMarkup(<GraphiumCard {...card()} />)).toContain('graphium.connect')
    expect(renderToStaticMarkup(<GraphiumCard {...card({ connected: true })} />)).toContain('graphium.import')
  })
  it('取り込み中はボタンが押せない・失敗は alert と設定を開く道', () => {
    const html = renderToStaticMarkup(<GraphiumCard {...card({ busy: true, error: '道具が無い' })} />)
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*data-testid="import-graphium-go"|data-testid="import-graphium-go"[^>]*disabled=""/)
    expect(html).toContain('role="alert"')
    expect(html).toContain('道具が無い')
    const onOpenSettings = vi.fn()
    press(byTestId(GraphiumCard(card({ error: 'x', onOpenSettings })), 'import-open-settings')[0])
    expect(onOpenSettings).toHaveBeenCalled()
  })
  it('探している間は「探しています」で押せない・両方見つけたら欄を出さず見つけた場所と「変える」', () => {
    const searching = renderToStaticMarkup(<GraphiumCard {...card({ searching: true, form: { script: '', root: '' } })} />)
    expect(searching).toContain('graphium.searching')
    expect(searching).not.toContain('import-graphium-script')
    expect(searching).toMatch(/disabled=""[^>]*data-testid="import-graphium-go"|data-testid="import-graphium-go"[^>]*disabled=""/)
    const found = { script: '/app/graphium-mcp.mjs', root: '/v' }
    const html = renderToStaticMarkup(<GraphiumCard {...card({ form: null, found })} />)
    expect(html).toContain('import-graphium-found')
    expect(html).not.toContain('import-graphium-script')
    const onEdit = vi.fn()
    press(byTestId(GraphiumCard(card({ form: null, found, onEdit })), 'import-graphium-edit')[0])
    expect(onEdit).toHaveBeenCalled()
  })
  it('見つからなかった物だけ欄を出し、起動ファイルには何を入れるかの 1 行', () => {
    const html = renderToStaticMarkup(<GraphiumCard {...card({ form: { script: '', root: '/v' }, showScript: true, showRoot: false, scriptMissing: true })} />)
    expect(html).toContain('import-graphium-script')
    expect(html).not.toContain('import-graphium-root')
    expect(html).toContain('graphium.scriptHint')
  })
  it('入力欄は form があるときだけ', () => {
    expect(renderToStaticMarkup(<GraphiumCard {...card()} />)).not.toContain('import-graphium-script')
    expect(renderToStaticMarkup(<GraphiumCard {...card({ form: { script: '', root: '' } })} />)).toContain('import-graphium-script')
  })
})

describe('進み', () => {
  const job = (o = {}) => ({ phase: 'running' as const, source: 'graphium', stage: 'pairs' as const, done: 132, total: 289, ...o })
  it('今の段に印・pairs は n / N 組・棒は割合・取り消す', () => {
    const html = renderToStaticMarkup(<ImportProgress job={job()} canCancel onCancel={noop} />)
    expect(html).toContain('data-step="copy" data-state="done"')
    expect(html).toContain('data-step="pairs" data-state="cur"')
    expect(html).toContain('aria-current="step"')
    expect(html).toContain('progress.pairsCount')
    expect(html).toContain('width:46%')
    expect(html).toContain('progress.note')
    const onCancel = vi.fn()
    press(byTestId(ImportProgress({ job: job(), canCancel: true, onCancel }), 'import-cancel')[0])
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
  it('数が無ければ棒は流れる・ジョブ id が無い間は取り消せない', () => {
    const html = renderToStaticMarkup(<ImportProgress job={job({ stage: 'copy', done: null, total: null })} canCancel={false} onCancel={noop} />)
    expect(html).toContain('is-flow')
    expect(html).toMatch(/disabled=""[^>]*data-testid="import-cancel"|data-testid="import-cancel"[^>]*disabled=""/)
  })
  it('失敗は alert・取り消しは status', () => {
    const f = renderToStaticMarkup(<ImportNotice job={{ phase: 'failed', source: 'a', error: '409' }} onDismiss={noop} />)
    expect(f).toContain('role="alert"')
    const c = renderToStaticMarkup(<ImportNotice job={{ phase: 'cancelled', source: 'a' }} onDismiss={noop} />)
    expect(c).toContain('progress.cancelled')
  })
})

describe('結果の 1 枚', () => {
  const props = (r = result(), over = {}) => ({ result: r, name: '実験ノート', lang: 'ja' as const, onAsk: noop, onGraph: noop, onClose: noop, onOpenSettings: noop, ...over })
  const qs = Array.from({ length: 20 }, (_, i) => ({ op: 'o', term: { id: `t${i}`, name: `語${i}` }, text: `問い${i}`, count: 1 }))
  it('問いは先頭 5・押すとその文が渡る・「ほか」・知識グラフを見る', () => {
    const r = result({ questions: qs, questions_total: 38 })
    const onAsk = vi.fn()
    const onGraph = vi.fn()
    const tree = ImportResultCard(props(r, { onAsk, onGraph }))
    const rows = byTestId(tree, 'import-question')
    expect(rows).toHaveLength(5)
    press(rows[2])
    expect(onAsk).toHaveBeenCalledWith('問い2')
    press(byTestId(tree, 'import-see-graph')[0])
    expect(onGraph).toHaveBeenCalledTimes(1)
    expect(renderToStaticMarkup(<ImportResultCard {...props(r)} />)).toContain('result.more')
  })
  it('閉じる・AI 未設定の 1 行（設定を開く）・落とした数は 0 のとき出さない', () => {
    const onClose = vi.fn()
    press(byTestId(ImportResultCard(props(result(), { onClose })), 'import-close-result')[0])
    expect(onClose).toHaveBeenCalled()
    const html = renderToStaticMarkup(<ImportResultCard {...props(result({}, { skipped: 'ai_unset' }))} />)
    expect(html).toContain('data-skip="ai_unset"')
    expect(html).toContain('graphium.openSettings')
    expect(html).not.toContain('import-lost')
    expect(renderToStaticMarkup(<ImportResultCard {...props(result({}, { failed: 2 }))} />)).toContain('import-lost')
  })
  it('問いが 0 なら問いの欄を出さない', () => {
    expect(renderToStaticMarkup(<ImportResultCard {...props()} />)).not.toContain('result.questions')
  })
})

describe('取り込んだものの一覧', () => {
  it('行・写し直す（MCP 由来だけ）・外す', () => {
    const html = renderToStaticMarkup(<ImportSources {...srcProps({ sources: [src(), src({ source: 'bundle-a' })] })} />)
    expect(html.match(/data-testid="import-source-row"/g)).toHaveLength(2)
    expect(html.match(/data-testid="import-rerun"/g)).toHaveLength(1)
    expect(html.match(/data-testid="import-remove"/g)).toHaveLength(2)
  })
  it('写し以外のデータセットも同じ表の行（行を押すと id が渡る・操作は空）', () => {
    const onOpen = vi.fn()
    const row = { id: 'prices', name: '価格表', published: true, paper: false, kinds: 2, rows: 30, created: new Date('2026-11-10T09:00:00Z') }
    const tree = ImportSources(srcProps({ sources: [src()], rows: [row], onOpen }))
    const trs = byTestId(tree, 'import-ds-row')
    expect(trs).toHaveLength(1)
    press(trs[0])
    expect(onOpen).toHaveBeenCalledWith('prices')
    const html = renderToStaticMarkup(<ImportSources {...srcProps({ sources: [src()], rows: [row], onOpen })} />)
    expect(html.match(/data-testid="import-remove"/g)).toHaveLength(1)
  })
  it('押すと、その取り込み元の名前が渡る', () => {
    const onRerun = vi.fn()
    const onAskRemove = vi.fn()
    const tree = ImportSources(srcProps({ sources: [src({ source: 'a' }), src({ source: 'b' })], rerunnable: ['a', 'b'], onRerun, onAskRemove }))
    const reruns = byTestId(tree, 'import-rerun')
    press(reruns[1])
    expect(onRerun).toHaveBeenCalledWith('b')
    press(byTestId(tree, 'import-remove')[0])
    expect(onAskRemove).toHaveBeenCalledWith('a')
  })
  it('外す: 確認が開き、「外す」で onRemove・「やめる」で閉じる', () => {
    const html = renderToStaticMarkup(<ImportSources {...srcProps({ confirming: 'graphium' })} />)
    expect(html).toContain('import-confirm"')
    expect(html).toContain('sources.confirmRemove')
    expect(html).not.toContain('data-testid="import-rerun"')
    const onRemove = vi.fn()
    const onAskRemove = vi.fn()
    const tree = ImportSources(srcProps({ confirming: 'graphium', onRemove, onAskRemove }))
    press(byTestId(tree, 'import-confirm-remove')[0])
    expect(onRemove).toHaveBeenCalledWith('graphium')
    press(byTestId(tree, 'import-confirm-cancel')[0])
    expect(onAskRemove).toHaveBeenCalledWith(null)
  })
  it('走っている行は「走っています」・自分のジョブのときは出さない・操作は止まる', () => {
    const html = renderToStaticMarkup(<ImportSources {...srcProps({ sources: [src({ running: 'import' })] })} />)
    expect(html).toContain('import-running')
    expect(html).toMatch(/disabled=""[^>]*data-testid="import-remove"|data-testid="import-remove"[^>]*disabled=""/)
    expect(renderToStaticMarkup(<ImportSources {...srcProps({ sources: [src({ running: 'import' })], jobSource: 'graphium' })} />)).not.toContain('import-running')
  })
})

describe('つなぐ手順（prepareGraphium）', () => {
  const tool = (name: string) => ({ name })
  const ALL = ['list_media', 'get_media_file', 'add_material', 'export_asterism_knowledge'].map(tool)
  const reg = { transport: 'stdio' as const, command: '/n', args: ['/old.mjs'], env: { GRAPHIUM_ROOT: '/oldv' }, enabled: true }
  /** メモリの設定。apply は本物と同じ全部当てを真似る。 */
  function mk(init: Record<string, unknown>, over: Partial<ConnectDeps> = {}) {
    const mem: Record<string, unknown> = { ...init }
    const puts: string[] = []
    const deps: ConnectDeps = {
      locate: vi.fn(async () => ({ found: '/usr/bin/node', user_home: '/u' })),
      apply: vi.fn((_p, input) => {
        mem.mcpServers = { ...((mem.mcpServers as object) ?? {}), graphium: { transport: 'stdio', command: input.nodePath, args: [input.scriptPath], env: { GRAPHIUM_ROOT: input.root }, enabled: true } }
        mem.sourceHome = { server: 'graphium' }
        mem.sourceProvider = { list: 'list_media', get: 'get_media_file', add: 'add_material', knowledge: 'export_asterism_knowledge' }
        mem.sendOriginalsToHome = true
        return { servers: {}, provider: { list: '', get: '', add: '' } }
      }) as unknown as ConnectDeps['apply'],
      tools: vi.fn(async () => ALL),
      io: {
        get: <T,>(k: string) => mem[k] as T | undefined,
        put: (k, v) => {
          puts.push(k)
          mem[k] = v
        },
        flush: async () => {},
      },
      ...over,
    }
    return { mem, puts, deps }
  }
  const form = { script: '/g.mjs', root: '/v' }

  it('登録があれば設定は書き換えない（apply も呼ばない・knowledge があれば何も書かない）', async () => {
    const { mem, puts, deps } = mk({ mcpServers: { graphium: reg }, sourceHome: { server: 'other' }, sourceProvider: { list: 'a', get: 'b', add: 'c', knowledge: 'my_export' } })
    deps.tools = vi.fn(async () => [tool('my_export')])
    expect(await prepareGraphium(form, '/u', deps)).toBeNull()
    expect(deps.apply).not.toHaveBeenCalled()
    expect(puts).toEqual([])
    expect((mem.sourceProvider as { knowledge: string }).knowledge).toBe('my_export')
  })
  it('登録があって knowledge の名前が無いときは、それだけを足す（list/get/add・sourceHome はそのまま）', async () => {
    const { mem, puts, deps } = mk({ mcpServers: { graphium: reg }, sourceHome: { server: 'other' }, sourceProvider: { list: 'a', get: 'b', add: 'c' } })
    expect(await prepareGraphium(form, '/u', deps)).toBeNull()
    expect(puts).toEqual(['sourceProvider'])
    expect(mem.sourceProvider).toEqual({ list: 'a', get: 'b', add: 'c', knowledge: 'export_asterism_knowledge' })
    expect(mem.sourceHome).toEqual({ server: 'other' })
    expect(mem.mcpServers).toEqual({ graphium: reg })
  })
  it('登録が無く、取り込み元が別のサーバなら、登録と knowledge だけ足す', async () => {
    const { mem, deps } = mk({ mcpServers: { lab: reg }, sourceHome: { server: 'lab' }, sourceProvider: { list: 'a', get: 'b', add: 'c' } })
    expect(await prepareGraphium(form, '/u', deps)).toBeNull()
    expect(deps.apply).not.toHaveBeenCalled()
    expect(Object.keys(mem.mcpServers as object).sort()).toEqual(['graphium', 'lab'])
    expect(mem.sourceHome).toEqual({ server: 'lab' })
    expect(mem.sourceProvider).toEqual({ list: 'a', get: 'b', add: 'c', knowledge: 'export_asterism_knowledge' })
  })
  it('登録が無く、取り込み元も空なら、今までどおりプリセットを全部当てる・~/ はフルパスに', async () => {
    const { mem, deps } = mk({})
    expect(await prepareGraphium({ script: '~/G/x.mjs', root: '~/V' }, '/Users/me', deps)).toBeNull()
    expect(deps.apply).toHaveBeenCalledWith(expect.objectContaining({ serverName: 'graphium' }), { nodePath: '/usr/bin/node', scriptPath: '/Users/me/G/x.mjs', root: '/Users/me/V' })
    expect(mem.sourceHome).toEqual({ server: 'graphium' })
  })
  it('入力が無ければ form・node が無ければ node', async () => {
    expect(await prepareGraphium({ script: '', root: '' }, '/u', mk({}).deps)).toEqual({ code: 'form' })
    const noNode = mk({}, { locate: vi.fn(async () => ({ found: null, user_home: '/u' })) })
    expect(await prepareGraphium(form, '/u', noNode.deps)).toEqual({ code: 'node' })
  })
  it('古い Graphium（knowledge の道具が無い）はジョブを始めずに止め、書いたものは全部（sendOriginalsToHome も）戻す', async () => {
    const { mem, deps } = mk({})
    deps.tools = vi.fn(async () => ALL.slice(0, 3))
    expect(await prepareGraphium(form, '/u', deps)).toEqual({ code: 'old', tool: 'export_asterism_knowledge' })
    expect(mem.mcpServers).toBeUndefined()
    expect(mem.sourceHome).toBeUndefined()
    expect(mem.sourceProvider).toBeUndefined()
    expect(mem.sendOriginalsToHome).toBeUndefined()
  })
  it('新しく繋いだのに list の道具が無ければ tool・例外は http で、どちらも元に戻す', async () => {
    const a = mk({})
    a.deps.tools = vi.fn(async () => [tool('export_asterism_knowledge')])
    expect(await prepareGraphium(form, '/u', a.deps)).toEqual({ code: 'tool', tool: 'list_media' })
    expect(a.mem.mcpServers).toBeUndefined()
    const b = mk({ mcpServers: { graphium: { ...reg, enabled: false } }, sourceProvider: { list: 'a', get: 'b', add: 'c' } })
    b.deps.tools = vi.fn(async () => {
      throw new Error('graphium は無効です')
    })
    expect(await prepareGraphium(form, '/u', b.deps)).toEqual({ code: 'http', detail: 'graphium は無効です' })
    expect(b.mem.sourceProvider).toEqual({ list: 'a', get: 'b', add: 'c' }) // 足した knowledge も戻る
    expect((b.mem.mcpServers as { graphium: { enabled: boolean } }).graphium.enabled).toBe(false)
  })
})
