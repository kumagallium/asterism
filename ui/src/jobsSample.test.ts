import { describe, expect, it } from 'vitest'
import ja from './i18n/locales/ja/jobs.json'
import { normalizeJob, type IngestJob } from './jobsApi'
import { isSampleJob, sampleJobLines, sampleStatusKey } from './jobsSample'

function lookup(key: string): string | undefined {
  let node: unknown = ja
  for (const part of key.replace(/^jobs:/, '').split('.')) {
    if (node && typeof node === 'object' && part in node) node = (node as Record<string, unknown>)[part]
    else return undefined
  }
  return typeof node === 'string' ? node : undefined
}
const t = (key: string): string => {
  const text = lookup(key)
  if (text === undefined) throw new Error(`missing ja key: ${key}`)
  return text
}
const RAW = /[a-z_]{3,}/

function job(over: Record<string, unknown> = {}): IngestJob {
  return normalizeJob({
    kind: 'sample_refresh',
    status: 'ok',
    dataset_id: 'demo',
    ended_at: '2026-09-30T01:00:00+00:00',
    sample: { action: 'startup', units: ['design'], held: [] },
    ...over,
  })
}

describe('normalizeJob', () => {
  it('sample を運ぶ（action・units・held のコード）', () => {
    const j = job({ sample: { action: 'override', units: ['design', 5], held: [{ unit: 'name', reason: 'edited' }, 'x', {}] } })
    expect(j.sample).toEqual({
      action: 'override',
      units: ['design'],
      held: [{ unit: 'name', reason: 'edited' }],
    })
  })

  it('sample が無い・形が違う記録は null（既存の取り込み記録は変わらない）', () => {
    expect(normalizeJob({ kind: 'ingest', status: 'ok' }).sample).toBeNull()
    expect(normalizeJob({ kind: 'sample_refresh', sample: 'x' }).sample).toBeNull()
  })
})

describe('sampleJobLines', () => {
  it('見本の記録だけが専用の行になる', () => {
    expect(isSampleJob(job())).toBe(true)
    expect(isSampleJob(normalizeJob({ kind: 'ingest' }))).toBe(false)
  })

  it('起動時: 新しくした・一部は変えたので新しくしていない', () => {
    expect(sampleJobLines(job(), t)).toEqual(['見本を新しい版にしました'])
    const held = job({
      status: 'partial',
      sample: { action: 'startup', units: ['design'], held: [{ unit: 'tools', reason: 'edited' }] },
    })
    expect(sampleJobLines(held, t)).toEqual([
      '見本を新しい版にしました',
      '一部は、あなたが変えたので新しくしていません',
    ])
    const heldOnly = job({
      status: 'partial',
      sample: { action: 'startup', units: [], held: [{ unit: 'design', reason: 'ids_move' }] },
    })
    // あなたが変えたのではない理由（引用の住所が動く版）は、その言い方をしない
    expect(sampleJobLines(heldOnly, t)).toEqual(['一部は、いまは新しくしていません'])
  })

  it('手動の置き換え・控えから戻す・失敗', () => {
    expect(sampleJobLines(job({ sample: { action: 'override', units: ['design'], held: [] } }), t)).toEqual([
      '変えたものを新しい見本に置き換えました',
    ])
    expect(sampleJobLines(job({ sample: { action: 'restore', units: ['design'], held: [] } }), t)).toEqual([
      '控えから戻しました',
    ])
    expect(sampleJobLines(job({ status: 'error', sample: { action: 'restore', units: [], held: [] } }), t)).toEqual([
      '控えから戻せませんでした',
    ])
  })

  it('知らない action・sample の無い記録は総称の文に落ち、英字の識別子が出ない', () => {
    for (const j of [job({ sample: { action: 'brand_new', units: [], held: [] } }), job({ sample: null })]) {
      const lines = sampleJobLines(j, t)
      expect(lines).toEqual(['見本の記録'])
      for (const line of lines) expect(line).not.toMatch(RAW)
    }
  })

  it('ファイル名も「〇件の事実」も出さない', () => {
    for (const j of [job(), job({ sample: { action: 'restore', units: [], held: [] } })]) {
      for (const line of sampleJobLines(j, t)) {
        expect(line).not.toContain('件の事実')
        expect(line).not.toMatch(RAW)
      }
    }
  })

  it('partial の状態は「失敗」でなく「一部は入れ替えていません」', () => {
    expect(sampleStatusKey('partial')).toBe('jobs:sample.status_partial')
    expect(sampleStatusKey('ok')).toBeNull()
    expect(t(sampleStatusKey('partial') as string)).toBe('一部は入れ替えていません')
  })
})
