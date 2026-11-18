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
  const deps = (over: Partial<ConnectDeps> = {}): ConnectDeps => ({
    locate: vi.fn(async () => ({ found: '/usr/bin/node', user_home: '/u' })),
    apply: vi.fn(() => ({ servers: {}, provider: { list: '', get: '', add: '' } })),
    verify: vi.fn(async () => ({ ok: true, tool: 'list_media' })),
    ensure: vi.fn(async () => {}),
    ...over,
  })
  it('つながっていれば knowledge の道具名を足すだけ', async () => {
    const d = deps()
    expect(await prepareGraphium('on', { script: '', root: '' }, d)).toBeNull()
    expect(d.ensure).toHaveBeenCalled()
    expect(d.apply).not.toHaveBeenCalled()
  })
  it('入力が無ければ form・node が無ければ node・道具が無ければ tool', async () => {
    expect(await prepareGraphium('off', { script: '', root: '' }, deps())).toEqual({ code: 'form' })
    const noNode = deps({ locate: vi.fn(async () => ({ found: null, user_home: '/u' })) })
    expect(await prepareGraphium('off', { script: '/g.mjs', root: '/v' }, noNode)).toEqual({ code: 'node' })
    const noTool = deps({ verify: vi.fn(async () => ({ ok: false, tool: 'list_media' })) })
    expect(await prepareGraphium('off', { script: '/g.mjs', root: '/v' }, noTool)).toEqual({ code: 'tool', tool: 'list_media' })
  })
  it('入力があれば設定に書いて確かめ、成功なら null・例外は http', async () => {
    const d = deps()
    expect(await prepareGraphium('off', { script: '/g.mjs', root: '/v' }, d)).toBeNull()
    expect(d.apply).toHaveBeenCalledWith(expect.objectContaining({ serverName: 'graphium' }), { nodePath: '/usr/bin/node', scriptPath: '/g.mjs', root: '/v' })
    const boom = deps({ verify: vi.fn(async () => { throw new Error('MCP が落ちました') }) })
    expect(await prepareGraphium('off', { script: '/g.mjs', root: '/v' }, boom)).toEqual({ code: 'http', detail: 'MCP が落ちました' })
  })
})
