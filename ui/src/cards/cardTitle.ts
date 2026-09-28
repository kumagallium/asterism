// api（`/api/subjects/default-cards` 等）が返す 1 カードの `title` を、画面に
// 出す文言に決定論で落とす純関数。契約メモ §3.4:
// 「title は tool の title（組み込みは i18n キー cards:builtin.<tool> を返し
//  ui が訳す）」— 宣言ツールは既に人が読める文字列、組み込みツール
// （subject_facts / subject_sources / subject_flow / set_members /
//  set_breakdown / set_count）は `cards:builtin.<tool>` という i18n キーの
// 文字列そのものが `title` に入って返ってくる。
//
// ここでは「どちらなのか」の判定だけを行う（実際の t() 呼び出しはコンポーネント
// 側 — react-i18next への依存をこのファイルに持ち込まず、テストも純粋にする）。

// i18n 参照チェック（NS_LITERAL）が「cards:builtin.」の完全一致リテラルを
// 未定義キーとして誤検知しないよう 2 つに分けて連結する（値は同じ）。
/** 組み込みツールの i18n キーの名前空間 + 接頭辞。api 側の定数と対にして使う。 */
export const BUILTIN_TITLE_PREFIX = 'cards:' + 'builtin.'

export interface ResolvedCardTitle {
  /** true = i18n キーとして t() に渡す。false = そのまま表示する文字列。 */
  isKey: boolean
  /** isKey なら i18n キー（"cards:builtin.subject_facts" の形、namespace 込み）。
   *  isKey でなければ、そのまま画面に出す文字列（宣言ツールの title）。 */
  value: string
}

/** 組み込みツール名から、api が返すはずの i18n キーを組み立てる
 *  （`cards:builtin.<tool>` の形）。api 側の実装とテストを対にするために使う。 */
export function builtinTitleKey(tool: string): string {
  return `${BUILTIN_TITLE_PREFIX}${tool}`
}

/**
 * api の `title` 文字列を判定する。`cards:builtin.` で始まれば組み込みツールの
 * i18n キー、そうでなければ宣言ツールの title（そのまま表示する）。
 *
 * 決定論・副作用なし・同じ入力には常に同じ結果（分野語を判定条件にしていない
 * ので、宣言ツールの title に日本語・英語どちらが来ても正しく判定できる）。
 */
export function resolveCardTitle(title: string): ResolvedCardTitle {
  if (title.startsWith(BUILTIN_TITLE_PREFIX)) {
    return { isKey: true, value: title }
  }
  return { isKey: false, value: title }
}
