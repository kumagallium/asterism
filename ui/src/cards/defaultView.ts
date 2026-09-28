// 出口の意味型（`OutputKind`）→ 既定ビュー（`ViewSpec`）を決定論で決める。
// 分野語の辞書は持たない — 列の見出しは項目のキーを機械的に整形して作る
// （`_iri` を落とす・`_` を空白に）。LLM は呼ばない。
import type { ItemRole, ItemSpec, Row, TableColumn, TableSpec, ToolContract, VegaLiteSpec, ViewSpec } from './viewSpec'
import { unitLabel } from './unitLabel'

/** キーを人が読める見出しにする決定論（分野語の辞書は持たない）。 */
function humanizeKey(key: string): string {
  const stripped = key.endsWith('_iri') ? key.slice(0, -4) : key
  return stripped.replace(/_/g, ' ')
}

function allByRole(item: Record<string, ItemSpec>, role: ItemRole): ItemSpec[] {
  return Object.values(item).filter((i) => i.role === role)
}

function findRole(item: Record<string, ItemSpec>, role: ItemRole): ItemSpec | undefined {
  return allByRole(item, role)[0]
}

/** 1 件のページを指す IRI 列（K4: 列としては出さず行クリックの遷移先にする）。
 *  `role: 'subject'` を優先し、組み込みツールの契約キー `subject_iri`
 *  （`builtinFields.ts` のコメント参照）へフォールバックする。 */
function findSubject(item: Record<string, ItemSpec>): ItemSpec | undefined {
  return findRole(item, 'subject') ?? Object.values(item).find((i) => i.var === 'subject_iri')
}

/** 見出し（＋単位があれば `[単位]` を添える。表の列見出しと Vega-Lite の軸タイトルで共通）。
 *  `item.label` があればそれを使い（呼び側が組み込みツールの表示名を焼き込む —
 *  `builtinFields.ts`）、無ければキーの機械整形にフォールバックする。 */
function fieldTitle(item: ItemSpec): string {
  const label = item.label ?? humanizeKey(item.var)
  return item.unit != null ? `${label} [${unitLabel(item.unit)}]` : label
}

/** 表の 1 列。`role: 'subject'` または `_iri` で終わる変数名は IRI 列とみなす。 */
function columnFor(item: ItemSpec): TableColumn {
  const isIri = item.var.endsWith('_iri') || item.role === 'subject'
  const col: TableColumn = {
    field: item.var,
    label: item.label ?? humanizeKey(item.var),
    format: isIri ? 'iri' : item.number ? 'number' : 'text',
  }
  if (item.unit != null) col.unit = unitLabel(item.unit)
  if (item.number) col.align = 'right'
  return col
}

/** x 軸の encoding。`number: false` の項目だけ ordinal にする（契約 §5）。 */
function xEncoding(item: ItemSpec): Record<string, unknown> {
  return { field: item.var, type: item.number === false ? 'ordinal' : 'quantitative', title: fieldTitle(item) }
}

/** y 軸の encoding。x と違い、`number: false` でも常に quantitative（契約 §5）。 */
function yEncoding(item: ItemSpec): Record<string, unknown> {
  return { field: item.var, type: 'quantitative', title: fieldTitle(item) }
}

function quantityView(tool: ToolContract): ViewSpec {
  const columns: TableColumn[] = []
  const value = findRole(tool.item, 'value')
  if (value) columns.push(columnFor(value))
  for (const role of ['at', 'label', 'subject'] as const) {
    for (const spec of allByRole(tool.item, role)) columns.push(columnFor(spec))
  }
  const spec: TableSpec = { variant: 'figure', columns }
  return { lang: 'table', spec }
}

function seriesView(tool: ToolContract, rows: Row[]): ViewSpec {
  const x = findRole(tool.item, 'x')
  const y = findRole(tool.item, 'y')
  const series = findRole(tool.item, 'series')
  const encoding: Record<string, unknown> = {}
  if (x) encoding.x = xEncoding(x)
  if (y) encoding.y = yEncoding(y)
  if (series) encoding.color = { field: series.var, type: 'nominal', title: fieldTitle(series) }
  const spec: VegaLiteSpec = { mark: { type: 'line', point: true }, encoding, data: { values: rows } }
  return { lang: 'vega-lite', spec }
}

function pairsView(tool: ToolContract, rows: Row[]): ViewSpec {
  const x = findRole(tool.item, 'x')
  const y = findRole(tool.item, 'y')
  const encoding: Record<string, unknown> = {}
  if (x) encoding.x = { field: x.var, type: 'quantitative', title: fieldTitle(x) }
  if (y) encoding.y = { field: y.var, type: 'quantitative', title: fieldTitle(y) }
  const spec: VegaLiteSpec = { mark: 'point', encoding, data: { values: rows } }
  return { lang: 'vega-lite', spec }
}

function rankedView(tool: ToolContract): ViewSpec {
  // 生の IRI（subject）はセルとして描かない（K4）── 行の title / onRowClick に
  // 渡すためだけに `subject_field` へ持たせ、columns には入れない。
  const columns: TableColumn[] = []
  const label = findRole(tool.item, 'label')
  if (label) columns.push(columnFor(label))
  const value = findRole(tool.item, 'value')
  if (value) columns.push(columnFor(value))
  const subject = findSubject(tool.item)
  const spec: TableSpec = { variant: 'ranked', columns }
  if (subject) spec.subject_field = subject.var
  if (value) spec.sort = { field: value.var, dir: 'desc' }
  return { lang: 'table', spec }
}

function breakdownView(tool: ToolContract, rows: Row[]): ViewSpec {
  const category = findRole(tool.item, 'category')
  const count = findRole(tool.item, 'count')
  const encoding: Record<string, unknown> = {}
  // y 軸（出どころ等）の title は出さない（縦書きで軸ラベルに重なる・カードの
  // 見出しと列のラベルで意味は足りている）。x 軸の title はそのまま出す。
  if (category) encoding.y = { field: category.var, type: 'nominal', sort: '-x', axis: { title: null } }
  // 件数は整数（0.0〜1.0 のような小数目盛にしない）。
  if (count)
    encoding.x = {
      field: count.var,
      type: 'quantitative',
      title: fieldTitle(count),
      axis: { tickMinStep: 1, format: 'd' },
    }
  const spec: VegaLiteSpec = { mark: 'bar', encoding, data: { values: rows } }
  return { lang: 'vega-lite', spec }
}

function factsView(tool: ToolContract): ViewSpec {
  const allEntries = Object.values(tool.item)
  const byVar = new Map(allEntries.map((e) => [e.var, e]))
  // 1 件のページを指す列（K4: 列にせず行クリックの遷移先へ・ranked と同じ
  // 扱い）。組み込みツール（例: set_members）の `subject_iri` はここで除く。
  const subject = findSubject(tool.item)
  const entries = subject ? allEntries.filter((i) => i !== subject) : allEntries
  const normal = entries.filter((i) => !i.var.endsWith('_iri'))
  // `_iri` で終わる列のうち、兄弟のラベル列（`X_iri` に対する `X`）が無い
  // ものだけ列として残す（例: 宣言ツールの `sample_iri`）。兄弟があるものは
  // 列を作らず、下で兄弟列の `href_field` として付ける（K4）。
  const orphanIri = entries.filter((i) => i.var.endsWith('_iri') && !byVar.has(i.var.slice(0, -4)))
  const columns = [...normal, ...orphanIri].map((entry) => {
    const col = columnFor(entry)
    if (!entry.var.endsWith('_iri')) {
      const sibling = byVar.get(`${entry.var}_iri`)
      if (sibling && sibling !== subject) col.href_field = sibling.var
    }
    return col
  })
  const spec: TableSpec = { variant: 'grid', columns }
  if (subject) spec.subject_field = subject.var
  return { lang: 'table', spec }
}

/** `flow` は rows を使わない（呼び側が provenance から得た GraphSpec を持つ）。
 *  ここでは空の graph を返し、呼び側が差し替える。 */
function flowView(): ViewSpec {
  return { lang: 'graph', spec: { nodes: [], edges: [] } }
}

/** 型 → 既定ビュー。同じ `tool`/`rows` に対して常に同じ `ViewSpec` を返す（決定論）。
 *  分野語の辞書は持たない。返り値に `custom` は付かない（LLM が書いたものではない）。 */
export function defaultViewFor(tool: ToolContract, rows: Row[]): ViewSpec {
  switch (tool.output_kind) {
    case 'quantity':
      return quantityView(tool)
    case 'series':
      return seriesView(tool, rows)
    case 'pairs':
      return pairsView(tool, rows)
    case 'ranked':
      return rankedView(tool)
    case 'breakdown':
      return breakdownView(tool, rows)
    case 'facts':
      return factsView(tool)
    case 'flow':
      return flowView()
  }
}
