// ホーム（作業場）の hash のクエリ。`#/home?deep=1` は「中身まで見る」（値の網）、
// `#/home?new=<iri>,<iri>` は公開直後の「生まれた輪」（その点だけを 1 度灯す）、
// `#/home?write=1[&q=<題>][&resume=<下書きの id>][&extend=<テーマの id>&label=<名前>]` は左の会話を
// 「問いを書く」で開く（旧 `#/questions?new=1…` も同じ）。
// App.tsx の `parseHash`／`routeToHash` と、かんたんの完了画面（星図を見る）が同じ形を使う。

/** 「問いを書く」で会話を開く合図。`resume`＝続きの下書き・`extend`＝既にあるテーマに問いを足す。 */
export interface WriteRequest {
  q: string
  resume: string
  extend: { id: string; label: string } | null
}

export interface HomeQuery {
  deep: boolean
  /** 灯す種類・用語の IRI。 */
  newIris: string[]
  /** 会話を「問いを書く」で開く（無ければ null）。 */
  write: WriteRequest | null
}

/** `?` の後ろ（`deep=1&new=a,b`）を読む。IRI は 1 つずつ encodeURIComponent されているので、
 *  先に `,` で割ってから戻す（IRI の中の `,` は %2C で来る）。 */
export function parseHomeQuery(query: string): HomeQuery {
  let deep = false
  let newIris: string[] = []
  let write = false
  let q = ''
  let resume = ''
  let extendId = ''
  let label = ''
  const dec = (s: string): string => {
    try {
      return decodeURIComponent(s.replace(/\+/g, ' '))
    } catch {
      return s
    }
  }
  for (const pair of query.split('&')) {
    const eq = pair.indexOf('=')
    const key = eq === -1 ? pair : pair.slice(0, eq)
    const raw = eq === -1 ? '' : pair.slice(eq + 1)
    if (key === 'deep') deep = raw === '1'
    else if (key === 'new') {
      newIris = raw
        .split(',')
        .filter(Boolean)
        .map((s) => {
          try {
            return decodeURIComponent(s)
          } catch {
            return s
          }
        })
    } else if (key === 'write') write = raw === '1'
    else if (key === 'q') q = dec(raw)
    else if (key === 'resume') resume = dec(raw)
    else if (key === 'extend') extendId = dec(raw)
    else if (key === 'label') label = dec(raw)
  }
  return {
    deep,
    newIris,
    write: write ? { q, resume, extend: extendId ? { id: extendId, label } : null } : null,
  }
}

/** `#/home` の hash（クエリは deep → new → write の順）。 */
export function homeHash(q: Partial<HomeQuery> = {}): string {
  const parts: string[] = []
  if (q.deep) parts.push('deep=1')
  if (q.newIris && q.newIris.length > 0) parts.push(`new=${q.newIris.map(encodeURIComponent).join(',')}`)
  if (q.write) {
    parts.push('write=1')
    if (q.write.q) parts.push(`q=${encodeURIComponent(q.write.q)}`)
    if (q.write.resume) parts.push(`resume=${encodeURIComponent(q.write.resume)}`)
    if (q.write.extend) {
      parts.push(`extend=${encodeURIComponent(q.write.extend.id)}`)
      if (q.write.extend.label) parts.push(`label=${encodeURIComponent(q.write.extend.label)}`)
    }
  }
  return parts.length ? `#/home?${parts.join('&')}` : '#/home'
}
