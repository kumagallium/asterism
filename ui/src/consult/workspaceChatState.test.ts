import { describe, expect, it } from 'vitest'
import { newProposalItem, type ChatItem } from '../workspace/workspaceState'
import { appliedOps, bindUnlinkedMessage, openProposalCount, settleRows } from './workspaceChatState'

const item = () =>
  newProposalItem({
    proposalId: 'p',
    ops: [
      { op: 'add_relation', from: 'kind:aa', rel: 'eq', to: 'specimen', reason: '' },
      { op: 'add_relation', from: 'kind:bb', rel: 'eq', to: 'specimen', reason: '' },
    ],
    sentences: ['a', 'b'],
    by: 'ai',
  })

describe('作業場の会話（home・dataset）の状態', () => {
  it('「そう」で当たった行は yes・落ちた行は dropped（理由つき）。返事をしていない行は残す', () => {
    const next = settleRows(item(), [0, 1], new Map([[1, 'duplicate']]), 'yes')
    expect(next.decided).toEqual(['yes', 'dropped'])
    expect(next.notes).toEqual([null, 'duplicate'])
    expect(next.busy).toBe(false)
    const one = settleRows(item(), [1], new Map(), 'yes')
    expect(one.decided).toEqual([null, 'yes'])
  })

  it('「違う」は no（落ちた扱いにしない）', () => {
    expect(settleRows(item(), [0], new Map(), 'no').decided).toEqual(['no', null])
  })

  it('開くボタンの印は、返事を待っている行の数', () => {
    const items: ChatItem[] = [{ kind: 'user', id: 'u', text: 'x' }, settleRows(item(), [0], new Map(), 'yes')]
    expect(openProposalCount(items)).toBe(1)
  })

  it('当たった操作は、落ちた添字を除いたもの', () => {
    const it0 = item()
    expect(appliedOps(it0.ops, [0])).toEqual([it0.ops[1]])
  })

  it('「AI に結ばせる」の定型の一言は日本語・英語とも 1 行', () => {
    expect(bindUnlinkedMessage('ja')).toBe('公開済みの種類で、用語にまだ結ばれていないものを結んで')
    expect(bindUnlinkedMessage('en-US')).toMatch(/not yet linked/)
  })
})
