// 見せ方（Presentation）を決定論で組む層。契約層（`viewSpec.ts` の型・
// `OutputKind`・role）は変えない — 「同じ結果から別の ViewSpec を組む」だけ
// （ADR `docs/architecture/object-cards-ui.md` O36）。LLM は呼ばない。
// `custom` の印は付けない（人が選んだ見せ方は事実の絵であり、LLM が書いた
// ものではない）。
import {
  columnFor,
  factsView,
  fieldTitle,
  findRole,
  flowView,
  quantityView,
  xEncoding,
  yEncoding,
  breakdownView as sourceBreakdownView,
  rankedView as sourceRankedView,
} from './defaultView'
import type { OutputKind, Row, TableColumn, TableSpec, ToolContract, VegaLiteSpec, ViewSpec } from './viewSpec'

/** 見せ方の選択そのもの。無指定のフィールドは既定を使う。 */
export type PresentationMark = 'line' | 'bar' | 'point' | 'table'

export interface Presentation {
  mark?: PresentationMark
  swapXY?: boolean
  colorBy?: string | null
}

/** 色分けの候補（1 つだけ — role が高々 1 列にしか付かないため）。 */
export interface ColorByCandidate {
  key: string
  label: string
}

/** その `output_kind` で許される見せ方の固定表（契約メモ §1.2）。
 *  `marks` が空 = 切替 UI を出さない（quantity/facts/flow）。 */
export interface PresentationOptions {
  marks: PresentationMark[]
  swapXY: boolean
  colorBy: ColorByCandidate | null
}

const NO_SWITCH: PresentationOptions = { marks: [], swapXY: false, colorBy: null }

/** 固定表そのもの。ここに無い組み合わせは UI にも `viewFor` にも出てこない。 */
export function allowedPresentations(tool: ToolContract, rows: Row[]): PresentationOptions {
  void rows
  switch (tool.output_kind) {
    case 'series': {
      const series = findRole(tool.item, 'series')
      return {
        marks: ['line', 'bar', 'point', 'table'],
        swapXY: false,
        colorBy: series ? { key: series.key, label: fieldTitle(series) } : null,
      }
    }
    case 'pairs': {
      const x = findRole(tool.item, 'x')
      const y = findRole(tool.item, 'y')
      const category = findRole(tool.item, 'category')
      return {
        marks: ['point', 'line', 'table'],
        swapXY: !!x && !!y,
        colorBy: category ? { key: category.key, label: fieldTitle(category) } : null,
      }
    }
    case 'ranked':
      return { marks: ['table', 'bar'], swapXY: false, colorBy: null }
    case 'breakdown':
      return { marks: ['bar', 'table'], swapXY: false, colorBy: null }
    default:
      return NO_SWITCH
  }
}

// 既存の defaultViewFor（presentation 導入前）が実際に返していたマーク＝
// `allowedPresentations` の候補順の先頭と同じ（ranked の既定は表: PR B で
// 主語の列を `subject_field` に持つ表を既定にした決定をそのまま引き継ぐ）。
const DEFAULT_MARK: Partial<Record<OutputKind, PresentationMark>> = {
  series: 'line',
  pairs: 'point',
  ranked: 'table',
  breakdown: 'bar',
}

interface ResolvedPresentation {
  mark: PresentationMark
  swapXY: boolean
  colorBy: string | null
}

function isValidPresentation(options: PresentationOptions, presentation: Presentation): boolean {
  if (presentation.mark !== undefined && !options.marks.includes(presentation.mark)) return false
  if (presentation.swapXY && !options.swapXY) return false
  if (presentation.colorBy != null && presentation.colorBy !== options.colorBy?.key) return false
  return true
}

function defaultColorBy(kind: OutputKind, options: PresentationOptions): string | null {
  // series だけ「役の列があれば既定で色分けする」（既存 defaultViewFor と同じ
  // 挙動）。pairs は役の列があっても既定では色分けしない（既存 pairsView は
  // category を見ていなかった）。
  return kind === 'series' ? (options.colorBy?.key ?? null) : null
}

/** `presentation` を固定表に照らして解決する。無効な組み合わせ（表に無い値）
 *  は丸ごと既定に戻す（部分的な採用はしない）。`options.marks` が空のときは
 *  呼ばない（呼び出し側で切替なしの output_kind を弾く）。 */
function resolvePresentation(
  kind: OutputKind,
  options: PresentationOptions,
  presentation: Presentation | undefined,
): ResolvedPresentation {
  const defaultMark = DEFAULT_MARK[kind] ?? options.marks[0]
  const valid = presentation !== undefined && isValidPresentation(options, presentation)
  return {
    mark: valid && presentation!.mark !== undefined ? presentation!.mark : defaultMark,
    swapXY: valid ? !!presentation!.swapXY : false,
    colorBy: valid && presentation!.colorBy !== undefined ? presentation!.colorBy : defaultColorBy(kind, options),
  }
}

/** UI 用: 実際に効いている見せ方（既定・無効フォールバック込み）。切替 UI を
 *  出さない output_kind では `null`。 */
export function currentPresentation(
  tool: ToolContract,
  rows: Row[],
  presentation: Presentation | undefined,
): ResolvedPresentation | null {
  const options = allowedPresentations(tool, rows)
  if (options.marks.length === 0) return null
  return resolvePresentation(tool.output_kind, options, presentation)
}

function pickFields(rows: Row[], keys: string[]): Row[] {
  return rows.map((row) => {
    const out: Row = {}
    for (const key of keys) out[key] = row[key]
    return out
  })
}

function seriesMark(mark: PresentationMark): VegaLiteSpec['mark'] {
  return mark === 'line' ? { type: 'line', point: true } : mark
}

function seriesViewWith(tool: ToolContract, rows: Row[], resolved: ResolvedPresentation): ViewSpec {
  const x = findRole(tool.item, 'x')
  const y = findRole(tool.item, 'y')
  const series = findRole(tool.item, 'series')
  if (resolved.mark === 'table') {
    const columns: TableColumn[] = []
    if (x) columns.push(columnFor(x))
    if (y) columns.push(columnFor(y))
    if (series) columns.push(columnFor(series))
    const spec: TableSpec = { variant: 'grid', columns }
    return { lang: 'table', spec }
  }
  const encoding: Record<string, unknown> = {}
  if (x) encoding.x = xEncoding(x)
  if (y) encoding.y = yEncoding(y)
  if (series && resolved.colorBy === series.key) {
    encoding.color = { field: series.key, type: 'nominal', title: fieldTitle(series) }
  }
  const spec: VegaLiteSpec = { mark: seriesMark(resolved.mark), encoding, data: { values: rows } }
  return { lang: 'vega-lite', spec }
}

function pairsViewWith(tool: ToolContract, rows: Row[], resolved: ResolvedPresentation): ViewSpec {
  const x = findRole(tool.item, 'x')
  const y = findRole(tool.item, 'y')
  const category = findRole(tool.item, 'category')
  if (resolved.mark === 'table') {
    const columns: TableColumn[] = []
    if (x) columns.push(columnFor(x))
    if (y) columns.push(columnFor(y))
    if (category) columns.push(columnFor(category))
    const spec: TableSpec = { variant: 'grid', columns }
    return { lang: 'table', spec }
  }
  let xEnc = x ? { field: x.key, type: 'quantitative', title: fieldTitle(x) } : undefined
  let yEnc = y ? { field: y.key, type: 'quantitative', title: fieldTitle(y) } : undefined
  if (resolved.swapXY && xEnc && yEnc) {
    const swapped = yEnc
    yEnc = xEnc
    xEnc = swapped
  }
  const encoding: Record<string, unknown> = {}
  if (xEnc) encoding.x = xEnc
  if (yEnc) encoding.y = yEnc
  if (category && resolved.colorBy === category.key) {
    encoding.color = { field: category.key, type: 'nominal', title: fieldTitle(category) }
  }
  const mark: VegaLiteSpec['mark'] = resolved.mark === 'line' ? { type: 'line', point: true } : 'point'
  const spec: VegaLiteSpec = { mark, encoding, data: { values: rows } }
  return { lang: 'vega-lite', spec }
}

function rankedViewWith(tool: ToolContract, rows: Row[], resolved: ResolvedPresentation): ViewSpec {
  if (resolved.mark === 'table') return sourceRankedView(tool)
  const label = findRole(tool.item, 'label')
  const value = findRole(tool.item, 'value')
  const encoding: Record<string, unknown> = {}
  if (label) encoding.y = { field: label.key, type: 'nominal', sort: '-x', axis: { title: null } }
  if (value) encoding.x = { field: value.key, type: 'quantitative', title: fieldTitle(value) }
  const keys = [label?.key, value?.key].filter((k): k is string => !!k)
  const spec: VegaLiteSpec = { mark: 'bar', encoding, data: { values: pickFields(rows, keys) } }
  return { lang: 'vega-lite', spec }
}

function breakdownViewWith(tool: ToolContract, rows: Row[], resolved: ResolvedPresentation): ViewSpec {
  if (resolved.mark === 'table') {
    const category = findRole(tool.item, 'category')
    const count = findRole(tool.item, 'count')
    const columns: TableColumn[] = []
    if (category) columns.push(columnFor(category))
    if (count) columns.push(columnFor(count))
    const spec: TableSpec = { variant: 'grid', columns }
    return { lang: 'table', spec }
  }
  return sourceBreakdownView(tool, rows)
}

/** 型 → ViewSpec。`presentation` が無指定、または固定表に無い組み合わせの
 *  ときは既定ビュー（presentation 導入前と同じ結果）を返す。役割（x/y/
 *  category/count/value）の割り当ては変えず、mark と encoding だけ差し替える。 */
export function viewFor(tool: ToolContract, rows: Row[], presentation?: Presentation): ViewSpec {
  const options = allowedPresentations(tool, rows)
  if (options.marks.length === 0) {
    // quantity/facts/flow: 切替なし（presentation は無視する）。
    switch (tool.output_kind) {
      case 'quantity':
        return quantityView(tool)
      case 'facts':
        return factsView(tool)
      case 'flow':
      default:
        return flowView()
    }
  }
  const resolved = resolvePresentation(tool.output_kind, options, presentation)
  switch (tool.output_kind) {
    case 'series':
      return seriesViewWith(tool, rows, resolved)
    case 'pairs':
      return pairsViewWith(tool, rows, resolved)
    case 'ranked':
      return rankedViewWith(tool, rows, resolved)
    case 'breakdown':
      return breakdownViewWith(tool, rows, resolved)
    default:
      return factsView(tool)
  }
}
