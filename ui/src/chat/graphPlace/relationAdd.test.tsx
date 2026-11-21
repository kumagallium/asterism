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
import { KnowledgeApiError, type UnstatedGroup } from './knowledgeRelationsApi'
import { addFailureOf, addRelation, deleteRelation, fetchAdded, normalizeAdded, patchRelation, retireRelation, type AddedRelation } from './relationAddApi'
import { buildAdd, buildPatch, draftFor, exampleNotes, problemsOf, valuesOf } from './relationFormModel'
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
  it('直す: 表示名・説明・標準の述語だけ（向き・項目に限るは送らない）', () => {
    const p = buildPatch(valuesOf(added({ exactMatch: 'https://x.example/p' }), 'ja'), 'ja')
    expect(p).toEqual({ label: { ja: '条件' }, definition: { ja: '後ろの語が前の語の条件になっている' }, exact_match: 'https://x.example/p' })
    expect(Object.keys(buildPatch(valuesOf(added(), 'en'), 'en')).sort()).toEqual(['definition', 'exact_match', 'label'])
    expect(buildPatch(valuesOf(added(), 'ja'), 'ja').exact_match).toBeNull()
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
  it('失敗: 400・409 は文をそのまま・409（使用中）は detail の入れ子も読む・401/503 は「足せない」', async () => {
    stub(409, { detail: '既にある関係（条件）' })
    const e = await addRelation({ label: {}, definition: {}, symmetric: false, to_property_only: false }).catch((x: unknown) => x)
    expect(addFailureOf(e)).toEqual({ kind: 'message', message: '既にある関係（条件）' })
    stub(400, { detail: '表示名は 40 文字までです' })
    expect(addFailureOf(await retireRelation('u').catch((x: unknown) => x))).toEqual({ kind: 'message', message: '表示名は 40 文字までです' })
    stub(409, { detail: { detail: 'relation is in use', usage: { lines: 1 } } })
    expect((await deleteRelation('u').catch((x: unknown) => x) as KnowledgeApiError).message).toBe('relation is in use')
    for (const st of [401, 503]) {
      stub(st, { detail: 'x' })
      expect(addFailureOf(await addRelation({ label: {}, definition: {}, symmetric: false, to_property_only: false }).catch((x: unknown) => x))).toEqual({ kind: 'unavailable' })
    }
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
