import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import type { SuggestItem } from '../workspace/workspaceState'
import { SuggestButton, SuggestCard } from './QuestionSuggest'

// 「問いを提案してもらう」のカード: 1 行 = 問いの文 + 薄く実行文 + 「この問いで始める」。押さなければ何も残らない。

const item = (over: Partial<SuggestItem>): SuggestItem => ({
  kind: 'suggest',
  id: 'i1',
  label: 'スーパーの価格表',
  status: 'done',
  rows: [
    { question: '食材ごとに記録は何件ある？', mode: 'data', op: 'group', shape: '値段の記録を 食材の名前 ごとに数える' },
    { question: '1 つあたりの値段がいちばん高い記録は？', mode: 'data', op: 'top', shape: '値段の記録のうち、1 つあたりの値段がいちばん大きいものを出します' },
  ],
  detail: '',
  ...over,
})
const html = (it: SuggestItem) => renderToStaticMarkup(<SuggestCard item={it} onStart={() => {}} onClose={() => {}} />)

describe('問いの提案カード', () => {
  it('見出し「こんな問いはどうですか」・元のデータセット名・各行に問い・実行文・「この問いで始める」', () => {
    const out = html(item({}))
    expect(out).toContain('こんな問いはどうですか')
    expect(out).toContain('スーパーの価格表')
    expect(out.match(/data-testid="suggest-row"/g)).toHaveLength(2)
    expect(out.match(/この問いで始める/g)).toHaveLength(2)
    expect(out).toContain('食材ごとに記録は何件ある？')
    expect(out).toContain('値段の記録を 食材の名前 ごとに数える')
  })
  it('待っている間は行を出さず、失敗は理由を言う。0 本のときは 1 行', () => {
    expect(html(item({ status: 'running', rows: [] }))).not.toContain('suggest-row')
    expect(html(item({ status: 'failed', rows: [], detail: 'AI が設定されていません' }))).toContain('AI が設定されていません')
    expect(html(item({ rows: [] }))).toContain('出せる問いが見つかりませんでした')
  })
  it('文言に「そう」「線」は無い', () => {
    const out = html(item({}))
    expect(out.replace(/<[^>]*>/g, '')).not.toMatch(/そう|線/)
  })
  it('ボタンは「問いを提案してもらう」', () => {
    expect(renderToStaticMarkup(<SuggestButton onAsk={() => {}} />)).toContain('問いを提案してもらう')
  })
})
