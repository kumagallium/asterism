import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { ThemesApiError } from '../themes/themesApi'
import type { Reply, SessionSummary, Turn } from './chatApi'
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
  it('未確認の最後の返事には ✓ 確認＋件数と「戻す」', () => {
    const html = card(turn())
    expect(html).toContain('data-testid="reply-confirm"')
    expect(html).toContain('data-testid="reply-revert"')
    expect(text(html)).toContain('✓ 確認 2 件')
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
    expect(text(card(turn({ state: 'confirmed', commit: { theme_id: 'x', minted: [], lines: 1, questions: [], batch: 'b', at: '2026-10-22T09:30:00Z' } }), false))).toContain('✓ 確認済み')
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

describe('📎 表を添える', () => {
  const binding = (column: string, decided: 'bind' | 'skip' = 'bind') => ({ column, label: column, term: column, term_label: column, relation: 'eq', subject: '', decided })
  it('入力欄に 📎（隠し input・表の拡張子だけ）がある', () => {
    const html = renderToStaticMarkup(<Composer onSend={async () => true} onAttach={noop} busy={false} noKey={false} onOpenSettings={noop} />)
    expect(html).toContain('data-testid="chat-attach"')
    expect(html).toMatch(/type="file"[^>]*multiple|multiple[^>]*type="file"/)
    expect(html).toContain('accept=".csv,.tsv,.xlsx,.xls,.json"')
    expect(text(html)).toContain('📎')
  })
  it('running の手番は「読んでいます…（段の 1 行）」で、✓ も戻すも出さない', () => {
    const html = renderToStaticMarkup(
      <ReplyCard turn={turn({ state: 'running', job_id: 'j1' })} canConfirm count={0} busy={false} onConfirm={noop} onRevert={noop} onAsk={noop} progress="列の意味を調べています" />,
    )
    expect(html).toContain('consult-bubble--pending')
    expect(html).toContain('consult-spinner')
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
    expect(text(html)).toContain('📎 価格表.csv・12 行')
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
    expect(text(card(turn({ by: 'machine', attachment: at })))).not.toContain('機械')
    expect(text(card(turn({ by: 'machine', user: null, reply: reply({ suggestions: [{ subject: 'a', term: 'b', relation: 'eq' }] }) })))).toContain('機械')
  })
  it('選び待ちに ✓ は無く、表の手番の ✓ は「公開」', () => {
    const choice = { label: 'L', options: [{ value: 'a', label: 'A' }], default: 'a' }
    const wait = card(turn({ reply: reply({ notes: [{ kind: 'choice_id', text: 'x', choice }] }) }), false)
    expect(wait).not.toContain('reply-confirm')
    const pub = card(turn({ attachment: { dataset_id: 'd', name: 'a.csv', rows: 1, kinds: [], id_column: 'id' } }))
    expect(text(pub)).toContain('✓ 公開')
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
  it('行・新しい話・提案の印・題が空なら「新しい話」', () => {
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
    expect(text(html)).toContain('設定 › サーバで鍵を入れると、話を保存できます')
  })
  it('たたむと ≡ だけ（新しい話も一覧も出ない）', () => {
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
    expect(text(m)).toContain('機械')
    expect(m).not.toContain('reply-confirm')
    expect(m).not.toContain('reply-revert')
    expect(text(card(turn({ by: 'ai' })))).not.toContain('機械')
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
