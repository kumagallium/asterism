// 見本のページの「新しくなった・新しくしていない」の知らせ（ADR kantan K62・画面の知らせ）。
//
// サーバは理由・単位をコードのまま返す（`held[].reason`／`unit`）。画面に出す文にするのは
// ここ 1 か所 — コード→文言は純関数で、知らないコードは総称の文に落とす（K4: 生の識別子を
// 出さない）。失敗も `detail.error` のコードから固定の文にし、`message` は決して出さない。
import { ApiError } from '../api'
import type { SampleHeldItem, SampleNotice } from './cardsApi'

export type Translate = (key: string, options?: Record<string, unknown>) => string

/** 文言を持っている単位・理由・失敗のコード。これに無いものは総称の文になる。 */
const KNOWN_UNITS = ['design', 'description', 'tools', 'name', 'data'] as const
const KNOWN_REASONS = [
  'edited',
  'decisions',
  'appended',
  'reingested',
  'ids_move',
  'ids_unknown',
  'unsettled',
  'data',
] as const
const KNOWN_ERRORS = [
  'stale',
  'busy',
  'not_overridable',
  'ingest_in_progress',
  'ingest_reserved',
  'retracted',
  'failed',
] as const

function known<T extends string>(list: readonly T[], code: string): code is T {
  return (list as readonly string[]).includes(code)
}

/** 単位のコード → 「設計」「AI に渡す道具」… 知らない単位は「別の項目」。 */
export function unitLabel(unit: string, t: Translate): string {
  return t(known(KNOWN_UNITS, unit) ? `cards:sample.unit_${unit}` : 'cards:sample.unit_other')
}

/** 理由のコード → 「あなたが変えたため」… 知らない理由は「別の事情で入れ替えていません」。 */
export function reasonText(reason: string, t: Translate): string {
  return t(known(KNOWN_REASONS, reason) ? `cards:sample.reason_${reason}` : 'cards:sample.reason_other')
}

/** 失敗のコード → 固定の文（`ApiError.code`）。コードが無い・知らないときは総称の文。 */
export function errorText(error: unknown, t: Translate): string {
  const code = error instanceof ApiError ? (error.code ?? '') : ''
  return t(known(KNOWN_ERRORS, code) ? `cards:sample.error_${code}` : 'cards:sample.error_other')
}

/** 保留 1 件の行「設計 — あなたが変えたため」。ツールは表示名ごとに 1 行（表示名の無い
 *  ものは件数だけ）。 */
function linesOfHeld(item: SampleHeldItem, t: Translate): string[] {
  const reason = reasonText(item.reason, t)
  if (item.unit === 'tools') {
    const titles = item.titles ?? []
    const rest = Math.max((item.count ?? titles.length) - titles.length, 0)
    const names = titles.map((title) => t('cards:sample.tool_titled', { title }))
    if (rest > 0 || names.length === 0) {
      names.push(t('cards:sample.tool_count', { count: rest > 0 ? rest : (item.count ?? 1) }))
    }
    return names.map((name) => t('cards:sample.held_line', { unit: name, reason }))
  }
  return [t('cards:sample.held_line', { unit: unitLabel(item.unit, t), reason })]
}

/** 「入れ替えていません」の項目の行。データの群（設計・データ・AI に渡す道具が同じ理由で
 *  そろって保留になる）は、データの 1 行にまとめる。 */
export function heldLines(held: SampleHeldItem[], t: Translate): string[] {
  const dataReasons = new Set(held.filter((h) => h.unit === 'data').map((h) => h.reason))
  return held
    .filter((h) => !((h.unit === 'design' || h.unit === 'tools') && dataReasons.has(h.reason)))
    .flatMap((h) => linesOfHeld(h, t))
}

/** 「置き換える」ボタンを出すか（置き換えられる項目があるときだけ）。 */
export function canOverride(notice: SampleNotice): boolean {
  return notice.overridable.length > 0 && notice.seq !== null && notice.revision !== null
}

/** 置き換わる単位の名前（確認の文に並べる）。 */
export function overridableLabels(notice: SampleNotice, t: Translate): string[] {
  return notice.overridable.map((u) => unitLabel(u, t))
}

/** 確認の文に「AI に渡す道具は次に起動したときから」を添えるか。 */
export function touchesTools(units: string[]): boolean {
  return units.includes('tools')
}

/** 静かな 1 行「この見本は {日付} に新しい版になりました — {note}」。note は UI の言語の方。
 *  データを入れ替えた更新は「データも新しい版になりました」を 1 文足す。 */
export function updatedLine(
  updated: NonNullable<SampleNotice['updated']>,
  language: string,
  t: Translate,
): string {
  const note = (language.startsWith('en') ? updated.note.en : updated.note.ja) ?? updated.note.ja ?? ''
  const date = formatDate(updated.at, language)
  const base = note
    ? t('cards:sample.updated', { date, note })
    : t('cards:sample.updated_plain', { date })
  return updated.units.includes('data') ? `${base}${t('cards:sample.updated_data')}` : base
}

/** 「{日付} に置き換える前の内容を控えています」。 */
export function backupLine(at: string, language: string, t: Translate): string {
  return t('cards:sample.backup_line', { date: formatDate(at, language) })
}

function formatDate(iso: string | null, language: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return new Intl.DateTimeFormat(language, { dateStyle: 'medium' }).format(d)
}
