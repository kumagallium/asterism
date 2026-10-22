import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { ThemesApiError } from '../themes/themesApi'
import type { Point, Reply, SessionSummary, Turn } from './chatApi'
import { PointCard } from './PointCard'
import { ChatPane } from './ChatPane'
import { AddPanel } from './AddPanel'
import { Composer } from './Composer'
import { HandForm } from './HandForm'
import { ReplyCard, SENTENCE_LIMIT } from './ReplyCard'
import { SessionList } from './SessionList'

const noop = () => {}
const reply = (over: Partial<Reply> = {}): Reply => ({
  text: '問い「熱伝導率を下げる要素は？」を覚えました。',
  ops: [],
  sentences: [],
  questions: [],
  bindings: [],
  suggestions: [],
  notes: [],
  answer: null,
  table: null,
  ...over,
})
const turn = (over: Partial<Turn> = {}): Turn => ({
  id: 't1',
  at: '2026-10-22T00:00:00Z',
  user: { text: 'q', attachments: [] },
  reply: reply(),
  batch: 'cabc12345x:t1',
  state: 'proposed',
  before: null,
  commit: null,
  by: 'human',
  attachment: null,
  job_id: null,
  ...over,
})
const card = (t: Turn, canConfirm = true, count = 2) =>
  renderToStaticMarkup(<ReplyCard turn={t} canConfirm={canConfirm} count={count} busy={false} onConfirm={noop} onRevert={noop} onAsk={noop} />)
const text = (html: string) => html.replace(/<[^>]*>/g, '')

const summary = (over: Partial<SessionSummary> = {}): SessionSummary => ({
  id: 'cabc12345x',
  title: '熱電材料',
  theme_id: null,
  draft_id: 'd1',
  created_at: '0',
  updated_at: '1',
  turns: 2,
  proposed: 2,
  ...over,
})
const list = (over: Partial<Parameters<typeof SessionList>[0]> = {}) =>
  renderToStaticMarkup(
    <SessionList
      sessions={[summary(), summary({ id: 'cdef67890y', title: '', proposed: 0 })]}
      error={null}
      activeId="cabc12345x"
      folded={false}
      showToggle
      onToggle={noop}
      onNew={noop}
      onOpen={noop}
      onOpenSettings={noop}
      onChanged={noop}
      onDeleted={noop}
      {...over}
    />,
  )

describe('ReplyCard', () => {
  it('route: chat の返事は札が出ず、「知識グラフに足す」のチップが出る', () => {
    const t = turn({ route: 'chat', state: 'none', by: 'ai', reply: reply({ text: 'どういたしまして' }) })
    const html = renderToStaticMarkup(<ReplyCard turn={t} canConfirm={false} count={0} busy={false} onConfirm={noop} onRevert={noop} onAsk={noop} onPropose={noop} />)
    expect(html).toContain('どういたしまして')
    expect(html).not.toContain('data-testid="reply-card"')
    expect(html).toContain('data-testid="reply-propose"')
    expect(html).toContain('知識グラフに足す')
  })
  it('未確認の最後の返事には 確認＋件数と「戻す」。札の上の行はこの手番の件数', () => {
    const html = card(turn({ reply: reply({ ops: [{ op: 'add_concept' }, { op: 'add_relation' }] }) }))
    expect(html).toContain('data-testid="reply-confirm"')
    expect(html).toContain('data-testid="reply-revert"')
    expect(text(html)).toContain('承認 2 件')
    expect(text(html)).toContain('知識グラフへの提案 · 2 件')
  })
  it('最後でない未確認の返事には ✓ を出さず、「戻す」は残る', () => {
    const html = card(turn(), false)
    expect(html).not.toContain('reply-confirm')
    expect(html).toContain('reply-revert')
  })
  it('state: none の返事には ✓ も「戻す」も出さない', () => {
    const html = card(turn({ state: 'none' }))
    expect(html).not.toContain('reply-confirm')
    expect(html).not.toContain('reply-revert')
  })
  it('確認済みは日時つきの印、取り消し済みはその印だけ', () => {
    expect(text(card(turn({ state: 'confirmed', commit: { theme_id: 'x', minted: [], lines: 1, questions: [], batch: 'b', at: '2026-10-22T09:30:00Z' } }), false))).toContain('承認済み ·')
    const rev = card(turn({ state: 'reverted' }), false)
    expect(text(rev)).toContain('取り消し済み')
    expect(rev).not.toContain('reply-revert')
  })
  it('文は 6 行まで・残りは「ほか N 件」', () => {
    const sentences = Array.from({ length: SENTENCE_LIMIT + 3 }, (_, i) => `文${i}`)
    const html = card(turn({ reply: reply({ sentences }) }))
    expect(html).toContain('ほか 3 件')
    expect(html).toContain('文0')
    expect(html).toContain(`文${SENTENCE_LIMIT + 2}`)
  })
  it('チップと notes の全 kind を描ける・commit_error は赤い 1 行', () => {
    const kinds = ['ai_unset', 'ai_failed', 'dropped', 'no_question', 'commit_error', 'info']
    const html = card(
      turn({
        reply: reply({
          questions: ['熱伝導率を下げる要素は？'],
          notes: kinds.map((kind) => ({ kind, text: `注記-${kind}`, choice: null })),
        }),
      }),
    )
    for (const k of kinds) expect(html).toContain(`注記-${k}`)
    expect(html).toContain('class="chat-chip"')
    expect(html).toMatch(/class="promote-err"[^>]*data-note-kind="commit_error"/)
  })
  it('文言に「そう」「線」「ことば」「下書き」「置く」は無い', () => {
    expect(text(card(turn()))).not.toMatch(/そう|線|ことば|下書き|置く|pending/)
  })
})

describe('表を添える（クリップ）', () => {
  const binding = (column: string, decided: 'bind' | 'skip' = 'bind') => ({ column, label: column, term: column, term_label: column, relation: 'eq', subject: '', decided })
  it('入力欄にクリップ（隠し input・表の拡張子だけ）があり、絵文字は使わない', () => {
    const html = renderToStaticMarkup(<Composer onSend={async () => true} onAttach={noop} busy={false} noKey={false} onOpenSettings={noop} />)
    expect(html).toContain('data-testid="chat-attach"')
    expect(html).toMatch(/type="file"[^>]*multiple|multiple[^>]*type="file"/)
    expect(html).toContain('accept=".csv,.tsv,.xlsx,.xls,.json"')
    expect(text(html)).not.toContain('📎')
    expect(html).toContain('<svg')
    expect(html).toContain('data-testid="chat-send"')
  })
  it('running の手番は「読んでいます…（段の 1 行）」で、✓ も戻すも出さない', () => {
    const html = renderToStaticMarkup(
      <ReplyCard turn={turn({ state: 'running', job_id: 'j1' })} canConfirm count={0} busy={false} onConfirm={noop} onRevert={noop} onAsk={noop} progress="列の意味を調べています" />,
    )
    expect(html).toContain('chat-thinking')
    expect(html).toContain('data-testid="reply-reading"')
    expect(text(html)).toContain('読んでいます…（列の意味を調べています）')
    expect(html).not.toContain('reply-confirm')
    expect(html).not.toContain('reply-revert')
  })
  it('bindings は 1 行ずつ「結ぶ／結ばない」のラジオ・既定は decided', () => {
    const html = card(turn({ attachment: { dataset_id: 'd', name: '価格表.csv', rows: 12, kinds: [], id_column: 'id' }, reply: reply({ bindings: [binding('価格'), binding('備考', 'skip')] }) }))
    expect(html.match(/<li class="chat-binding"/g)).toHaveLength(2)
    expect(text(html)).toContain('列「価格」は「価格」と同じもの')
    expect(text(html)).toContain('結ぶ')
    expect(text(html)).toContain('結ばない')
    expect(html).toContain('name="bind-t1-0"')
    expect(text(html)).toContain('価格表.csv・12 行')
    expect(text(html)).not.toContain('📎')
    const radios = [...html.matchAll(/<input[^>]*name="bind-t1-(0|1)"[^>]*>/g)].map((m) => m[0])
    expect(radios.filter((r) => r.includes('checked'))).toHaveLength(2)
  })
  it('choice はラジオ 1 組＋「決める」・決めたあとは印だけ', () => {
    const choice = { label: 'ID にする列', options: [{ value: 'id', label: '番号' }, { value: 'code', label: '記号' }], default: 'id' }
    const html = card(turn({ reply: reply({ notes: [{ kind: 'choice_id', text: 'ID の候補が 2 つあります', choice }] }) }))
    // onChoose を渡さない描画では「決める」は出ない（渡した描画で確かめる）
    const withBtn = renderToStaticMarkup(
      <ReplyCard turn={turn({ reply: reply({ notes: [{ kind: 'choice_id', text: 'x', choice }] }) })} canConfirm count={0} busy={false} onConfirm={noop} onRevert={noop} onAsk={noop} onChoose={noop} />,
    )
    expect(html).not.toContain('choice-decide')
    expect(withBtn).toContain('data-testid="choice-decide"')
    expect(text(withBtn)).toContain('決める')
    expect(withBtn.match(/type="radio"/g)).toHaveLength(2)
    const done = renderToStaticMarkup(
      <ReplyCard turn={turn({ reply: reply({ notes: [{ kind: 'choice_id', text: 'x', choice: { ...choice, chosen: 'code' } }] }) })} canConfirm count={0} busy={false} onConfirm={noop} onRevert={noop} onAsk={noop} onChoose={noop} />,
    )
    expect(done).not.toContain('choice-decide')
    expect(text(done)).toContain('決めた: 記号')
  })
  it('📎 の手番に「機械」は出ない・名前の一致には出る', () => {
    const at = { dataset_id: 'd', name: 'a.csv', rows: 1, kinds: [], id_column: 'id' }
    expect(text(card(turn({ by: 'machine', attachment: at })))).not.toContain('自動（名前の一致）')
    expect(text(card(turn({ by: 'machine', user: null, reply: reply({ suggestions: [{ subject: 'a', term: 'b', relation: 'eq' }] }) })))).toContain('自動（名前の一致）')
  })
  it('選び待ちに ✓ は無く、表の手番の ✓ は「公開」', () => {
    const choice = { label: 'L', options: [{ value: 'a', label: 'A' }], default: 'a' }
    const wait = card(turn({ reply: reply({ notes: [{ kind: 'choice_id', text: 'x', choice }] }) }), false)
    expect(wait).not.toContain('reply-confirm')
    const pub = card(turn({ attachment: { dataset_id: 'd', name: 'a.csv', rows: 1, kinds: [], id_column: 'id' } }))
    expect(text(pub)).toContain('公開')
    expect(text(pub)).not.toContain('件')
  })
  it('機械の手番は suggestions を 1 行ずつ・新しい kind も描ける', () => {
    const html = card(turn({ by: 'machine', user: null, reply: reply({ text: '', suggestions: [{ subject: '価格', term: '値段', relation: 'eq' }] }) }))
    expect(text(html)).toContain('「価格」は「値段」と同じもの')
    const kinds = ['shape', 'ingest_stop', 'attach_failed', 'unsupported']
    const notes = card(turn({ reply: reply({ notes: kinds.map((kind) => ({ kind, text: `注記-${kind}`, choice: null })) }) }))
    for (const k of kinds) expect(notes).toContain(`chat-note chat-note--${k}`)
    expect(text(notes)).not.toMatch(/そう|線|ことば|下書き|置く|pending/)
  })
})

describe('AddPanel（AI 未設定のとき右に出すかんたん）', () => {
  it('上に「データを添える」と「閉じる」・sky-add-area の見た目', () => {
    const html = renderToStaticMarkup(<AddPanel onClose={noop} />)
    expect(html).toContain('class="sky-add-area chat-add-area"')
    expect(html).toContain('data-testid="add-area"')
    expect(html).toContain('data-testid="add-close"')
    expect(text(html)).toContain('データを添える')
    expect(text(html)).toContain('閉じる')
  })
})

describe('SessionList', () => {
  it('行・新しいチャット・提案の印・題が空なら「新しいチャット」', () => {
    const html = list()
    expect(html.match(/data-testid="session-row"/g)).toHaveLength(2)
    expect(html).toContain('data-testid="session-new"')
    expect(html).toContain('data-testid="list-toggle"')
    expect(text(html)).toContain('熱電材料')
    expect(text(html)).toContain('提案 2')
  })
  it('鍵が無い（401）ときは一覧の代わりに 1 行', () => {
    const html = list({ error: new ThemesApiError('x', 401, '') })
    expect(html).not.toContain('session-row')
    expect(text(html)).toContain('設定 › サーバで鍵を入れると、チャットを保存できます')
  })
  it('たたむと一覧の開閉だけ（新しいチャットも一覧も出ない）', () => {
    const html = list({ folded: true })
    expect(html).toContain('list-toggle')
    expect(html).not.toContain('session-new')
    expect(html).not.toContain('session-row')
  })
})

describe('HandForm', () => {
  const draft = {
    draft_id: 'd1',
    theme_label: '',
    questions: [],
    relations: [],
    concepts: [
      { key: 'h1', label: '熱伝導率', kind: 'property' as const, use: 'new' },
      { key: 'h2', label: '散乱', kind: 'class' as const, use: 'new' },
    ],
  }
  it('概念が 2 つ以上あれば関係の行（7 つ）も出る', () => {
    const html = renderToStaticMarkup(<HandForm draft={draft} nextKey="h3" busy={false} onSubmit={async () => true} />)
    expect(html).toContain('data-testid="hand-form"')
    expect((html.match(/<option value="(sub|eq|quantity|part|lowers|raises|affects)"/g) ?? []).length).toBe(7)
    expect(text(html)).toContain('下げる')
  })
  it('問いがあれば 3 行目「答え方」（8 つの方法）と testid が出る', () => {
    const withQ = { ...draft, questions: ['熱伝導率を下げるものは？'] }
    const html = renderToStaticMarkup(<HandForm draft={withQ} nextKey="h3" busy={false} onSubmit={async () => true} onAnswer={async () => true} />)
    for (const id of ['hand-answer-q', 'hand-answer-way', 'hand-answer-target', 'hand-answer', 'hand-name', 'hand-kind', 'hand-add', 'hand-rel-from', 'hand-rel-kind', 'hand-rel-to', 'hand-rel-add']) {
      expect(html).toContain(`data-testid="${id}"`)
    }
    expect((html.match(/<option value="(factors:(lowers|raises|affects)|parts|kinds|count|range|top)"/g) ?? []).length).toBe(8)
    const none = renderToStaticMarkup(<HandForm draft={draft} nextKey="h3" busy={false} onSubmit={async () => true} onAnswer={async () => true} />)
    expect(none).not.toContain('hand-answer')
  })
  it('概念が無いうちは名前の行だけ', () => {
    const html = renderToStaticMarkup(<HandForm draft={null} nextKey="h1" busy={false} onSubmit={async () => true} />)
    expect(html).not.toContain('value="lowers"')
  })

  it('text と箇条書きが同じなら 1 回だけ・人の手番は箇条書きだけ', () => {
    const same = text(card(turn({ reply: reply({ text: '足しました', sentences: ['足しました'] }) })))
    expect(same.split('足しました').length - 1).toBe(1)
    const hand = card(turn({ user: null, reply: reply({ text: '足しました', sentences: ['A を足す'] }) }))
    expect(hand).not.toContain('chat-card-text')
    expect(hand).toContain('A を足す')
  })
  it('機械の手番には印・state none には ✓ も戻すも出ない', () => {
    const m = card(turn({ by: 'machine', state: 'none' }))
    expect(text(m)).toContain('自動（名前の一致）')
    expect(m).not.toContain('reply-confirm')
    expect(m).not.toContain('reply-revert')
    expect(text(card(turn({ by: 'ai' })))).not.toContain('自動（名前の一致）')
  })
  it('機械の手番の text と同じ注記は二重に出さない（違う注記は残す）', () => {
    const same = card(turn({ by: 'machine', state: 'none', reply: reply({ text: '同じ文です', notes: [{ kind: 'info', text: '同じ文です', choice: null }] }) }))
    expect(same.split('同じ文です').length - 1).toBe(1)
    const diff = card(turn({ by: 'machine', state: 'none', reply: reply({ text: '本文', notes: [{ kind: 'info', text: '別の注記', choice: null }] }) }))
    expect(diff).toContain('別の注記')
  })
  it('残した語は名前で出る（引けなければ slug）', () => {
    const t = turn({ state: 'confirmed', commit: { at: '2026-10-22T00:00:00Z', kept: [{ slug: 'thermal', reason: '使用中' }, { slug: 'zzz', reason: '使用中' }] } as Turn['commit'] })
    const html = renderToStaticMarkup(<ReplyCard turn={t} canConfirm={false} count={0} busy={false} onConfirm={noop} onRevert={noop} onAsk={noop} names={new Map([['thermal', '熱伝導率']])} />)
    expect(text(html)).toContain('熱伝導率')
    expect(text(html)).toContain('zzz')
    expect(text(html)).not.toContain('thermal')
  })
  it('注記 ai_failed・info が描ける', () => {
    const h = card(turn({ state: 'none', reply: reply({ notes: [{ kind: 'ai_failed', text: '失敗', choice: null }, { kind: 'info', text: '補足', choice: null }] }) }))
    expect(h).toContain('data-note-kind="ai_failed"')
    expect(h).toContain('data-note-kind="info"')
  })
})

describe('ChatPane の楽観表示', () => {
  const pane = (over: Partial<Parameters<typeof ChatPane>[0]> = {}) =>
    renderToStaticMarkup(
      <ChatPane session={null} loading={false} loadError={null} busy sending error={null} noKey={false} onSend={async () => true} onConfirm={noop} onRevert={noop} onOpenSettings={noop} {...over} />,
    )
  it('送った文が末尾に出て、その下に「考えています」', () => {
    const html = pane({ pendingUser: '熱伝導率を下げるには？' })
    const t = text(html)
    expect(html).toContain('data-testid="pending-user"')
    expect(t.indexOf('熱伝導率を下げるには？')).toBeGreaterThanOrEqual(0)
    expect(t.indexOf('考えています')).toBeGreaterThan(t.indexOf('熱伝導率を下げるには？'))
  })
  it('文が無ければ人の文は出さない', () => {
    expect(pane({ sending: false, busy: false })).not.toContain('pending-user')
  })
})

describe('ReplyCard の ✓ で外した件数', () => {
  const commit = { theme_id: 't', minted: [], lines: 1, questions: [], batch: 'b', at: '2026-10-22T00:00:00Z' }
  it('外しました・残した', () => {
    const t = text(
      card(turn({ state: 'confirmed', commit: { ...commit, removed: { terms: 3, lines: 2 }, kept: [{ slug: 'seebeck', reason: '使われている' }] } }), false),
    )
    expect(t).toContain('外しました: 用語 3・関係 2')
    expect(t).toContain('残した: seebeck（使われている）')
  })
  it('外していなければ出さない', () => {
    expect(text(card(turn({ state: 'confirmed', commit }), false))).not.toContain('外しました')
  })
})


const point = (over: Partial<Point> = {}): Point => ({
  iri: 'https://ex.org/term/lattice',
  kind: 'term',
  label: '格子熱伝導率',
  term_kind: 'property',
  sentences: [
    { text: '格子熱伝導率は熱伝導率の一部', relation: 'isPartOf', source: 'https://ex.org/term/lattice', target: 'https://ex.org/term/kappa', drafted_by: 'ai', at: '2026-10-22T01:00:00Z', batch: 'cabc12345x:t1', reason: '', session_id: 'cabc12345x', turn_id: 't1' },
  ],
  provenance: { drafted_by: 'ai', at: '2026-10-22T01:00:00Z', theme_id: 'th', session_id: 'cabc12345x', turn_id: 't1' },
  questions: [{ title: '熱伝導率を下げる要素は？', mode: 'knowledge', answering: 2, knowledge: 1 }],
  members: [{ iri: 'https://ex.org/kind/sample', label: '試料', dataset_label: '熱電データ' }],
  can_revert: true,
  confirmed: true,
  standard: null,
  dataset_label: '',
  revert: { session_id: 'cabc12345x', turn_id: 't1' },
  ...over,
})
const pcard = (p: Point) =>
  renderToStaticMarkup(<PointCard point={p} busy={false} titleOf={() => '熱電材料'} onContents={noop} onRevert={noop} onRemove={noop} onHighlight={noop} />)

describe('PointCard', () => {
  it('用語: 名前・種別・文・来歴（話の題・日付・AI が足し）・チャットを開く・中身・戻す・問い・入っている種類', () => {
    const html = pcard(point())
    const tx = text(html)
    expect(html).toContain('data-testid="point-card"')
    expect(tx).toContain('格子熱伝導率')
    expect(tx).toContain('項目')
    expect(tx).toContain('格子熱伝導率は熱伝導率の一部')
    expect(tx).toContain('チャット「熱電材料」で 2026-10-22 に AI が足し、人が承認')
    expect(html).toContain('href="#/t/cabc12345x"')
    expect(html).toContain('data-testid="point-contents"')
    expect(html).toContain('data-testid="point-revert"')
    expect(tx).toContain('知識 1')
    expect(tx).toContain('データ 2')
    expect(tx).toContain('試料')
  })
  it('種類: 中身が出る・標準用語は出ない・禁句が画面に無い', () => {
    const html = pcard(point({ kind: 'kind', members: [], can_revert: false, revert: null, sentences: [] }))
    expect(html).toContain('data-testid="point-contents"')
    expect(html).not.toContain('point-revert')
    for (const w of ['線', 'ことば', '下書き', 'pending']) expect(text(html)).not.toContain(w)
  })
  it('標準用語・つながり・未確認の提案（取り消す手番が無い）: 中身も戻すも出ない。確認済みの線は外す提案の戻すだけ', () => {
    for (const kind of ['std', 'hub'] as const) {
      const html = pcard(point({ kind, members: [], can_revert: false, confirmed: true, revert: null, provenance: null, sentences: [] }))
      expect(html).not.toContain('point-contents')
      expect(html).not.toContain('point-revert')
      expect(html).toContain(`data-kind="${kind}"`)
    }
    const draft = pcard(point({ kind: 'draft', members: [], can_revert: false, confirmed: false, revert: null, provenance: null, sentences: [] }))
    expect(draft).not.toContain('point-contents')
    expect(draft).not.toContain('point-revert')
    const line = pcard(point({ kind: 'line', members: [], can_revert: true, confirmed: true, revert: null, provenance: null, sentences: [] }))
    expect(line).not.toContain('point-contents')
    expect(line).toContain('point-revert')
  })
  it('用語でも入っている種類が無ければ中身は出ない・確認済みの用語の戻すは外す提案として出る', () => {
    const html = pcard(point({ members: [], revert: null }))
    expect(html).not.toContain('point-contents')
    expect(html).toContain('point-revert')
    expect(text(html)).not.toContain('この提案を取り消します')
  })
  it('機械の来歴は「名前の一致」', () => {
    const html = pcard(point({ provenance: { drafted_by: 'machine', at: '2026-10-22T01:00:00Z', theme_id: null, session_id: null, turn_id: null } }))
    expect(text(html)).toContain('に 名前の一致')
    expect(html).not.toContain('point-open-session')
  })
})

describe('ReplyCard の表と答え', () => {
  it('表: 見出し・行・ほか N 件', () => {
    const html = card(
      turn({
        state: 'none',
        reply: reply({
          table: {
            kind: { iri: 'k', label: '試料' },
            columns: [{ iri: 'p1', label: '組成' }, { iri: 'p2', label: '相' }],
            rows: [{ iri: 's1', label: '試料1', cells: { p1: { text: 'Bi2Te3' }, p2: { text: '0123456789012345678901234567890', iri: 'https://ex.org/x' } } }],
            total: 25,
            truncated: true,
          },
        }),
      }),
      false,
    )
    expect(html).toContain('data-testid="reply-table"')
    expect(text(html)).toContain('組成')
    expect(text(html)).toContain('Bi2Te3')
    expect(text(html)).toContain('ほか 24 件')
    expect(text(html)).toContain('012345678901234567890123…')
    expect(html).toContain('title="0123456789012345678901234567890"')
  })
  it('答え: 本文・出典（名前とデータセット名）・警告・決定論の印。✓ も戻すも無い', () => {
    const html = card(
      turn({
        state: 'none',
        reply: reply({
          text: '3 件です',
          answer: {
            text: '3 件です',
            citations: [{ iri: 'k', label: '価格表', dataset_id: 'd1', dataset_label: 'スーパー' }],
            notes: [],
            warnings: ['一部の値は推定です'],
            verified_tools: ['count'],
            unverified_sparql: false,
            answered: true,
          },
        }),
      }),
      false,
    )
    expect(html).toContain('data-testid="reply-answer"')
    expect(text(html)).toContain('価格表')
    expect(text(html)).toContain('スーパー')
    expect(text(html)).toContain('一部の値は推定です')
    expect(html).toContain('data-testid="reply-verified"')
    expect(html).not.toContain('reply-confirm')
    expect(html).not.toContain('reply-revert')
  })
})

describe('点の 1 枚の穴（11d）', () => {
  it('線の 1 枚: 見出しが文そのもの・文は重ねない・「文はまだありません」は出ない', () => {
    const html = pcard(point({ kind: 'line', label: '格子熱伝導率 → 熱伝導率', members: [], can_revert: false, revert: null, provenance: null }))
    expect(html).toContain('<h3><span>格子熱伝導率は熱伝導率の一部</span>')
    expect(html).not.toContain('point-sentences')
    expect(text(html)).not.toContain('つながり')
    expect(text(html)).not.toContain('文はまだありません')
  })
  it('提案の札でも文を出す・文が無くても「まだありません」は出さない', () => {
    const withS = pcard(point({ kind: 'draft', members: [], can_revert: false, revert: null, provenance: null }))
    expect(withS).toContain('point-sentences')
    const none = pcard(point({ kind: 'draft', sentences: [], members: [], can_revert: false, revert: null, provenance: null }))
    expect(text(none)).not.toContain('文はまだありません')
    expect(text(pcard(point({ kind: 'term', sentences: [] })))).toContain('文はまだありません')
  })
  it('標準用語は ≡ prefix:label・種類の見出しは「結ばれた用語」', () => {
    const html = pcard(point({ standard: { iri: 'http://qudt.org/Kelvin', label: 'Kelvin', prefix: 'qudt' } }))
    expect(text(html)).toContain('≡ qudt:Kelvin')
    const k = pcard(point({ kind: 'kind', can_revert: false, revert: null }))
    expect(text(k)).toContain('結ばれた用語')
    expect(text(k)).not.toContain('入っている種類')
  })
  it('公開済みの種類: 名前の下にデータセット名が出る', () => {
    const html = pcard(point({ kind: 'kind', label: '商品', dataset_label: '価格表', members: [] }))
    expect(html).toContain('data-testid="point-dataset"')
    expect(text(html)).toContain('価格表')
    expect(pcard(point({ kind: 'kind', label: '商品', members: [] }))).not.toContain('point-dataset')
  })
  it('薄い星: 名前・データセット名・まだ公開していません。ほかは出ない', () => {
    const html = pcard(point({ kind: 'kind', label: '商品', thin: { dataset: '価格表' }, members: [], sentences: [], can_revert: false, revert: null }))
    expect(html).toContain('data-thin="true"')
    expect(text(html)).toContain('商品')
    expect(text(html)).toContain('価格表')
    expect(text(html)).toContain('まだ公開していません')
    expect(html).not.toContain('point-contents')
  })
  it('戻す: 確認済み（confirmed）は外す提案の確認文・未確認は取り消し・標準の語には出ない', () => {
    const open = (p: Point) => renderToStaticMarkup(<PointCard point={p} busy={false} onContents={noop} onRevert={noop} onRemove={noop} onHighlight={noop} />)
    expect(open(point({ confirmed: true }))).toContain('point-revert')
    expect(open(point({ confirmed: false, kind: 'draft', revert: { session_id: 'cabc12345x', turn_id: 't2' } }))).toContain('point-revert')
    expect(open(point({ confirmed: false, kind: 'draft', revert: null }))).not.toContain('point-revert')
    expect(open(point({ kind: 'std', confirmed: true, revert: null }))).not.toContain('point-revert')
  })
  it('確認済みの返事に「戻す」・戻した結果の 1 行・失敗は赤い 1 行', () => {
    const confirmed = turn({ state: 'confirmed', commit: { theme_id: 't', minted: ['a', 'b'], lines: 3, questions: [], batch: 'b', at: '2026-10-22T00:00:00Z' } })
    expect(card(confirmed, false)).toContain('data-testid="reply-undo"')
    const done = renderToStaticMarkup(
      <ReplyCard turn={confirmed} canConfirm={false} count={0} busy={false} onConfirm={noop} onRevert={noop} onAsk={noop} undone={{ lines: 3, terms: 2, questions: 0, kept: [{ slug: 'x', reason: 'r' }] }} />,
    )
    expect(text(done)).toContain('戻しました: 文 3・用語 2（残した: x）')
    const failed = renderToStaticMarkup(
      <ReplyCard turn={confirmed} canConfirm={false} count={0} busy={false} onConfirm={noop} onRevert={noop} onAsk={noop} undone={{ lines: 0, terms: 0, questions: 0, kept: [], failed: [{ what: '文', reason: '失敗' }] }} />,
    )
    expect(failed).toContain('reply-undo-failed')
    expect(failed).toContain('data-testid="reply-undo"')
    expect(card(turn({ state: 'proposed' }), true)).not.toContain('reply-undo')
  })
  it('Ask の印: 検証していない問い合わせ・答えが出ませんでした', () => {
    const base = { text: '3 件', citations: [], notes: [], warnings: [], verified_tools: [] as string[] }
    const mk = (extra: object) => card(turn({ state: 'none', reply: reply({ answer: { ...base, unverified_sparql: false, answered: true, ...extra } }) }), false)
    expect(text(mk({ unverified_sparql: true }))).toContain('検証していない問い合わせで答えました')
    expect(text(mk({ answered: false }))).toContain('答えが出ませんでした')
    expect(text(mk({}))).not.toContain('答えが出ませんでした')
  })
})
