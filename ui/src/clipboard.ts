/** クリップボードへのコピー。
 *
 * `http://` オリジン（ローカルモードはこれ）では非同期 Clipboard API が使えない
 * ので、一時 textarea + execCommand に落とす。同じ実装が ToolRunner / RulesPanel
 * / 接続タブに散らばっていたのをここに集約している。
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}
