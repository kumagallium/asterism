// 取り込み元のファイルの一覧で使う定数と整形。
/** `chat_attach.TABLE_EXTS` と同じ（ドット付き小文字）。 */
export const TABLE_EXTS = ['.csv', '.tsv', '.xlsx', '.xls', '.json']

/** 大きさを KB / MB で。 */
export function formatSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}
