import { describe, expect, it } from 'vitest'
import { subjectDisplayLabel } from './subjectLabel'

describe('subjectDisplayLabel', () => {
  it('label があればそれを返す', () => {
    expect(subjectDisplayLabel('サンプル A', 'https://example.org/subjects/1')).toBe('サンプル A')
  })

  it('label が null なら iri の末尾の名前を返す', () => {
    expect(subjectDisplayLabel(null, 'https://example.org/subjects/sample-1')).toBe('sample-1')
  })

  it('label が undefined でも末尾の名前を返す', () => {
    expect(subjectDisplayLabel(undefined, 'https://example.org/subjects/sample-2')).toBe('sample-2')
  })

  it('末尾のスラッシュや # を落としてから末尾の名前を取る', () => {
    expect(subjectDisplayLabel(null, 'https://example.org/subjects/sample-3/')).toBe('sample-3')
    expect(subjectDisplayLabel(null, 'https://example.org/subjects#sample-4#')).toBe('sample-4')
  })
})
