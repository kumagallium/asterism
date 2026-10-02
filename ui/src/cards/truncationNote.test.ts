import { describe, expect, it } from 'vitest'
import { truncationNote } from './truncationNote'

describe('truncationNote', () => {
  it('全部を出しているときは注記なし', () => {
    expect(truncationNote({ count: 3001, truncated: false, total: 3001 })).toBeNull()
  })

  it('間引いた図は「全 N 点を M 点に間引いた」', () => {
    expect(truncationNote({ count: 5000, truncated: true, total: 12000, thinned: true })).toEqual({
      key: 'truncation.thinned',
      params: { shown: 5000, total: 12000 },
    })
  })

  it('読み切れていないときは「N 点以上」', () => {
    expect(
      truncationNote({ count: 5000, truncated: true, total: 100000, thinned: true, total_is_lower_bound: true }),
    ).toEqual({ key: 'truncation.thinned_lower_bound', params: { shown: 5000, total: 100000 } })
  })

  it('一覧の上限で切れたときは「全 N 件のうち M 件」', () => {
    expect(truncationNote({ count: 20, truncated: true, total: 47 })).toEqual({
      key: 'truncation.partial',
      params: { shown: 20, total: 47 },
    })
  })

  it('元の件数が分からなければ「ほかにもあります」', () => {
    expect(truncationNote({ count: 20, truncated: true })).toEqual({
      key: 'truncation.more',
      params: { shown: 20 },
    })
  })
})
