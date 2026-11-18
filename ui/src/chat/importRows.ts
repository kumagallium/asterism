// 「取り込み」のデータセット一覧の並べ替えと 1 行の中身（純関数・PR 14d-1）。
import type { SkyDataset } from '../sky/skyApi'

export interface ImportRow {
  id: string
  name: string
  published: boolean
  paper: boolean
  /** 論文は null（種類と件数の行を出さない）。 */
  kinds: number | null
  rows: number | null
  /** 壊れた日付は null。 */
  created: Date | null
}

function parseDate(s: string): Date | null {
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? null : d
}

const time = (r: ImportRow) => r.created?.getTime() ?? Number.NEGATIVE_INFINITY

function toRow(d: SkyDataset, published: boolean): ImportRow {
  const paper = !!d.document
  return {
    id: d.id,
    name: d.label,
    published,
    paper,
    kinds: paper ? null : d.kinds.length,
    rows: paper ? null : d.kinds.reduce((n, k) => n + k.count, 0),
    created: parseDate(d.created_at),
  }
}

/** 公開済み → 未公開の順。各群の中は created_at の新しい順（同じなら元の順）。 */
export function importRows(datasets: SkyDataset[], proposed: SkyDataset[]): ImportRow[] {
  const sorted = (xs: SkyDataset[], published: boolean) =>
    xs
      .map((d, i) => ({ r: toRow(d, published), i }))
      .sort((a, b) => time(b.r) - time(a.r) || a.i - b.i)
      .map((x) => x.r)
  return [...sorted(datasets, true), ...sorted(proposed, false)]
}
