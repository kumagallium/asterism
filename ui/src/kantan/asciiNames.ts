// 英字にできない名前から、決定論的に一意な識別子を作る（TypeScript 版）。
//
// Python 版 `step0/src/asterism_step0/ascii_names.py`（契約メモ
// contract_a_kind_display_names.md R1）の移植。列名やファイル名が日本語だけ
// だと、機械が作る名前が `value`／`Value` に潰れ、違う名前どうしが番号でしか
// 区別できなくなる穴を、ここに置く2つの道具（`nameTag`／`losesWords`）だけで
// 埋める。同じ入力からは、いつも同じ符号（乱数・時刻・列の順番を使わない）。
//
// `classNameFromLabel` は R4（「名前・ID を直す」欄・かんたんモード）が使う
// `_class_name` の TypeScript 版 — 打った表示名から、公開される種類の識別子
// を 1 つ作る。5 つの見本の `nameTag` 期待値は Python 側と両方のテストに
// 同じ値を固定してある（asciiNames.test.ts）。

const FNV_OFFSET_BASIS = 0x811c9dc5
const FNV_PRIME = 0x01000193

/** `text` から決定論的に導く6桁の16進符号。
 *
 * `NFKC(text).trim()` の UTF-8 バイト列に FNV-1a 32bit（初期値
 * `0x811C9DC5`・素数 `0x01000193`）をかけ、8桁の16進表現の先頭6桁を返す。
 * 同じ入力は、いつ・どこで呼んでも同じ符号になる（Python 版と一致）。
 */
export function nameTag(text: string): string {
  const bytes = new TextEncoder().encode(text.normalize('NFKC').trim())
  let h = FNV_OFFSET_BASIS
  for (const byte of bytes) {
    h ^= byte
    h = Math.imul(h, FNV_PRIME) >>> 0
  }
  return h.toString(16).padStart(8, '0').slice(0, 6)
}

// 大文字小文字の区別が無い文字体系（日本語の漢字・かな、中国語の漢字など）。
// Lo = other letter（かな・漢字・ハングル音節など）、Lm = modifier letter
// （長音記号「ー」など）。ASCII 部分を単語として使うと意味を失う合図。
const NO_CASE_RE = /\p{Lo}|\p{Lm}/u

/** `text` に、大文字小文字の区別が無い文字が1つでもあれば真。 */
export function losesWords(text: string): boolean {
  return NO_CASE_RE.test(text)
}

/** ASCII の部分だけを lowerCamel にする（`_ascii_camel` の写し）。
 *  ASCII の部分が無ければ空文字を返す — 呼び出し側が fallback を選ぶ。 */
function asciiCamel(text: string): string {
  const parts = text.split(/[^0-9A-Za-z]+/).filter(Boolean)
  if (parts.length === 0) return ''
  const head = parts[0]
  const headLower = /^[a-z]/.test(head) ? head : head[0].toLowerCase() + head.slice(1)
  const out =
    headLower + parts.slice(1).map((p) => p[0].toUpperCase() + p.slice(1)).join('')
  return /^[0-9]/.test(out) ? `v${out}` : out
}

/** `xrd_peaks` → `XrdPeaks`（PascalCase・ASCII-safe）。英字にできない名前
 *  （ASCII の部分が空、または漢字・かなを含む）は `fallback`（か ASCII の
 *  部分）に {@link nameTag} の符号を付けて一意にする。R4 の「名前・ID を
 *  直す」欄が、打った表示名から種類の識別子を作るのに使う（`_class_name`
 *  と同じ規則）。 */
export function classNameFromLabel(text: string, fallback = 'record'): string {
  const asciiPart = asciiCamel(text)
  if (asciiPart && !losesWords(text)) {
    return asciiPart[0].toUpperCase() + asciiPart.slice(1)
  }
  const base = asciiPart || fallback
  const pascalBase = base ? base[0].toUpperCase() + base.slice(1) : 'Record'
  return `${pascalBase}_${nameTag(text)}`
}
