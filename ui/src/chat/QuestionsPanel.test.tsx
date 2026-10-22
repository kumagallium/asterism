import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'
import '../i18n'
import i18n from '../i18n'
import { EMPTY_SKY, type SkyData, type SkyQuestion } from '../sky/skyApi'
import { QuestionsPanel } from './QuestionsPanel'
import { normalizeQuestion } from './questionsState'

const q = (over: Partial<SkyQuestion>): SkyQuestion => ({
  tool_name: 'q1',
  title: '熱伝導率が低い試料は？',
  mode: 'data',
  theme_id: null,
  theme_label: '',
  answering: 0,
  knowledge: 0,
  total_datasets: 3,
  needs: [],
  shape: '',
  ...over,
} as SkyQuestion)

const term = (iri: string, datasetIds: string[]) => ({
  iri, slug: iri, label: iri, label_en: iri, kind: 'class' as const,
  members: datasetIds.map((dataset_id) => ({ iri: `k:${iri}`, dataset_id, relation: 'is' })),
  standard: null, answering: 0, questions: [],
})
const sky = (n = 4): SkyData =>
  ({
    ...EMPTY_SKY,
    datasets: Array.from({ length: n }, (_, i) => ({ id: `d${i}`, label: `データ${i}`, kinds: [] })),
    terms: [term('t:a', ['d0', 'd1', 'd2', 'd3']), term('t:b', ['d0', 'd1', 'd2', 'd3'])],
  }) as unknown as SkyData

const props = { sky: sky(), proposed: [], onPick: () => {}, onAsk: async () => true, busy: false }
const html = (el: React.ReactElement) => renderToStaticMarkup(el)

afterEach(async () => {
  await i18n.changeLanguage('ja')
})

describe('QuestionsPanel', () => {
  it('✓ とデータセット名（3 つまで＋ほか n）', () => {
    const h = html(<QuestionsPanel {...props} questions={[q({ answering: 4, needs: ['t:a', 't:b'] })]} />)
    expect(h).toContain('data-testid="question-row"')
    expect(h).toContain('✓')
    expect(h).toContain('データ0・データ1・データ2・ほか 1 で答えられる')
  })
  it('– と「データがまだ無い」', () => {
    const h = html(<QuestionsPanel {...props} questions={[q({ answering: 0, needs: ['t:a'] })]} />)
    expect(h).toContain('–')
    expect(h).toContain('用語はある・データがまだ無い')
  })
  it('知識の問い: 関係で答えられる（n 本）', () => {
    const h = html(<QuestionsPanel {...props} questions={[q({ mode: 'knowledge', knowledge: 2 })]} />)
    expect(h).toContain('関係で答えられる（2 本）')
  })
  it('薄い提案: まだ承認していません', () => {
    const h = html(<QuestionsPanel {...props} questions={[]} proposed={['新しい問い']} />)
    expect(h).toContain('新しい問い')
    expect(h).toContain('まだ承認していません')
    expect(h).not.toContain('問いはまだありません')
  })
  it('空: 案内', () => {
    const h = html(<QuestionsPanel {...props} questions={[]} />)
    expect(h).toContain('問いはまだありません')
  })
  it('「答えて」のボタンが行ごとに出る（薄い提案には出ない）', () => {
    const h = html(<QuestionsPanel {...props} questions={[q({ answering: 1, needs: ['t:a'] })]} proposed={['薄い']} />)
    expect(h.match(/data-testid="question-ask"/g)).toHaveLength(1)
    expect(h).toContain('答えて')
    expect(h).not.toContain('disabled')
  })
  it('busy の間は押せない', () => {
    const h = html(<QuestionsPanel {...props} busy questions={[q({ answering: 1, needs: ['t:a'] })]} />)
    expect(h).toContain('disabled')
  })
  it('知識の問いでも knowledge=0 で answering>=1 ならデータの文', () => {
    const h = html(<QuestionsPanel {...props} questions={[q({ mode: 'knowledge', knowledge: 0, answering: 1, needs: ['t:a'] })]} />)
    expect(h).toContain('で答えられる')
    expect(h).not.toContain('関係で答えられる')
  })
  it('別々のデータセットにしか結ばれていない: 合わせて答えられる', () => {
    const s = { ...sky(), terms: [term('t:a', ['d0']), term('t:b', ['d1'])] } as unknown as SkyData
    const h = html(<QuestionsPanel {...props} sky={s} questions={[q({ answering: 1, needs: ['t:a', 't:b'] })]} />)
    expect(h).toContain('データ0・データ1 を合わせて答えられる')
  })
  it('名前が引けない: データで答えられる', () => {
    const h = html(<QuestionsPanel {...props} questions={[q({ answering: 1, needs: ['t:zzz'] })]} />)
    expect(h).toContain('データで答えられる')
  })
  it('normalizeQuestion: 空白と全角・半角の ？ の差を無視', () => {
    expect(normalizeQuestion(' 低い試料は？ ')).toBe(normalizeQuestion('低い試料は?'))
  })
})
