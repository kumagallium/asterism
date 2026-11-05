import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import type { Evidence, PaperText } from './chatApi'
import { paperOf } from './chatApi'
import { EvidenceLine } from './EvidenceLine'
import { PaperReaderView } from './PaperReader'
import { PaperReaderContext, type PaperReaderApi } from './paperReaderContext'
import { ReaderSlot } from './ReaderSlot'

const noop = () => undefined
const paper: PaperText = {
  dataset_id: 'p1',
  label: '論文X',
  truncated: false,
  evidence: ['s2'],
  sections: [
    { iri: 'sec1', title: 'Introduction', path: 'intro', role: 'introduction', depth: 0, paragraphs: [{ iri: 'p1', sentences: [{ iri: 's1', text: 'First.' }, { iri: 's2', text: 'Second.' }] }] },
    { iri: 'sec2', title: null, path: null, role: null, depth: 1, paragraphs: [{ iri: 'p2', sentences: [{ iri: 's3', text: 'Third.' }] }] },
  ],
}
const view = (focus: string | null, p: PaperText | null = paper) => renderToStaticMarkup(<PaperReaderView paper={p} focus={focus} onBack={noop} onSentence={noop} />)

describe('PaperReaderView（13b）', () => {
  it('節の題名と、題名が無いときの「節 n」・文は段落でつなげる', () => {
    const h = view(null)
    expect(h).toContain('Introduction')
    expect(h).toContain('節 2')
    expect(h).toContain('論文X')
    expect(h).toContain('← 会話に戻る')
    expect(h.match(/<p class="chat-reader-para"/g)).toHaveLength(2)
  })
  it('根拠の文は下線の印と title・focus の文だけ強調', () => {
    const h = view('s2')
    expect(h.match(/data-evidence="true"/g)).toHaveLength(1)
    expect(h.match(/data-focus="true"/g)).toHaveLength(1)
    expect(h).toContain('is-evidence is-focus')
    expect(h).toContain('根拠になっている関係を知識グラフで見る')
    expect(view('s1')).toMatch(/is-focus[^>]*>First\./)
  })
  it('読み込み中・失敗・長い論文の注記', () => {
    expect(renderToStaticMarkup(<PaperReaderView paper={null} loading focus={null} onBack={noop} onSentence={noop} />)).toContain('論文を読み込んでいます')
    expect(renderToStaticMarkup(<PaperReaderView paper={null} error="boom" focus={null} onBack={noop} onSentence={noop} />)).toContain('論文を開けませんでした: boom')
    expect(view(null, { ...paper, truncated: true })).toContain('先頭の 3 文だけ')
  })
  it('paperOf は形の合うものだけ通す', () => {
    expect(paperOf({ dataset_id: 'p1', label: 'L', sections: [], evidence: ['a', 1], truncated: true })).toMatchObject({ evidence: ['a'], truncated: true })
    expect(paperOf({ dataset_id: 'p1', sections: [{ nope: 1 }] })).toBeNull()
    expect(paperOf(null)).toBeNull()
  })
})

describe('EvidenceLine が押せるのは dataset_id があって公開済みのときだけ（13b）', () => {
  const ev = (over: Partial<Evidence> = {}): Evidence => ({ iri: 'https://ex.org/s/1', text: '根拠', path: null, dataset_label: '論文X', ...over })
  const api = (unavailable: string[] = []): PaperReaderApi => ({ open: noop, unavailable: new Set(unavailable) })
  const line = (e: Evidence, ctx: PaperReaderApi | null) =>
    renderToStaticMarkup(
      <PaperReaderContext.Provider value={ctx}>
        <EvidenceLine evidence={[e]} />
      </PaperReaderContext.Provider>,
    )
  it('dataset_id あり・文脈あり → ボタン', () => {
    expect(line(ev({ dataset_id: 'p1' }), api())).toContain('data-testid="evidence-open"')
  })
  it('dataset_id なし・文脈なし・未公開（404 だった）→ 押せない', () => {
    expect(line(ev(), api())).not.toContain('evidence-open')
    expect(line(ev({ dataset_id: 'p1' }), null)).not.toContain('evidence-open')
    expect(line(ev({ dataset_id: 'p1' }), api(['p1']))).not.toContain('evidence-open')
  })
})

describe('ReaderSlot（論文を開いても入力欄の下書きを残す）', () => {
  it('論文を開いていても会話の列は mount したまま隠す（閉じない）', () => {
    const open = renderToStaticMarkup(<ReaderSlot reader={<div>READER</div>} pane={<textarea defaultValue="下書き" />} />)
    expect(open).toContain('READER')
    expect(open).toContain('下書き')
    expect(open).toMatch(/data-testid="pane-slot" hidden/)
    const closed = renderToStaticMarkup(<ReaderSlot reader={null} pane={<textarea />} />)
    expect(closed).not.toMatch(/pane-slot"[^>]*hidden/)
  })
})
