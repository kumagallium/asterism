// ADR `kind-splitting-and-consult-suggestions.md` §5「D3 の適用: 提案ブロックの
// JSON から骨格が変わることをテスト（LLM 0）」。
//
// 通す道は「モデルの返事の文字列 → パース → 決定論の反映 → 骨格」。途中に LLM は
// 1 回も入らない。

import { describe, expect, it } from 'vitest'
import type { MappingSkeleton } from '../api'
import { applyIdentifiers, applyOwners, applySplits, twinKindNames } from '../skeletonKinds'
import { parseSuggestionsBlock, SUGGESTIONS_FENCE } from './consultApply'

const SOURCE = 'xrd-card.csv'

function reply(block: string): string {
  return `カードと結晶は別のものです。\n\n\`\`\`${SUGGESTIONS_FENCE}\n${block}\n\`\`\`\n`
}

const CARD: MappingSkeleton = {
  version: 1,
  prefixes: { xrr: 'https://example.org/x#' },
  maps: [
    { name: 'card', source: SOURCE, subject: { template: 'xrr:card/{No}', classes: ['xrr:Card'] } },
    {
      name: 'peak',
      source: SOURCE,
      subject: { template: 'xrr:peak/{No}-{hkl}', classes: ['xrr:Peak'] },
    },
  ],
}

describe('parseSuggestionsBlock — D3 の 3 型', () => {
  it('splits / owners / identifiers を読み取り、ブロックは本文から消える', () => {
    const parsed = parseSuggestionsBlock(
      reply(
        JSON.stringify({
          splits: [{ from: 'card', name: '結晶', columns: ['Cell', 'Volume', 'Z value'] }],
          owners: [{ column: 'Radiation', map: 'peak' }],
          identifiers: [
            { column: 'Chemical Formula', reason: '外のデータと同じ名前で呼ばれる' },
          ],
        }),
      ),
    )
    expect(parsed.displayText).toBe('カードと結晶は別のものです。')
    expect(parsed.splits).toEqual([
      { from: 'card', name: '結晶', columns: ['Cell', 'Volume', 'Z value'] },
    ])
    expect(parsed.owners).toEqual([{ column: 'Radiation', map: 'peak' }])
    expect(parsed.identifiers).toEqual([
      { column: 'Chemical Formula', reason: '外のデータと同じ名前で呼ばれる' },
    ])
  })

  it('理由の無い identifiers は捨てる（K22: 押させるのではなく、選ばせる）', () => {
    const parsed = parseSuggestionsBlock(
      reply(JSON.stringify({ identifiers: [{ column: 'CSD' }, { column: 'X', reason: '  ' }] })),
    )
    expect(parsed.identifiers).toEqual([])
  })

  it('欠けた項目のある splits / owners は捨てる', () => {
    const parsed = parseSuggestionsBlock(
      reply(
        JSON.stringify({
          splits: [{ from: 'card', columns: ['Cell'] }, { from: 'card', name: 'X', columns: [] }],
          owners: [{ column: 'Radiation' }],
        }),
      ),
    )
    expect(parsed.splits).toEqual([])
    expect(parsed.owners).toEqual([])
  })

  it('ブロックが無い / 壊れていても、本文はそのまま返る（エラーにしない）', () => {
    expect(parseSuggestionsBlock('ただの返事').splits).toEqual([])
    expect(parseSuggestionsBlock('ただの返事').displayText).toBe('ただの返事')
    const broken = parseSuggestionsBlock(reply('{ not json'))
    expect(broken.displayText).toBe('カードと結晶は別のものです。')
    expect(broken.splits).toEqual([])
  })

  it('既存の suggestions / kinds は今までどおり読める', () => {
    const parsed = parseSuggestionsBlock(
      reply(
        JSON.stringify({
          suggestions: [{ column: 'CSD', meaning: '収載コード', unit: '' }],
          kinds: [{ map: 'peak', name: 'ピーク' }],
        }),
      ),
    )
    expect(parsed.suggestions).toEqual([{ column: 'CSD', meaning: '収載コード', unit: undefined }])
    expect(parsed.kinds).toEqual([{ map: 'peak', name: 'ピーク' }])
  })
})

describe('提案ブロック → 骨格（LLM 0 の一周）', () => {
  it('splits の JSON から、同じキーで項目の分かれた種類ができる', () => {
    const { splits } = parseSuggestionsBlock(
      reply(
        JSON.stringify({
          splits: [{ from: 'card', name: '結晶', columns: ['Cell', 'Volume', 'Z value'] }],
        }),
      ),
    )
    const out = applySplits(CARD, splits)
    expect(out.applied).toBe(1)
    const added = out.skeleton.maps.find((m) => m.name === 'kind')!
    // カードと同じ `No` で数える別の種類 — ピークはこの種類も入れ子の親に持つ。
    expect(added.subject.template).toBe('xrr:kind/{No}')
    expect(added.owns).toEqual(['No', 'Cell', 'Volume', 'Z value'])
    // 完了条件 1: 同じキーで項目が分かれた種類を作っても警告は出ない。
    expect(twinKindNames(out.skeleton).size).toBe(0)
  })

  it('owners は分散クラスが同じときだけ動く', () => {
    const { owners } = parseSuggestionsBlock(
      reply(JSON.stringify({ owners: [{ column: 'Radiation', map: 'peak' }] })),
    )
    const classOf = (n: string) => ({ card: 'singleton', peak: 'unique' })[n]
    expect(applyOwners(CARD, owners, classOf).applied).toBe(0)
  })

  it('identifiers の JSON から、その列で数える種類ができる', () => {
    const { identifiers } = parseSuggestionsBlock(
      reply(
        JSON.stringify({
          identifiers: [{ column: 'Chemical Formula', reason: '外のデータと同じ名前' }],
        }),
      ),
    )
    const out = applyIdentifiers(CARD, identifiers, 'card')
    expect(out.applied).toBe(1)
    expect(out.skeleton.maps.find((m) => m.name === 'chemical_formula')?.subject.template).toBe(
      'xrr:chemical_formula/{Chemical Formula}',
    )
  })
})
