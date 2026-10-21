import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { DraftCard, ProposalCard } from './ProposalCard'
import { type ProposalItem, newProposalItem } from './workspaceState'

// 返事のあとの提案カード: できなかった理由を畳まず各行に出す・「適用しました」と偽らない。

const ops = [
  { op: 'add_relation', from: 'c1', rel: 'sub', to: 'c2', reason: '' },
  { op: 'add_relation', from: 'c2', rel: 'sub', to: 'c1', reason: '' },
]
const card = (over: Partial<ProposalItem>) => ({
  ...newProposalItem({ proposalId: 'p', ops, sentences: ['A は B の一種', 'B は A の一種'], by: 'ai' }),
  ...over,
})
const html = (item: ProposalItem) =>
  renderToStaticMarkup(
    <ProposalCard item={item} disabled={false} onYes={() => {}} onNo={() => {}} onPer={() => {}} onRowYes={() => {}} onRowNo={() => {}} />,
  )

describe('a proposal after the person answered', () => {
  it('all dropped: says it could not be applied (with the reason), not "skipped", and shows each row’s reason', () => {
    const out = html(card({ decided: ['dropped', 'dropped'], notes: ['cycle', 'cycle'] }))
    expect(out).toContain('適用できませんでした（一周してしまう）')
    expect(out).not.toContain('入れませんでした')
    expect(out).not.toContain('<details')
    expect(out.match(/一周してしまう（循環）/g)).toHaveLength(2) // 各行に薄く
  })

  it('different reasons: the title points to the rows', () => {
    const out = html(card({ decided: ['dropped', 'dropped'], notes: ['cycle', 'unknown_key'] }))
    expect(out).toContain('適用できませんでした（理由は各行に）')
  })

  it('partly dropped: counts both, and the reason stays on the row that failed', () => {
    const out = html(card({ decided: ['yes', 'dropped'], notes: [null, 'dismissed'] }))
    expect(out).toContain('図に入れた 1 件・できなかった 1 件')
    expect(out).toContain('前に「入れない」と返事をした内容')
  })

  it('everything already there is one line', () => {
    const out = html(card({ decided: ['dropped', 'dropped'], notes: ['duplicate', 'duplicate'] }))
    expect(out).toContain('すべて既にあります')
    expect(out).not.toContain('A は B の一種')
  })

  it('all yes stays folded; all "no" is "skipped"', () => {
    expect(html(card({ decided: ['yes', 'yes'] }))).toContain('図に入れた 2 件')
    expect(html(card({ decided: ['no', 'no'] }))).toContain('入れませんでした')
  })
})

describe('the first card', () => {
  const draftCard = (resumed: boolean) =>
    renderToStaticMarkup(
      <DraftCard
        conceptNames={[]}
        relationSentences={[]}
        per={false}
        total={{ concepts: 4, relations: 3, resumed }}
        left={5}
        onYesAll={() => {}}
        onPer={() => {}}
        onRowYes={() => {}}
        onRowNo={() => {}}
      />,
    )
  it('says how many are left when the draft was reopened', () => {
    expect(draftCard(true)).toContain('AI の下書き（残り 5 件）')
    expect(draftCard(false)).toContain('AI の下書き（概念 4・関係 3）')
  })
})
