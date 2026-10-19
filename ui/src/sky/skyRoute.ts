// ホーム（星図）の hash のクエリ。`#/home?deep=1` は「中身まで見る」（値の網）、
// `#/home?new=<iri>,<iri>` は公開直後の「生まれた輪」（その点だけを 1 度灯す）。
// App.tsx の `parseHash`／`routeToHash` と、かんたんの完了画面（星図を見る）が同じ形を使う。

export interface HomeQuery {
  deep: boolean
  /** 灯す種類・用語の IRI。 */
  newIris: string[]
}

/** `?` の後ろ（`deep=1&new=a,b`）を読む。IRI は 1 つずつ encodeURIComponent されているので、
 *  先に `,` で割ってから戻す（IRI の中の `,` は %2C で来る）。 */
export function parseHomeQuery(query: string): HomeQuery {
  let deep = false
  let newIris: string[] = []
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
    }
  }
  return { deep, newIris }
}

/** `#/home` の hash（クエリは deep → new の順）。 */
export function homeHash(q: Partial<HomeQuery> = {}): string {
  const parts: string[] = []
  if (q.deep) parts.push('deep=1')
  if (q.newIris && q.newIris.length > 0) parts.push(`new=${q.newIris.map(encodeURIComponent).join(',')}`)
  return parts.length ? `#/home?${parts.join('&')}` : '#/home'
}
