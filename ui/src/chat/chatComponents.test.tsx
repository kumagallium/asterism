import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { ThemesApiError } from '../themes/themesApi'
import type { Reply, SessionSummary, Turn } from './chatApi'
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
  it('注記 ai_failed・info が描ける', () => {
    const h = card(turn({ state: 'none', reply: reply({ notes: [{ kind: 'ai_failed', text: '失敗', choice: null }, { kind: 'info', text: '補足', choice: null }] }) }))
    expect(h).toContain('data-note-kind="ai_failed"')
    expect(h).toContain('data-note-kind="info"')
  })
})
