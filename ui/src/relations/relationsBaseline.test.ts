// 関係の直書きを目録（builtinCatalog）から導く形に寄せる前の結果（golden）と、今の結果が同じこと。
// golden は寄せる前のコードで作った（見た目と振る舞いを 1 つも変えないための固定）。
import { describe, expect, it } from 'vitest'
import golden from './relationsBaseline.golden.json'
import { probe } from './baselineProbe'

describe('関係の直書きを目録に寄せた前後で結果が同じ', () => {
  const now = probe()
  for (const key of Object.keys(golden)) {
    it(key, () => {
      expect(now[key]).toEqual((golden as Record<string, unknown>)[key])
    })
  }
  it('golden に無い測りが増えていない', () => {
    expect(Object.keys(now).sort()).toEqual(Object.keys(golden).sort())
  })
})
