import { describe, expect, it } from 'vitest'
import { nodePropLines } from './graphProps'

/** GraphView が節の下に出す行 — K4（生の識別子を見せない）: 出すのは
 *  `type_label`（見出し「種類」）と `snapshot`（見出し「版」）だけで、
 *  生の `type`（クラス IRI）や `dataset_id` は出さない。 */

const t = (key: string): string => (key === 'cards:graph.prop_type' ? '種類' : '版')

describe('nodePropLines', () => {
  it('type_label と snapshot が「見出し: 値」で出る', () => {
    const lines = nodePropLines(
      {
        type: 'https://ex/weatherlog#Digest',
        type_label: 'Digest',
        dataset_id: 'weatherlog',
        snapshot: 'v1',
      },
      t,
    )
    expect(lines).toEqual(['種類: Digest', '版: v1'])
  })

  it('dataset_id と生の type は出ない・値の無い行も出ない', () => {
    const lines = nodePropLines({ type: 'https://ex/weatherlog#Digest', dataset_id: 'weatherlog' }, t)
    expect(lines).toEqual([])
    expect(lines.join(' ')).not.toContain('https://ex/weatherlog#Digest')
    expect(lines.join(' ')).not.toContain('weatherlog')
  })
})
