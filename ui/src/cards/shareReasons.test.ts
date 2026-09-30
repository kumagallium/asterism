import { describe, expect, it } from 'vitest'
import { formatShareReasons, shareReasonKey, shareReasonLabels } from './shareReasons'

describe('shareReasonKey', () => {
  it('固定語彙 5 種を cards:detail.reason_* へ写す', () => {
    expect(shareReasonKey('own_data')).toBe('detail.reason_own_data')
    expect(shareReasonKey('unknown_origin')).toBe('detail.reason_unknown_origin')
    expect(shareReasonKey('unknown_license')).toBe('detail.reason_unknown_license')
    expect(shareReasonKey('not_redistributable')).toBe('detail.reason_not_redistributable')
    expect(shareReasonKey('no_materials')).toBe('detail.reason_no_materials')
  })

  it('未知の reason はそのまま返す（保守側・UI を落とさない）', () => {
    expect(shareReasonKey('mystery_reason')).toBe('mystery_reason')
  })
})

describe('shareReasonLabels', () => {
  const t = (key: string) => `[${key}]`

  it('順序を保ったまま文言化する', () => {
    expect(shareReasonLabels(['own_data', 'unknown_license'], t)).toEqual([
      '[detail.reason_own_data]',
      '[detail.reason_unknown_license]',
    ])
  })

  it('空配列は空配列', () => {
    expect(shareReasonLabels([], t)).toEqual([])
  })
})

describe('formatShareReasons', () => {
  const t = (key: string) => `[${key}]`

  it('「・」区切りの 1 文にする', () => {
    expect(formatShareReasons(['own_data', 'unknown_license'], t)).toBe(
      '[detail.reason_own_data]・[detail.reason_unknown_license]',
    )
  })

  it('材料が無いときの単独理由も文言化する', () => {
    expect(formatShareReasons(['no_materials'], t)).toBe('[detail.reason_no_materials]')
  })

  it('空配列は空文字列', () => {
    expect(formatShareReasons([], t)).toBe('')
  })
})
