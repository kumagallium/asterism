// カード契約層の型。PR A（api/mcp）が返す ToolContract と、描画器 3 つ
// （Vega-Lite／グラフ／表）が読む ViewSpec を定義する。この層自体は
// 描画もデータ生成もしない（純粋な型宣言）。

/** 契約層（PR A）の「出口の意味型」。既定ビューはこの値だけから決まる。 */
export type OutputKind = 'quantity' | 'series' | 'pairs' | 'ranked' | 'breakdown' | 'facts' | 'flow'

/** result.item の 1 列が、絵の中でどの役割を担うか。 */
export type ItemRole = 'subject' | 'label' | 'value' | 'x' | 'y' | 'series' | 'category' | 'count' | 'at'

/** 契約層（PR A）が返す 1 ツールの result.item の 1 列。 */
export interface ItemSpec {
  var: string
  number?: boolean
  role?: ItemRole
  quantity_kind?: string | null
  unit?: string | null
  /** 見出し／軸タイトルに使う表示名。無ければ `var` の機械整形にフォールバック
   *  する（`defaultView.ts` の `humanizeKey`）。組み込みツールの item は呼び側
   *  （`builtinFields.ts` の `withFieldLabels`）がここへ焼き込む。 */
  label?: string
}

/** 契約層（PR A）が返す 1 ツールの宣言。 */
export interface ToolContract {
  name: string
  title: string
  output_kind: OutputKind
  item: Record<string, ItemSpec>
}

export type ViewLang = 'vega-lite' | 'graph' | 'table'

/** 描画に渡す仕様。`spec` はデータであって実行しない。 */
export interface ViewSpec {
  lang: ViewLang
  spec: VegaLiteSpec | GraphSpec | TableSpec
  /** true = 「書く」で LLM が書いた（印が付く・Phase 2）。既定ビューには付かない。 */
  custom?: boolean
}

/** Vega-Lite の JSON。データは spec.data.values に埋め込む。 */
export type VegaLiteSpec = Record<string, unknown>

export type GraphNodeKind = 'entity' | 'activity' | 'other'
export interface GraphNode {
  id: string
  label: string
  kind: GraphNodeKind
  props?: Record<string, string>
}
export interface GraphEdge {
  from: string
  to: string
  label?: string
}
export interface GraphSpec {
  direction?: 'LR' | 'TD'
  nodes: GraphNode[]
  edges: GraphEdge[]
  truncated?: boolean
}

export interface TableColumn {
  field: string
  label: string
  unit?: string | null
  format?: 'number' | 'integer' | 'text' | 'iri'
  align?: 'left' | 'right'
  /** この列の値が IRI に由来するとき、その IRI を持つ**兄弟**フィールド名
   *  （K4: 生の IRI は列として見せず、`/describe?iri=…` へのリンクにする —
   *  `TableView` の grid variant が読む）。 */
  href_field?: string
}
export interface TableHighlight {
  when: { field: string; op: 'gt' | 'lt' | 'eq'; value: number | string }
  style: 'accent' | 'muted'
}
export interface TableSpec {
  /** grid = 普通の表 / figure = 1 値を大きく（数値カード）/ ranked = 順位表（番号列つき）。 */
  variant?: 'grid' | 'figure' | 'ranked'
  columns: TableColumn[]
  sort?: { field: string; dir: 'asc' | 'desc' }
  group_by?: string
  limit?: number
  highlight?: TableHighlight[]
  /** grid・ranked 共通（K4）: 生の IRI を列として見せず、行の `title` と
   *  `onRowClick` に渡すためだけに持つ行のキー（1 件のページへの遷移用）。 */
  subject_field?: string
}

/** 描画に渡す行。ツール実行結果 items そのもの。 */
export type Row = Record<string, unknown>
