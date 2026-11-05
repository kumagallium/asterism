import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { SentencesPanel } from './SentencesPanel'
import { canRemoveRow, relSentence, rowKey, rowsOfDraft, rowsOfPoint, shrinkScope, subTabOf, type SentenceRow } from './sentenceRows'
import type { Point } from './chatApi'
import type { DraftSky } from '../sky/skyApi'

const noop = () => undefined
const approved: SentenceRow = { text: '三斜晶系 は 空間群 の一種', approved: true, source: 'https://ex.org/s', relation: 'sub', target: 'https://ex.org/o' }
const thin: SentenceRow = { text: 'A は B を下げる', approved: false, draft: { draftId: 'd1', from: 'a', rel: 'lowers', to: 'b', turnId: 't2' } }
const panel = (rows: SentenceRow[], over: Partial<Parameters<typeof SentencesPanel>[0]> = {}) =>
  renderToStaticMarkup(<SentencesPanel rows={rows} noSelection={false} hasTheme busy={false} onPress={noop} onRemove={noop} onFix={noop} onApprove={noop} {...over} />)

describe('SentencesPanel', () => {
  it('濃淡: 承認済み＝濃い・未承認＝薄い', () => {
    const h = panel([approved, thin])
    expect(h.match(/data-testid="sentence-row"/g)).toHaveLength(2)
    expect(h).toContain('data-approved="true"')
    expect(h).toContain('chat-sentences-row is-thin')
  })
  it('承認は未承認の行だけ（テーマありのとき）', () => {
    const h = panel([approved, thin])
    expect(h.match(/data-testid="sentence-approve"/g)).toHaveLength(1)
    expect(h.match(/data-testid="sentence-remove"/g)).toHaveLength(2)
    expect(h.match(/data-testid="sentence-fix"/g)).toHaveLength(2)
  })
  it('テーマなしでは承認が出ない', () => {
    expect(panel([thin], { hasTheme: false })).not.toContain('sentence-approve')
  })
  it('空: 選んでいなければ案内・選んでいて 0 件なら「関係はまだありません」', () => {
    expect(panel([], { noSelection: true })).toContain('知識グラフの点を選ぶと、その点の関係がここに出ます')
    expect(panel([])).toContain('関係はまだありません')
  })
  it('選んでいないときの提案の行には見出し', () => {
    expect(panel([thin], { noSelection: true })).toContain('このチャットの提案')
  })
})

describe('根拠の文（12c）', () => {
  const ev = (n: number, over: Record<string, unknown> = {}) => ({ iri: `https://ex.org/sent/${n}`, text: `根拠${n}`, path: null, dataset_label: '論文X', ...over })
  const withEv = (evidence: ReturnType<typeof ev>[]): SentenceRow => ({ ...approved, evidence })
  it('行の下に根拠の文を小さく 1 行と「論文名 · 節」（title に全文）', () => {
    const h = panel([withEv([ev(1, { text: 'あ'.repeat(150) + '…', full: 'あ'.repeat(200), section: 'Results' })])])
    expect(h).toContain('data-testid="reply-evidence"')
    expect(h).toContain('論文X · Results')
    expect(h).toContain(`title="根拠の文: ${'あ'.repeat(200)}"`)
    expect(h).not.toContain('evidence-more')
  })
  it('節の題名が無ければ path から「節 n」・どちらも無ければ論文名だけ', () => {
    expect(panel([withEv([ev(1, { path: '2.1' })])])).toContain('論文X · 節 2.1')
    const bare = panel([withEv([ev(1)])])
    expect(bare).toContain('>論文X<')
    expect(bare).not.toContain(' · ')
  })
  it('2 件以上は 1 件目だけ出して「ほか n 件」', () => {
    const h = panel([withEv([ev(1), ev(2), ev(3)])])
    expect(h).toContain('根拠1')
    expect(h).not.toContain('根拠2')
    expect(h).toContain('ほか 2 件')
  })
  it('根拠が 2 本以上の論文にまたがるときだけ「論文 k 本」（1 本のときは出さない）', () => {
    const two = panel([withEv([ev(1, { dataset_id: 'a' }), ev(2, { dataset_id: 'b', dataset_label: '論文Y' })])])
    expect(two).toContain('data-testid="evidence-papers"')
    expect(two).toContain('論文 2 本')
    const one = panel([withEv([ev(1, { dataset_id: 'a' }), ev(2, { dataset_id: 'a' })])])
    expect(one).not.toContain('evidence-papers')
    expect(panel([withEv([ev(1)])])).not.toContain('evidence-papers')
  })
  it('根拠の無い行は今のまま', () => {
    expect(panel([approved, thin])).not.toContain('reply-evidence')
  })
  it('rowsOfPoint は evidence を通す（無い文には付けない）', () => {
    const base = { text: 'x', relation: 'r', source: 's', target: 'o', drafted_by: 'ai', at: '', batch: '', reason: '', session_id: null, turn_id: 't1' }
    const p = { sentences: [{ ...base, evidence: [ev(1)] }, base] } as unknown as Point
    const rows = rowsOfPoint(p)
    expect(rows[0].evidence).toHaveLength(1)
    expect('evidence' in rows[1]).toBe(false)
  })
  it('論文の点は d| でも「文」のタブが既定（paper| の頭で用語側に寄せる）', () => {
    expect(subTabOf(null, 'paper|d|p1')).toBe('sentences')
  })
})

describe('sentenceRows', () => {
  it('relSentence は REL_SENTENCE と同じ言い方', () => {
    expect(relSentence('sub', 'A', 'B', 'ja')).toBe('A は B の一種')
    expect(relSentence('eq', 'A', 'B', 'ja')).toBe('A と B は同じもの')
    expect(relSentence('zzz', 'A', 'B', 'ja')).toBe('A - B')
    expect(relSentence('lowers', 'A', 'B', 'en')).toBe('A lowers B')
  })
  it('rowsOfDraft: 実データの形（state なし・from_seed あり）。種は濃い行・外す／離れる線は除く・名前は curie の右も', () => {
    const d = {
      draft_id: 'd1',
      theme_label: '',
      concepts: [],
      questions: [],
      relations: [
        { from: 'a', to: 'b', rel: 'part' },
        { from: 'a', to: 'qudt:Kelvin', rel: 'sub', from_seed: true },
        { from: 'a', to: 'b', rel: 'sub', state: 'remove' },
        { from: 'a', to: 'b', rel: 'eq', state: 'moved' },
      ],
    } as unknown as DraftSky
    const rows = rowsOfDraft(d, new Map([['a', '粒界']]), 't2', 'ja')
    expect(rows).toHaveLength(2)
    expect(rows[0].text).toBe('粒界 は b の一部')
    expect(rows[0].approved).toBe(false)
    expect(rows[0].draft?.turnId).toBe('t2')
    expect(rows[1].approved).toBe(true)
    expect(rows[1].text).toBe('粒界 は Kelvin の一種')
    expect(rowsOfDraft(d, new Map([['a', '粒界'], ['qudt:Kelvin', 'ケルビン']]), null, 'ja')[1].text).toBe('粒界 は ケルビン の一種')
    expect(rowsOfDraft(null, new Map(), null, 'ja')).toEqual([])
  })
  it('種の行は承認済み: 承認は出ない・IRI が無ければ外すも出ない', () => {
    const seed: SentenceRow = { text: '種', approved: true, draft: { draftId: 'd1', from: 'a', rel: 'sub', to: 'b', turnId: 't2' } }
    const h = panel([seed])
    expect(h).not.toContain('sentence-approve')
    expect(h).not.toContain('sentence-remove')
    expect(h).toContain('sentence-fix')
    expect(panel([{ ...seed, source: 'https://ex.org/s', relation: 'sub', target: 'https://ex.org/o' }])).toContain('sentence-remove')
  })
  it('承認済みの行の外すは IRI のときだけ（draft: の参照では出さない）', () => {
    const d = { draftId: 'd1', from: 'c2', rel: 'sub', to: 'c1', turnId: null }
    expect(canRemoveRow({ text: 'x', approved: true, source: 'draft:theme:c2', relation: 'sub', target: 'draft:theme:c1', draft: d })).toBe(false)
    expect(canRemoveRow({ text: 'x', approved: true, source: 'https://ex.org/a', relation: 'sub', target: 'https://ex.org/b', draft: d })).toBe(true)
    expect(canRemoveRow(thin)).toBe(true)
  })
  it('他の話の提案の行は読むだけ（外す・直す・承認が出ない）', () => {
    const h = panel([thin], { editable: () => false })
    expect(h).toContain('sentence-row')
    expect(h).not.toContain('sentence-fix')
    expect(h).not.toContain('sentence-approve')
  })
  it('rowKey: 下書きの鍵か IRI の組', () => {
    expect(rowKey(thin)).toBe('d|a|lowers|b')
    expect(rowKey(approved)).toBe('a|https://ex.org/s|sub|https://ex.org/o')
  })
  it('rowsOfPoint: 下書きの鍵がある文は未承認', () => {
    const base = { text: 'x', relation: 'r', source: 's', target: 'o', drafted_by: 'ai', at: '', batch: '', reason: '', session_id: null, turn_id: 't1' }
    const p = { sentences: [base, { ...base, from: 'a', rel: 'sub', to: 'b', draft_id: 'd1' }] } as unknown as Point
    const rows = rowsOfPoint(p)
    expect(rows.map((r) => r.approved)).toEqual([true, false])
  })
  it('タブの既定: 覚えた選びは同じ種類の点のあいだだけ', () => {
    expect(subTabOf(null, 't|x')).toBe('sentences')
    expect(subTabOf(null, 'draft|d|k')).toBe('sentences')
    expect(subTabOf(null, null)).toBe('sentences')
    expect(subTabOf(null, 'k|ds|x')).toBe('data')
    expect(subTabOf(null, 'd|ds')).toBe('data')
    expect(subTabOf({ tab: 'data', group: 'term' }, 't|x')).toBe('data')
    expect(subTabOf({ tab: 'data', group: 'term' }, 'draft|d|k')).toBe('data')
    expect(subTabOf({ tab: 'data', group: 'term' }, 'k|a|b')).toBe('data') // 既定
    expect(subTabOf({ tab: 'sentences', group: 'term' }, 'k|a|b')).toBe('data') // 種類に変わったので既定へ
    expect(subTabOf({ tab: 'data', group: 'term' }, null)).toBe('sentences')
  })
})

describe('読み直しと範囲', () => {
  const base = { text: 'x', relation: 'r', source: 's', target: 'o', drafted_by: 'ai', at: '', batch: '', reason: '', session_id: null, turn_id: 't1' }
  const pt = (n: number) => ({ sentences: Array.from({ length: n }, (_, i) => ({ ...base, text: `文${i}`, from: `a${i}`, rel: 'sub', to: 'b', draft_id: 'd1' })) }) as unknown as Point
  it('point が変わると行も変わる', () => {
    expect(rowsOfPoint(pt(4))).toHaveLength(4)
    expect(rowsOfPoint(pt(3))).toHaveLength(3)
  })
  it('shrinkScope: 行が消えたら点だけに戻る・残っていればそのまま', () => {
    const sc = { node: 'draft|d1|c3', name: 'A は B の一種', line: { from: 'a0', rel: 'sub', to: 'b' } }
    expect(shrinkScope(sc, rowsOfPoint(pt(4)), '値段')).toBe(sc)
    const after = shrinkScope(sc, rowsOfPoint(pt(0)), '値段')
    expect(after).toEqual({ node: 'draft|d1|c3', name: '値段' })
    expect(shrinkScope(sc, rowsOfPoint(pt(0)), '')).toBeNull()
    expect(shrinkScope({ node: 'n', name: 'n' }, [], 'x')).toEqual({ node: 'n', name: 'n' })
  })
})
