// 「言えなかった関係の種類」の節（R2）: 描画・開閉・札・ほかの書き方・読み込み中／失敗・0 件で出ない・API の読み方・hook の引き直し。
import type { ReactElement, ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

// 最小の hook 実行器: 呼び出し順に state/ref を持ち、useEffect は deps が変わったときだけ走る（DOM の環境が無いので）。
const cells: unknown[] = []
const effects: { deps: unknown[] | undefined }[] = []
let cursor = 0
let ecursor = 0
const pending: (() => void)[] = []
vi.mock('react', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react')>()),
  useState: (init: unknown) => {
    const i = cursor++
    if (!(i in cells)) cells[i] = init
    return [cells[i], (v: unknown) => { cells[i] = typeof v === 'function' ? (v as (c: unknown) => unknown)(cells[i]) : v }]
  },
  useRef: (init: unknown) => {
    const i = cursor++
    if (!(i in cells)) cells[i] = { current: init }
    return cells[i]
  },
  useEffect: (fn: () => void, deps?: unknown[]) => {
    const i = ecursor++
    const prev = effects[i]?.deps
    if (!prev || !deps || deps.some((d, k) => !Object.is(d, prev[k]))) pending.push(fn)
    effects[i] = { deps }
  },
}))
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string, o?: Record<string, unknown>) => (o ? `${key}${JSON.stringify(o)}` : key), i18n: { language: 'ja' } }),
}))

import { fetchUnstated, normalizeUnstated, type UnstatedGroup } from './knowledgeRelationsApi'
import { UnstatedRow, UnstatedSection } from './UnstatedSection'
import { useUnstated } from './useUnstated'

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

const group = (over: Partial<UnstatedGroup> = {}): UnstatedGroup => ({
  key: '条件',
  label: '条件',
  count: 7,
  variants: [{ label: '条件', count: 5 }, { label: '条件・', count: 2 }],
  examples: [
    { source: 's', pair: 'a|b', a: { id: 'a', name: '小麦粉' }, b: { id: 'b', name: '水' }, note: '文は 2 語を並べているだけ' },
    { source: 's', pair: 'a|c', a: { id: 'a', name: '小麦粉' }, b: { id: 'c', name: '塩' }, note: null },
  ],
  inCatalog: null,
  ...over,
})
const noop = () => {}
const sec = (over: Record<string, unknown> = {}) => ({ load: { status: 'ok' as const, groups: [group()] }, lang: 'ja' as const, openKey: null, onToggle: noop, shown: 10, onMore: noop, ...over })

describe('節の描画', () => {
  it('群が 0 のときは節ごと出さない（読み込み済み）', () => {
    expect(renderToStaticMarkup(<UnstatedSection {...sec({ load: { status: 'ok', groups: [] } })} />)).toBe('')
  })
  it('見出し・説明・種類の名前と「N 組」。ボタンは「もっと見る」以外に置かない', () => {
    const html = renderToStaticMarkup(<UnstatedSection {...sec()} />)
    expect(html).toContain('unstated.title')
    expect(html).toContain('unstated.note')
    expect(html).toContain('条件')
    expect(html).toContain('unstated.count{&quot;n&quot;:7}')
    expect(html.match(/<button/g)).toHaveLength(1) // 行の開閉だけ
    expect(html).not.toContain('gp-unstated-more')
  })
  it('読み込み中・失敗は 1 行。前の群があれば失敗でも残す', () => {
    expect(renderToStaticMarkup(<UnstatedSection {...sec({ load: { status: 'loading', groups: [] } })} />)).toContain('gp-unstated-loading')
    expect(renderToStaticMarkup(<UnstatedSection {...sec({ load: { status: 'error', groups: [] } })} />)).toContain('gp-unstated-failed')
    const kept = renderToStaticMarkup(<UnstatedSection {...sec({ load: { status: 'error', groups: [group()] } })} />)
    expect(kept).toContain('gp-unstated-failed')
    expect(kept).toContain('gp-unstated-row')
  })
  it('群が多いと shown 件で切り、「もっと見る」が残りを数える', () => {
    const many = Array.from({ length: 12 }, (_, i) => group({ key: `k${i}`, label: `種類${i}` }))
    const html = renderToStaticMarkup(<UnstatedSection {...sec({ load: { status: 'ok', groups: many } })} />)
    expect(html.match(/data-testid="gp-unstated-row"/g)).toHaveLength(10)
    expect(html).toContain('act.more{&quot;n&quot;:2}')
  })
})

describe('行', () => {
  const rowHtml = (g: UnstatedGroup, open: boolean) => renderToStaticMarkup(<ul><UnstatedRow group={g} lang="ja" open={open} onToggle={noop} /></ul>)
  it('閉じていると例の組を出さない。開くと「A ― B」と AI の理由', () => {
    expect(rowHtml(group(), false)).not.toContain('小麦粉')
    const open = rowHtml(group(), true)
    expect(open).toContain('小麦粉 ― 水')
    expect(open).toContain('文は 2 語を並べているだけ')
    expect(open).toContain('小麦粉 ― 塩')
  })
  it('見せていない組があれば「ほか N 組」', () => {
    expect(rowHtml(group(), true)).toContain('unstated.rest{&quot;n&quot;:5}')
    expect(rowHtml(group({ count: 2 }), true)).not.toContain('unstated.rest')
  })
  it('ほかの書き方は、群の名前と違う物だけ。無ければ出さない', () => {
    const html = rowHtml(group(), true)
    expect(html).toContain('unstated.variants')
    expect(html).toContain('条件・')
    expect(rowHtml(group({ variants: [{ label: '条件', count: 7 }] }), true)).not.toContain('unstated.variants')
  })
  it('in_catalog があれば「既にある関係」の札（関係の表示名で）。無ければ出さない', () => {
    expect(rowHtml(group({ inCatalog: 'lowers' }), false)).toContain('gp-unstated-incatalog')
    expect(rowHtml(group({ inCatalog: 'lowers' }), false)).toContain('下げる')
    expect(rowHtml(group(), false)).not.toContain('gp-unstated-incatalog')
  })
  it('見出しを押すと開閉が呼ばれる', () => {
    const onToggle = vi.fn()
    const tree = UnstatedRow({ group: group(), lang: 'ja', open: false, onToggle })
    const btn = byTestId(tree, 'gp-unstated-toggle')[0]
    ;(btn.props.onClick as () => void)()
    expect(onToggle).toHaveBeenCalledTimes(1)
  })
})

describe('API の読み方', () => {
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  afterEach(() => vi.unstubAllGlobals())
  it('lang と source を付けて読む。source が無ければ付けない', async () => {
    const urls: string[] = []
    vi.stubGlobal('fetch', async (u: string) => { urls.push(u); return json({ groups: [], total_pairs: 0 }) })
    await fetchUnstated('ja')
    await fetchUnstated('en', 'src 1')
    expect(urls).toEqual(['/api/relations/unstated?lang=ja', '/api/relations/unstated?lang=en&source=src%201'])
  })
  it('契約の形（snake_case）を直す。壊れた群・例は捨てる', () => {
    const r = normalizeUnstated({
      groups: [
        { key: '条件', label: '条件', count: 3, sources: { s: 3 }, variants: [{ label: '条件', count: 3 }, { count: 1 }], examples: [{ source: 's', pair: 'a|b', a: { id: 'a', name: 'あ' }, b: { id: 'b', name: 'い' }, note: '理由' }, { a: 1 }], in_catalog: 'lowers' },
        { key: '', label: 'x', count: 1 },
        { key: 'y', label: 'y', count: 0 },
        'bad',
      ],
      total_pairs: 3,
    })
    expect(r.totalPairs).toBe(3)
    expect(r.groups).toHaveLength(1)
    expect(r.groups[0]).toMatchObject({ key: '条件', count: 3, inCatalog: 'lowers', variants: [{ label: '条件', count: 3 }] })
    expect(r.groups[0].examples).toEqual([{ source: 's', pair: 'a|b', a: { id: 'a', name: 'あ' }, b: { id: 'b', name: 'い' }, note: '理由' }])
  })
  it('形が違う返り（groups が無い）は失敗', async () => {
    vi.stubGlobal('fetch', async () => json({ nope: 1 }))
    await expect(fetchUnstated('ja')).rejects.toThrow()
  })
})

describe('読み直し（useUnstated）', () => {
  const useRun = (trigger: unknown, source: string | undefined, enabled: boolean, fetcher: typeof fetchUnstated) => {
    cursor = 0
    ecursor = 0
    const out = useUnstated(trigger, 'ja', source, enabled, fetcher)
    for (const f of pending.splice(0)) f()
    return out
  }
  const reset = () => { cells.length = 0; effects.length = 0 }
  it('台帳の読み直し（trigger の変化）・取り込み元の絞り込みの変化で引き直す。同じなら読まない・無効の間は読まない', async () => {
    reset()
    const fetcher = vi.fn(async (_l: 'ja' | 'en', s?: string) => ({ groups: [group({ label: s ?? 'all' })], totalPairs: 1 }))
    const t1 = [1]
    useRun(t1, undefined, false, fetcher)
    expect(fetcher).not.toHaveBeenCalled()
    useRun(t1, undefined, true, fetcher)
    expect(fetcher).toHaveBeenCalledTimes(1)
    useRun(t1, undefined, true, fetcher)
    expect(fetcher).toHaveBeenCalledTimes(1)
    useRun([2], undefined, true, fetcher) // 台帳が読み直された
    expect(fetcher).toHaveBeenCalledTimes(2)
    useRun([2], 'src-a', true, fetcher) // 取り込み元で絞った
    expect(fetcher).toHaveBeenLastCalledWith('ja', 'src-a')
    await Promise.resolve()
    await Promise.resolve()
    expect(useRun([2], 'src-a', true, fetcher)).toEqual({ status: 'ok', groups: [expect.objectContaining({ label: 'src-a' })] })
  })
  it('失敗したら error。前の群は残す', async () => {
    reset()
    let fail = false
    const fetcher = vi.fn(async () => { if (fail) throw new Error('x'); return { groups: [group()], totalPairs: 1 } })
    useRun([1], undefined, true, fetcher)
    await Promise.resolve(); await Promise.resolve()
    fail = true
    useRun([2], undefined, true, fetcher)
    await Promise.resolve(); await Promise.resolve()
    const r = useRun([2], undefined, true, fetcher)
    expect(r.status).toBe('error')
    expect(r.groups).toHaveLength(1)
  })
})
