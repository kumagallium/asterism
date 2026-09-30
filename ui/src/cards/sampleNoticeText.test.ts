import { describe, expect, it } from 'vitest'
import ja from '../i18n/locales/ja/cards.json'
import { ApiError } from '../api'
import type { SampleHeldItem, SampleNotice } from './cardsApi'
import {
  backupLine,
  canOverride,
  errorText,
  heldLines,
  overridableLabels,
  reasonText,
  touchesTools,
  unitLabel,
  updatedLine,
} from './sampleNoticeText'

// 本物の ja 文言を引く t()（key を素通しする t では、key の英字を「生の識別子」と
// 見分けられない）。{{name}} は options で埋める。
function lookup(key: string): string | undefined {
  let node: unknown = ja
  for (const part of key.replace(/^cards:/, '').split('.')) {
    if (node && typeof node === 'object' && part in node) node = (node as Record<string, unknown>)[part]
    else return undefined
  }
  return typeof node === 'string' ? node : undefined
}
const t = (key: string, options?: Record<string, unknown>): string => {
  const text = lookup(key)
  if (text === undefined) throw new Error(`missing ja key: ${key}`)
  return text.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(options?.[name] ?? ''))
}

// 英字の識別子（コードのままの reason・unit・ツール名・ファイル名）が画面に出ていないか。
const RAW = /[a-z_]{3,}/

const REASONS = [
  'edited',
  'decisions',
  'appended',
  'reingested',
  'ids_move',
  'ids_unknown',
  'unsettled',
  'data',
]
const UNITS = ['design', 'description', 'tools', 'name', 'data']

function notice(over: Partial<SampleNotice> = {}): SampleNotice {
  return { updated: null, held: [], overridable: [], restorable: null, seq: 3, revision: 'r', ...over }
}

describe('reasonText / unitLabel', () => {
  it('知っている理由・単位は、どれも英字の識別子を含まない日本語の文になる', () => {
    for (const r of REASONS) expect(reasonText(r, t)).not.toMatch(RAW)
    for (const u of UNITS) expect(unitLabel(u, t)).not.toMatch(RAW)
  })

  it('知らないコードも総称の文に落ちる（コードのままは出ない）', () => {
    expect(reasonText('some_new_reason', t)).toBe(t('cards:sample.reason_other'))
    expect(unitLabel('some_new_unit', t)).toBe(t('cards:sample.unit_other'))
    expect(reasonText('some_new_reason', t)).not.toMatch(RAW)
  })

  it('個別に文言を決めた理由は、その文になる（総称に落ちない）', () => {
    expect(reasonText('decisions', t)).toBe('あなたが決めた内容（列の意味や見た目など）があるため')
    expect(reasonText('edited', t)).toBe('あなたが変えたため')
    expect(reasonText('appended', t)).toBe('データを追記したため')
    expect(reasonText('reingested', t)).toBe('データを取り込み直したため')
    expect(reasonText('decisions', t)).not.toBe(t('cards:sample.reason_other'))
  })

  it('引用の住所が動く版の 2 つの理由は、同じ文になる', () => {
    expect(reasonText('ids_move', t)).toBe(reasonText('ids_unknown', t))
    expect(reasonText('ids_move', t)).toContain('引用の住所')
    expect(reasonText('unsettled', t)).toBe(reasonText('data', t))
  })
})

describe('heldLines', () => {
  it('単位×理由の全部の組み合わせで、英字の識別子が入らない', () => {
    const all: SampleHeldItem[] = UNITS.flatMap((unit) => REASONS.map((reason) => ({ unit, reason })))
    for (const line of heldLines(all, t)) expect(line).not.toMatch(RAW)
  })

  it('ツールは表示名ごとに 1 行、表示名の無いものは件数だけ（ツール名は出さない）', () => {
    const lines = heldLines(
      [{ unit: 'tools', reason: 'edited', titles: ['国の平均寿命の推移'], count: 3 }],
      t,
    )
    expect(lines).toEqual([
      'AI に渡す道具『国の平均寿命の推移』 — あなたが変えたため',
      'AI に渡す道具 2 件 — あなたが変えたため',
    ])
    expect(heldLines([{ unit: 'tools', reason: 'edited', count: 2 }], t)).toEqual([
      'AI に渡す道具 2 件 — あなたが変えたため',
    ])
    expect(heldLines([{ unit: 'tools', reason: 'edited' }], t)).toEqual([
      'AI に渡す道具 1 件 — あなたが変えたため',
    ])
  })

  it('データの群（設計・データ・道具が同じ理由でそろって保留）は、データの 1 行にまとまる', () => {
    const lines = heldLines(
      [
        { unit: 'design', reason: 'ids_move' },
        { unit: 'data', reason: 'ids_move' },
        { unit: 'tools', reason: 'ids_move' },
        { unit: 'name', reason: 'edited' },
      ],
      t,
    )
    expect(lines).toEqual([
      'データ — 引用の住所が変わる版のため、自動では入れません',
      '名前 — あなたが変えたため',
    ])
  })

  it('保留が無ければ行も無い', () => {
    expect(heldLines([], t)).toEqual([])
  })
})

describe('errorText', () => {
  const err = (code: string) =>
    new ApiError('sample refresh', 409, JSON.stringify({ detail: { error: code } }))

  it('コードから固定の文になり、message（生の文字列）は出ない', () => {
    for (const code of [
      'stale',
      'busy',
      'not_overridable',
      'ingest_in_progress',
      'ingest_reserved',
      'retracted',
      'failed',
    ]) {
      const text = errorText(err(code), t)
      expect(text).not.toMatch(RAW)
      expect(text).not.toContain('HTTP')
    }
  })

  it('個別に文言を決めたコードは、その文になる（総称に落ちない）', () => {
    expect(errorText(err('ingest_reserved'), t)).toBe(
      'データの取り込みの途中のため、いまは置き換えられません。',
    )
    expect(errorText(err('ingest_in_progress'), t)).toBe(errorText(err('ingest_reserved'), t))
    expect(errorText(err('stale'), t)).toContain('読み直して')
    expect(errorText(err('retracted'), t)).toBe('この見本は取り下げ中のため、置き換えられません。')
    expect(errorText(err('not_overridable'), t)).not.toBe(t('cards:sample.error_other'))
  })

  it('知らないコード・コードの無い失敗は総称の文', () => {
    expect(errorText(err('brand_new'), t)).toBe(t('cards:sample.error_other'))
    expect(errorText(new Error('boom'), t)).toBe(t('cards:sample.error_other'))
    expect(errorText(new ApiError('x', 500, 'plain text'), t)).toBe(t('cards:sample.error_other'))
  })
})

describe('updatedLine / backupLine', () => {
  const updated = {
    at: '2026-09-30T01:00:00+00:00',
    note: { ja: '図の箱を日本語にした', en: 'Named the boxes' },
    units: ['design'],
  }

  it('note は UI の言語の方（en なら en）', () => {
    expect(updatedLine(updated, 'ja', t)).toContain('図の箱を日本語にした')
    expect(updatedLine(updated, 'ja', t)).not.toContain('Named')
    // ja 文言を引く t でも、選ばれる note は言語で決まる
    expect(updatedLine(updated, 'en', t)).toContain('Named the boxes')
  })

  it('データを入れ替えた更新は「データも新しい版になりました」を足す', () => {
    expect(updatedLine({ ...updated, units: ['design', 'data'] }, 'ja', t)).toContain(
      'データも新しい版になりました',
    )
    expect(updatedLine(updated, 'ja', t)).not.toContain('データも')
  })

  it('note が無くても日付だけの文になり、識別子は入らない', () => {
    const line = updatedLine({ ...updated, note: { ja: null, en: null } }, 'ja', t)
    expect(line).toContain('新しい版になりました')
    expect(line).not.toContain('—')
    expect(backupLine('2026-09-30T02:00:00+00:00', 'ja', t)).not.toMatch(RAW)
  })
})

describe('canOverride / overridableLabels / touchesTools', () => {
  it('置き換えられる項目があるときだけボタンを出す', () => {
    expect(canOverride(notice())).toBe(false)
    expect(canOverride(notice({ overridable: ['design'] }))).toBe(true)
    // 画面が見た seq・revision が無ければ出さない（サーバの stale 判定が通らない）
    expect(canOverride(notice({ overridable: ['design'], seq: null }))).toBe(false)
  })

  it('確認の文に並べる名前は日本語で、道具を含むときだけ「次に起動したとき」の注記を足す', () => {
    const n = notice({ overridable: ['design', 'tools'] })
    expect(overridableLabels(n, t)).toEqual(['設計', 'AI に渡す道具'])
    expect(touchesTools(n.overridable)).toBe(true)
    expect(touchesTools(['design'])).toBe(false)
  })
})
