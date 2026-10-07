// 設計が外せなかった「取り込まない」（ID を作る列・その種類のただ 1 つの項目）を、
// 作る保存が advisories で言い残す。その 1 行を「ためす」と公開一覧が読む形。
//
// 文の形は api（`_settle_on_minted_design`）が決め、理由は mapping-spec の
// 修正器（`asterism_step0.staged_propose`）の文言そのまま。固定句で見分ける。
import { describe, expect, it } from 'vitest'
import { isMeaningReviewAdvisory, keptExclusionsOf } from './advisoryPlain'

const KEPT = 'stays in the design although it was marked do-not-take-in'
const identifier = `column no of source stock.csv ${KEPT}: excluded column 'no' is an identifier for map 'record' and cannot be removed safely`
const only = `column amplitude of source readings.csv ${KEPT}: excluded column 'amplitude' is the only property of map 'reading' and cannot be removed safely`
const unmapped =
  'source stock.csv has 1 column(s) the mapping never uses: unit. If a column carries meaning users will ask about — map it.'

describe('設計が外せなかった「取り込まない」の知らせ', () => {
  it('列・ソース・理由を読み取る', () => {
    expect(
      keptExclusionsOf([identifier, only]).map(({ column, source, why }) => ({ column, source, why })),
    ).toEqual([
      { column: 'no', source: 'stock.csv', why: 'identifier' },
      { column: 'amplitude', source: 'readings.csv', why: 'only' },
    ])
  })

  it('知らない理由は「外せない」とだけ言い、元の文を添える', () => {
    const odd = `column x of source a.csv ${KEPT}: something new`
    expect(keptExclusionsOf([odd])).toEqual([{ column: 'x', source: 'a.csv', why: 'other', raw: odd }])
  })

  it('ほかの知らせは読まない', () => {
    expect(keptExclusionsOf([unmapped, 'map X mints one entity per row …'])).toEqual([])
  })

  it('AI に直させる指摘ではない（人の列の判断・または何もしない）', () => {
    // 「使われていない列」と同じ扱い: 設計の作り直しでは止めず、見直しの
    // 「きっかけ」の札にも出さない。
    expect(isMeaningReviewAdvisory(identifier)).toBe(true)
    expect(isMeaningReviewAdvisory(unmapped)).toBe(true)
    expect(isMeaningReviewAdvisory('the mapping has 2 DISCONNECTED groups: A | B.')).toBe(false)
  })
})
