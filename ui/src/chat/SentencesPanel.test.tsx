import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { SentencesPanel } from './SentencesPanel'
import { relSentence, rowsOfDraft, rowsOfPoint, shrinkScope, subTabOf, type SentenceRow } from './sentenceRows'
import type { Point } from './chatApi'
import type { DraftSky } from '../sky/skyApi'

const noop = () => undefined
const approved: SentenceRow = { text: '三斜晶系 は 空間群 の一種', approved: true, source: 's', relation: 'sub', target: 'o' }
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
  it('空: 選んでいなければ案内・選んでいて 0 件なら「文はまだありません」', () => {
    expect(panel([], { noSelection: true })).toContain('知識グラフの点を押すと、その点についての文がここに出ます')
    expect(panel([])).toContain('文はまだありません')
  })
  it('選んでいないときの提案の行には見出し', () => {
    expect(panel([thin], { noSelection: true })).toContain('この話の提案')
  })
})

describe('sentenceRows', () => {
  it('relSentence は REL_SENTENCE と同じ言い方', () => {
    expect(relSentence('sub', 'A', 'B', 'ja')).toBe('A は B の一種')
    expect(relSentence('lowers', 'A', 'B', 'en')).toBe('A lowers B')
  })
  it('rowsOfDraft: 外す・離れる線は除き、名前を引く', () => {
    const d = {
      draft_id: 'd1',
      theme_label: '',
      concepts: [],
      questions: [],
      relations: [
        { from: 'a', to: 'b', rel: 'part' },
        { from: 'a', to: 'b', rel: 'sub', state: 'remove' },
      ],
    } as unknown as DraftSky
    const rows = rowsOfDraft(d, new Map([['a', '粒界']]), 't2', 'ja')
    expect(rows).toHaveLength(1)
    expect(rows[0].text).toBe('粒界 は b の一部')
    expect(rows[0].draft?.turnId).toBe('t2')
    expect(rowsOfDraft(null, new Map(), null, 'ja')).toEqual([])
  })
  it('rowsOfPoint: 下書きの鍵がある文は未承認', () => {
    const base = { text: 'x', relation: 'r', source: 's', target: 'o', drafted_by: 'ai', at: '', batch: '', reason: '', session_id: null, turn_id: 't1' }
    const p = { sentences: [base, { ...base, from: 'a', rel: 'sub', to: 'b', draft_id: 'd1' }] } as unknown as Point
    const rows = rowsOfPoint(p)
    expect(rows.map((r) => r.approved)).toEqual([true, false])
  })
  it('タブの既定: 用語・提案・なし→文／種類・データセット→データ／覚えた選びが先', () => {
    expect(subTabOf(null, 't|x')).toBe('sentences')
    expect(subTabOf(null, 'draft|d|k')).toBe('sentences')
    expect(subTabOf(null, null)).toBe('sentences')
    expect(subTabOf(null, 'k|ds|x')).toBe('data')
    expect(subTabOf(null, 'd|ds')).toBe('data')
    expect(subTabOf('data', 't|x')).toBe('data')
    expect(subTabOf('bad', 'k|a|b')).toBe('data')
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
    expect(shrinkScope({ node: 'n', name: 'n' }, [], 'x')).toEqual({ node: 'n', name: 'n' })
  })
})
