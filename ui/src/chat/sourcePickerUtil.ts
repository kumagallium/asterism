// 取り込み元のファイルの一覧で使う定数と整形。
/** `chat_attach.TABLE_EXTS` と同じ（ドット付き小文字）。 */
export const TABLE_EXTS = ['.csv', '.tsv', '.xlsx', '.xls', '.json']
/** `chat_attach.DOCUMENT_EXTS` と同じ（論文: PDF・Word・JATS XML）。 */
export const DOCUMENT_EXTS = ['.pdf', '.docx', '.xml']
/** 📎 で添えられるもの（表と論文）。 */
export const ATTACH_EXTS = [...TABLE_EXTS, ...DOCUMENT_EXTS]

/** 大きさを KB / MB で。 */
export function formatSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

/** 添える名前が全部論文か（論文は AI を使わないので、AI 未設定でも送れる）。 */
export function allDocumentNames(names: string[]): boolean {
  return names.length > 0 && names.every((n) => DOCUMENT_EXTS.some((e) => n.toLowerCase().endsWith(e)))
}
