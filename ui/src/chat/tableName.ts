/** 論文の表の名前。日本語の画面では `Table 2` を「表 2」に（それ以外の名前はそのまま・無ければ id）。 */
export function tableName(label: string, id: string, lang: string): string {
  const m = /^\s*(?:table|tab\.?)\s*(\d+)\s*$/i.exec(label)
  if (m && lang.startsWith('ja')) return `表 ${m[1]}`
  return label.trim() || id
}
