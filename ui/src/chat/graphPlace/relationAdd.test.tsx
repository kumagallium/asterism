// 足した関係の画面（R3c）: 入力欄の純関数・API の形・節の出し分け・種類の行との結び・手で付ける欄の選択肢。
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string, o?: Record<string, unknown>) => (o ? `${key}${JSON.stringify(o)}` : key), i18n: { language: 'ja' } }),
}))

import { BUILTIN, getCatalog, reloadRelationCatalog, resetCatalogForTest } from '../../relations/catalog'
import { assignableRelations } from '../../relations/relationFns'
import { AddedRow, AddedSection } from './AddedSection'
import { type UnstatedGroup } from './knowledgeRelationsApi'
import { addFailureOf, addRelation, deleteRelation, failureText, fetchAdded, normalizeAdded, patchRelation, retireRelation, type AddedRelation } from './relationAddApi'
import { buildAdd, buildPatch, draftFor, editOptional, exampleNotes, otherLangHint, problemsOf, valuesOf } from './relationFormModel'
import { reflectChange } from './relationReflect'
import { UnstatedRow } from './UnstatedSection'
import type { AddedLoad } from './useAddedRelations'

const group = (over: Partial<UnstatedGroup> = {}): UnstatedGroup => ({
  key: '条件',
  label: '条件',
  count: 7,
  variants: [],
  examples: [
    { source: 's', pair: 'a|b', a: { id: 'a', name: '小麦粉' }, b: { id: 'b', name: '水' }, note: '水の量が条件になっている' },
    { source: 's', pair: 'a|c', a: { id: 'a', name: '小麦粉' }, b: { id: 'c', name: '塩' }, note: '水の量が条件になっている' },
    { source: 's', pair: 'a|d', a: { id: 'a', name: '小麦粉' }, b: { id: 'd', name: '砂糖' }, note: '温度が条件' },
    { source: 's', pair: 'a|e', a: { id: 'a', name: '小麦粉' }, b: { id: 'e', name: '油' }, note: null },
    { source: 's', pair: 'a|f', a: { id: 'a', name: '小麦粉' }, b: { id: 'f', name: '卵' }, note: '3 つ目' },
    { source: 's', pair: 'a|g', a: { id: 'a', name: '小麦粉' }, b: { id: 'g', name: '牛乳' }, note: '4 つ目' },
  ],
  inCatalog: null,
  ...over,
})

const added = (over: Partial<AddedRelation> = {}): AddedRelation => ({
  name: 'u_0a1b2c3d',
  label: { ja: '条件', en: 'Condition' },
  definition: { ja: '後ろの語が前の語の条件になっている', en: '' },
  symmetric: false,
  toPropertyOnly: true,
  exactMatch: null,
  retired: false,
  addedAt: '2026-11-21T01:02:03+00:00',
  used: 0,
  ...over,
})

const load = (relations: AddedRelation[], over: Partial<AddedLoad> = {}): AddedLoad => ({ status: 'ok', relations, unavailable: false, ...over })
const section = (l: AddedLoad, openName: string | null = null) =>
  renderToStaticMarkup(<AddedSection load={l} lang="ja" openName={openName} onToggle={() => {}} onChanged={() => {}} adder={<i data-x="adder" />} adding={false} onAdd={() => {}} />)

describe('入力欄の純関数', () => {
  it('種類から開くと表示名に種類の名前が入り、説明は空・例は理由を重複なしで 3 件まで', () => {
    const d = draftFor(group())
    expect(d.label).toBe('条件')
    expect(d.definition).toBe('')
    expect(exampleNotes(group())).toEqual(['水の量が条件になっている', '温度が条件', '3 つ目'])
    expect(draftFor(null).label).toBe('')
    expect(exampleNotes(null)).toEqual([])
  })
  it('必須・長さ（表示名 40・説明 200）・IRI の形', () => {
    const ok = { ...draftFor(null), label: 'あ', definition: 'い' }
    expect(problemsOf(ok)).toEqual([])
    expect(problemsOf({ ...ok, label: '' })).toContain('labelEmpty')
    expect(problemsOf({ ...ok, label: ' ' })).toContain('labelEmpty')
    expect(problemsOf({ ...ok, label: 'あ'.repeat(40) })).toEqual([])
    expect(problemsOf({ ...ok, label: 'あ'.repeat(41) })).toContain('labelLong')
    expect(problemsOf({ ...ok, definition: '' })).toContain('definitionEmpty')
    expect(problemsOf({ ...ok, definition: 'い'.repeat(200) })).toEqual([])
    expect(problemsOf({ ...ok, definition: 'い'.repeat(201) })).toContain('definitionLong')
    expect(problemsOf({ ...ok, exactMatch: 'not an iri' })).toContain('exactBad')
    expect(problemsOf({ ...ok, exactMatch: 'http://purl.obolibrary.org/obo/RO_0002233' })).toEqual([])
  })
  it('送る形: 画面の言語の欄だけ・unstated_key は種類から開いたときだけ', () => {
    const v = { label: ' 条件 ', definition: ' 説明 ', symmetric: true, toPropertyOnly: false, exactMatch: '' }
    expect(buildAdd(v, 'ja', '条件')).toEqual({ label: { ja: '条件' }, definition: { ja: '説明' }, symmetric: true, to_property_only: false, unstated_key: '条件' })
    expect(buildAdd(v, 'en')).toEqual({ label: { en: '条件' }, definition: { en: '説明' }, symmetric: true, to_property_only: false })
    expect(buildAdd({ ...v, exactMatch: ' https://x.example/p ' }, 'ja').exact_match).toBe('https://x.example/p')
  })
  it('直す: 表示名・説明・標準の述語だけ。画面の言語の欄は入力値・もう片方は元の値をそのまま（写しを送らない）', () => {
    const r = added({ exactMatch: 'https://x.example/p', definition: { ja: '後ろの語が前の語の条件になっている', en: '' } })
    expect(buildPatch(valuesOf(r, 'ja'), 'ja', r)).toEqual({ label: { ja: '条件' }, definition: { ja: '後ろの語が前の語の条件になっている' }, exact_match: 'https://x.example/p' })
    expect(Object.keys(buildPatch(valuesOf(added(), 'en'), 'en', added())).sort()).toEqual(['definition', 'exact_match', 'label'])
    expect(buildPatch(valuesOf(added(), 'ja'), 'ja', added()).exact_match).toBeNull()
  })
  it('ja だけの関係を英語の画面で直す: 初期値は空（ja を en の欄に写さない）・説明を英語で入れると ja は元のまま残して送る', () => {
    const r = added({ label: { ja: '条件', en: '' }, definition: { ja: '説明です', en: '' } })
    const v = valuesOf(r, 'en')
    expect(v.label).toBe('')
    expect(v.definition).toBe('')
    expect(editOptional(r, 'en')).toEqual({ label: true, definition: true })
    expect(problemsOf(v, editOptional(r, 'en'))).toEqual([])
    expect(problemsOf(v)).toContain('labelEmpty')
    expect(otherLangHint(r, 'en')).toEqual({ label: '条件', definition: '説明です' })
    // 触らなければ表示名は何も送らない（サーバは渡さない言語を生の値のまま残す）。説明は丸ごと置き換わるので元の値を添える
    expect(buildPatch(v, 'en', r)).toEqual({ label: {}, definition: { ja: '説明です' }, exact_match: null })
    // 英語で入れれば en は入力値・ja は元の値
    expect(buildPatch({ ...v, label: 'Condition', definition: 'B conditions A' }, 'en', r)).toEqual({ label: { en: 'Condition' }, definition: { ja: '説明です', en: 'B conditions A' }, exact_match: null })
  })
})

describe('API', () => {
  afterEach(() => vi.unstubAllGlobals())
  const stub = (status: number, body: unknown) => {
    const f = vi.fn(async () => new Response(JSON.stringify(body), { status }))
    vi.stubGlobal('fetch', f)
    return f
  }
  it('一覧は組み込みを捨て、足した関係と使われ方の合計を返す', async () => {
    stub(200, {
      relations: [
        { name: 'sub', builtin: true, label: { ja: 'x', en: 'x' } },
        { name: 'u_1', builtin: false, label: { ja: '条件', en: '' }, definition: { ja: '説', en: '' }, symmetric_pair: true, to_property_only: true, retired: true, added_at: '2026-01-01T00:00:00Z', usage: { lines: 1, ledger: 2, drafts: 3 } },
      ],
    })
    const r = await fetchAdded()
    expect(r).toHaveLength(1)
    expect(r[0]).toMatchObject({ name: 'u_1', symmetric: true, toPropertyOnly: true, retired: true, used: 6, exactMatch: null })
    expect(normalizeAdded({ relations: [{ name: 'u_2', builtin: false }] })[0].used).toBeNull()
  })
  it('足す・直す・やめる・削除の口と形', async () => {
    const f = stub(200, {})
    await addRelation({ label: { ja: 'a' }, definition: { ja: 'b' }, symmetric: false, to_property_only: false, unstated_key: 'k' })
    await patchRelation('u_1', { label: { ja: 'a' }, definition: { ja: 'b' }, exact_match: null })
    await retireRelation('u_1')
    await deleteRelation('u_1')
    const calls = f.mock.calls as unknown as [string, RequestInit][]
    expect(calls.map(([u, i]) => `${i.method} ${u}`)).toEqual(['POST /api/relations', 'PATCH /api/relations/u_1', 'POST /api/relations/u_1/retire', 'DELETE /api/relations/u_1'])
    expect(JSON.parse(String(calls[0][1].body))).toMatchObject({ unstated_key: 'k', label: { ja: 'a' } })
  })
  it('失敗の分類: 400 入力・409 既にある（名前を取る）・409 上限・削除の 409 は使用中・404・502 とネットワーク・401/503', async () => {
    const add = () => addRelation({ label: {}, definition: {}, symmetric: false, to_property_only: false }).catch((x: unknown) => x)
    stub(400, { detail: '表示名は 40 文字までです' })
    expect(addFailureOf(await add())).toEqual({ kind: 'input' })
    stub(409, { detail: '既にある関係（条件）' })
    expect(addFailureOf(await add())).toEqual({ kind: 'exists', name: '条件' })
    stub(409, { detail: '既にある関係' })
    expect(addFailureOf(await add())).toEqual({ kind: 'exists', name: null })
    stub(409, { detail: '足せる関係は 500 件までです' })
    expect(addFailureOf(await add())).toEqual({ kind: 'limit' })
    stub(409, { detail: { detail: 'relation is in use', usage: { lines: 1 } } })
    expect(addFailureOf(await deleteRelation('u').catch((x: unknown) => x), 'delete')).toEqual({ kind: 'inUse' })
    stub(404, { detail: 'relation not found: u' })
    expect(addFailureOf(await deleteRelation('u').catch((x: unknown) => x), 'delete')).toEqual({ kind: 'notFound' })
    stub(502, { detail: 'x' })
    expect(addFailureOf(await add())).toEqual({ kind: 'failed' })
    expect(addFailureOf(new TypeError('Failed to fetch'))).toEqual({ kind: 'failed' })
    for (const st of [401, 503]) {
      stub(st, { detail: 'x' })
      expect(addFailureOf(await add())).toEqual({ kind: 'unavailable' })
    }
  })
  it('画面の文言に直す（サーバの文は出さない）', () => {
    const t = (k: string, o?: Record<string, unknown>) => (o ? `${k}${JSON.stringify(o)}` : k)
    expect(failureText(t, { kind: 'exists', name: '条件' })).toBe('graphPlace:error.rel.exists{"name":"条件"}')
    expect(failureText(t, { kind: 'exists', name: null })).toBe('graphPlace:error.rel.existsPlain')
    expect(failureText(t, { kind: 'limit' })).toContain('graphPlace:error.rel.limit')
    expect(failureText(t, { kind: 'inUse' })).toBe('graphPlace:added.inUse')
    expect(failureText(t, { kind: 'notFound' })).toBe('graphPlace:error.rel.notFound')
    expect(failureText(t, { kind: 'failed' })).toBe('graphPlace:error.rel.failed')
    expect(failureText(t, { kind: 'input' })).toContain('graphPlace:error.rel.input')
  })
  it('使われ方の形が壊れていれば「分からない」（null）', () => {
    const one = (usage: unknown) => normalizeAdded({ relations: [{ name: 'u_1', builtin: false, usage }] })[0].used
    expect(one({ lines: 0, ledger: 0, drafts: 0 })).toBe(0)
    expect(one({ lines: 0, ledger: 0 })).toBeNull()
    expect(one({ lines: 0, ledger: '0', drafts: 0 })).toBeNull()
    expect(one({ lines: -1, ledger: 0, drafts: 0 })).toBeNull()
    expect(one({ lines: NaN, ledger: 0, drafts: 0 })).toBeNull()
    expect(one('x')).toBeNull()
  })
})

describe('節「足した関係 N」', () => {
  it('0 件でも見出しと「関係を足す」と空の説明を出す', () => {
    const h = section(load([]))
    expect(h).toContain('added.title{&quot;n&quot;:0}')
    expect(h).toContain('data-testid="gp-added-add"')
    expect(h).toContain('data-testid="gp-added-empty"')
  })
  it('読み込み中・失敗・401/503 は 1 行（401/503 では「足す」を出さない）', () => {
    expect(section(load([], { status: 'loading' }))).toContain('gp-added-loading')
    const failed = section(load([], { status: 'error' }))
    expect(failed).toContain('added.failed')
    expect(failed).toContain('gp-added-add')
    const un = section(load([], { status: 'error', unavailable: true }))
    expect(un).toContain('added.unavailable')
    expect(un).not.toContain('gp-added-add')
  })
  it('行: 向きの印・項目に限るの札・使われ方（未使用／使われている N）・やめた物', () => {
    const h = section(load([added(), added({ name: 'u_2', symmetric: true, toPropertyOnly: false, used: 3 }), added({ name: 'u_3', retired: true, used: 5 })]))
    expect(h).toContain('→')
    expect(h).toContain('↔')
    expect(h.match(/gp-added-toprop/g)).toHaveLength(2)
    expect(h).toContain('added.unused')
    expect(h).toContain('added.used{&quot;n&quot;:3}')
    expect(h).toContain('gp-added-retired')
    expect(h).toContain('gp-item--retired')
  })
  it('開いた行: 未使用は [削除]・使われていれば [使うのをやめる]・やめた物はどちらも出ない', () => {
    const open = (r: AddedRelation) => renderToStaticMarkup(<AddedRow r={r} lang="ja" open onToggle={() => {}} onChanged={() => {}} />)
    const un = open(added())
    expect(un).toContain('gp-added-delete')
    expect(un).not.toContain('data-testid="gp-added-retire"')
    expect(un).toContain('gp-added-edit')
    expect(un).toContain('gp-added-definition')
    expect(un).toContain('gp-added-date')
    const used = open(added({ used: 2 }))
    expect(used).toContain('data-testid="gp-added-retire"')
    expect(used).not.toContain('gp-added-delete')
    const dead = open(added({ used: 2, retired: true }))
    expect(dead).not.toContain('data-testid="gp-added-retire"')
    expect(dead).not.toContain('gp-added-delete')
    expect(dead).toContain('gp-added-edit')
    // 標準の述語は値があるときだけ
    expect(un).not.toContain('gp-added-exact')
    expect(open(added({ exactMatch: 'https://x.example/p' }))).toContain('gp-added-exact')
  })
  it('使われ方が分からない行: 削除は出さず、[使うのをやめる] を出す', () => {
    const h = renderToStaticMarkup(<AddedRow r={added({ used: null })} lang="ja" open onToggle={() => {}} onChanged={() => {}} />)
    expect(h).toContain('gp-added-usage-unknown')
    expect(h).not.toContain('data-testid="gp-added-delete"')
    expect(h).toContain('data-testid="gp-added-retire"')
  })
  it('読み直しの途中は書き込みのボタンが無効（古い行で直す・消す・やめるを押させない）', () => {
    const h = renderToStaticMarkup(<AddedRow r={added()} lang="ja" open onToggle={() => {}} onChanged={() => {}} busy />)
    expect(h).toMatch(/<button[^>]*disabled=""[^>]*data-testid="gp-added-edit"|<button[^>]*data-testid="gp-added-edit"[^>]*disabled=""/)
    expect(h).toMatch(/<button[^>]*data-testid="gp-added-delete"[^>]*disabled=""|<button[^>]*disabled=""[^>]*data-testid="gp-added-delete"/)
    const sec = section(load([added()], { busy: true }))
    expect(sec).toMatch(/<button[^>]*data-testid="gp-added-add"[^>]*disabled=""|<button[^>]*disabled=""[^>]*data-testid="gp-added-add"/)
    expect(section(load([added()]))).not.toMatch(/data-testid="gp-added-add"[^>]*disabled/)
  })
  it('値は文字として描く（HTML として解釈しない）', () => {
    const h = renderToStaticMarkup(<AddedRow r={added({ label: { ja: '<b>x</b>', en: '' }, definition: { ja: '<img src=x onerror=1>', en: '' } })} lang="ja" open onToggle={() => {}} onChanged={() => {}} />)
    expect(h).not.toContain('<b>x</b>')
    expect(h).not.toContain('<img')
    expect(h).toContain('&lt;b&gt;x&lt;/b&gt;')
  })
  it('やめていない物が 41 件以上のときだけ「先の 40 件まで」の 1 行（やめた物は数えない）', () => {
    const many = (n: number, retired = false) => Array.from({ length: n }, (_, i) => added({ name: `u_${retired ? 'r' : 'a'}${i}`, retired }))
    expect(section(load(many(40)))).not.toContain('gp-added-limit')
    expect(section(load([...many(40), ...many(5, true)]))).not.toContain('gp-added-limit')
    expect(section(load(many(41)))).toContain('gp-added-limit')
  })
  it('入力欄を開いているときは adder を出し、「関係を足す」ボタンは隠す', () => {
    const h = renderToStaticMarkup(<AddedSection load={load([])} lang="ja" openName={null} onToggle={() => {}} onChanged={() => {}} adder={<i data-x="adder" />} adding onAdd={() => {}} />)
    expect(h).toContain('data-x="adder"')
    expect(h).not.toContain('gp-added-add"')
  })
})

describe('「言えなかった関係の種類」との結び', () => {
  const add = { adding: null, onAdd: () => {}, adder: () => <i data-x="form" /> }
  const row = (g: UnstatedGroup, a: (Omit<typeof add, 'adding'> & { adding: string | null }) | null = add, open = true) => renderToStaticMarkup(<UnstatedRow group={g} lang="ja" open={open} onToggle={() => {}} add={a ?? undefined} />)
  it('開いた行の最後に [関係として足す]', () => {
    expect(row(group())).toContain('gp-unstated-add')
  })
  it('in_catalog がある行には出さない（札のまま）', () => {
    const h = row(group({ inCatalog: 'u_0a1b2c3d' }))
    expect(h).not.toContain('gp-unstated-add')
    expect(h).toContain('gp-unstated-incatalog')
  })
  it('その種類の入力欄を開いていれば、ボタンの代わりに入力欄', () => {
    const h = row(group(), { ...add, adding: '条件' })
    expect(h).toContain('data-x="form"')
    expect(h).not.toContain('gp-unstated-add')
  })
  it('ほかの書き方: 代表の名前そのもの・空は並べず、0 件なら行ごと出さない', () => {
    const h = row(group({ variants: [{ label: '条件', count: 5 }, { label: ' ', count: 1 }, { label: '', count: 1 }] }))
    expect(h).not.toContain('unstated.variants')
    expect(row(group({ variants: [{ label: '条件', count: 5 }, { label: '状況', count: 1 }] }))).toContain('状況')
  })
  it('閉じた行には出ない・add が無ければ従来どおり出ない', () => {
    expect(row(group(), add, false)).not.toContain('gp-unstated-add')
    expect(row(group(), null)).not.toContain('gp-unstated-add')
  })
})

describe('足した関係の言語の欠け（片方の言語だけで書かれた物）', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    resetCatalogForTest()
  })
  it('目録の形に通る: もう片方の言語はあるほうで埋まる・組み込みは触らない', async () => {
    const human = { name: 'u_1', label: { ja: '条件' }, sentence: { ja: '{a} → {b}' }, predicates: [{ name: 'u1', iri: 'https://x.example/u1', when: null }], symmetric_draft: false, symmetric_pair: false, experiment: false, builtin: false }
    const pred = { name: 'u1', iri: 'https://x.example/u1', shapes: { from: ['class'], to: ['class'] }, same_shape: false, roles: null, no_self: false, subsumption: false, symmetric: false, knowledge: true, own: { label: { ja: '条件' }, definition: '説', searched: [], date: '2026-11-21' } }
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ relations: [...BUILTIN.relations, human], predicates: [...BUILTIN.predicates, pred] }), { status: 200 })))
    await reloadRelationCatalog()
    const r = getCatalog().relations.find((x) => x.name === 'u_1')
    expect(r?.label).toEqual({ ja: '条件', en: '条件' })
    expect(r?.sentence.en).toBe('{a} → {b}')
    expect(getCatalog().relations.slice(0, BUILTIN.relations.length)).toEqual(BUILTIN.relations)
  })
})

describe('目録の読み直しの順序・失敗', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    resetCatalogForTest()
  })
  const body = (extra: object[]) => JSON.stringify({ relations: [...BUILTIN.relations, ...extra], predicates: BUILTIN.predicates })
  const human = (name: string) => ({ name, label: { ja: name, en: name }, sentence: { ja: '{a}', en: '{a}' }, predicates: [{ name: 'sub', iri: 'x', when: null }], symmetric_draft: false, symmetric_pair: false, experiment: false, builtin: false })
  it('後から始めた読み込みの結果だけを採る（先に始めて遅れて返った古い結果で上書きしない）', async () => {
    let releaseOld: (r: Response) => void = () => {}
    const old = new Promise<Response>((res) => { releaseOld = res })
    const calls: Promise<Response>[] = [old, Promise.resolve(new Response(body([human('u_new')]), { status: 200 }))]
    const f = vi.fn(() => calls.shift() as Promise<Response>)
    const p1 = reloadRelationCatalog(f as unknown as typeof fetch)
    const p2 = reloadRelationCatalog(f as unknown as typeof fetch)
    expect(await p2).toBe(true)
    releaseOld(new Response(body([human('u_old')]), { status: 200 }))
    expect(await p1).toBe(true)
    const names = getCatalog().relations.map((r) => r.name)
    expect(names).toContain('u_new')
    expect(names).not.toContain('u_old')
  })
  it('読み直しに失敗したら false（今の目録は残る）', async () => {
    expect(await reloadRelationCatalog(vi.fn(async () => new Response('x', { status: 500 })) as unknown as typeof fetch)).toBe(false)
    expect(await reloadRelationCatalog(vi.fn(async () => { throw new Error('down') }) as unknown as typeof fetch)).toBe(false)
    expect(getCatalog()).toBe(BUILTIN)
  })
})

describe('書いた直後の窓（reflectChange）', () => {
  const t = (k: string, o?: Record<string, unknown>) => (o ? `${k}${JSON.stringify(o)}` : k)
  const make = (reload: () => Promise<boolean>) => {
    const log: string[] = []
    return { log, d: { reload, bump: () => log.push('bump'), setBusy: (b: boolean) => log.push(b ? 'busy' : 'free'), notify: (x: string) => log.push(`notify:${x}`), t } }
  }
  it('読み直しが終わるまで busy・終わってから bump して free（遅い fetch でも窓が開いている）', async () => {
    let done: (ok: boolean) => void = () => {}
    const { log, d } = make(() => new Promise<boolean>((r) => { done = r }))
    const p = reflectChange(d)
    expect(log).toEqual(['busy'])
    done(true)
    await p
    expect(log).toEqual(['busy', 'bump', 'free'])
  })
  it('読み直しが失敗しても・例外でも、必ず窓を閉じる', async () => {
    const a = make(async () => false)
    await reflectChange(a.d, { done: '条件' })
    expect(a.log).toEqual(['busy', 'notify:added.doneReflectFailed{"name":"条件"}', 'bump', 'free'])
    const b = make(async () => { throw new Error('x') })
    await reflectChange(b.d)
    expect(b.log).toEqual(['busy', 'notify:added.reflectFailed', 'bump', 'free'])
  })
  it('404 の知らせを優先し、反映の失敗は後ろに添える（上書きしない）', async () => {
    const a = make(async () => false)
    await reflectChange(a.d, { msg: '見つかりません' })
    expect(a.log[1]).toBe('notify:見つかりません added.reflectFailedTail')
    const b = make(async () => true)
    await reflectChange(b.d, { msg: '見つかりません' })
    expect(b.log).toEqual(['busy', 'notify:見つかりません', 'bump', 'free'])
  })
})

describe('目録の読み込み: 譲った読み込み・時間切れ', () => {
  afterEach(() => {
    vi.useRealTimers()
    resetCatalogForTest()
  })
  it('後の読み込みに譲った古い読み込みは、失敗を返さず最後の結果を返す', async () => {
    let failOld: () => void = () => {}
    const old = new Promise<Response>((_, rej) => { failOld = () => rej(new Error('down')) })
    const f = vi.fn().mockReturnValueOnce(old).mockResolvedValueOnce(new Response(JSON.stringify({ relations: BUILTIN.relations, predicates: BUILTIN.predicates }), { status: 200 }))
    const p1 = reloadRelationCatalog(f as unknown as typeof fetch)
    const p2 = reloadRelationCatalog(f as unknown as typeof fetch)
    failOld()
    expect(await p1).toBe(true)
    expect(await p2).toBe(true)
  })
  it('15 秒で時間切れ（失敗として false）', async () => {
    vi.useFakeTimers()
    const p = reloadRelationCatalog((() => new Promise<Response>(() => {})) as unknown as typeof fetch)
    await vi.advanceTimersByTimeAsync(15001)
    expect(await p).toBe(false)
  })
})

describe('足した関係が手で付ける欄の選択肢に出る', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    resetCatalogForTest()
  })
  const remote = (retired: boolean) => ({
    relations: [
      ...BUILTIN.relations,
      { name: 'u_0a1b2c3d', label: { ja: '条件', en: 'Condition' }, sentence: { ja: '{a} は {b} が条件', en: '{a} conditioned by {b}' }, predicates: [{ name: 'u0a1b2c3d', iri: 'https://asterism.example/rel#u0a1b2c3d', when: null }], symmetric_draft: false, symmetric_pair: false, experiment: false, builtin: false, retired },
    ],
    predicates: [
      ...BUILTIN.predicates,
      { name: 'u0a1b2c3d', iri: 'https://asterism.example/rel#u0a1b2c3d', shapes: { from: ['class', 'property'], to: ['class', 'property'] }, same_shape: false, roles: null, no_self: false, subsumption: false, symmetric: false, knowledge: true, own: null },
    ],
  })
  it('読み直すと選択肢に出る・やめた物は出ない・消すと目録から消える', async () => {
    expect(assignableRelations()).not.toContain('u_0a1b2c3d')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(remote(false)), { status: 200 })))
    await reloadRelationCatalog()
    expect(getCatalog().relations.some((r) => r.name === 'u_0a1b2c3d')).toBe(true)
    expect(assignableRelations()).toContain('u_0a1b2c3d')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(remote(true)), { status: 200 })))
    await reloadRelationCatalog()
    expect(assignableRelations()).not.toContain('u_0a1b2c3d')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ relations: BUILTIN.relations, predicates: BUILTIN.predicates }), { status: 200 })))
    await reloadRelationCatalog()
    expect(getCatalog().relations.some((r) => r.name === 'u_0a1b2c3d')).toBe(false)
  })
})
