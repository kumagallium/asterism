/** つながりの関係図（K23）— データ —共通の値— データ を 1 枚の絵で言う。
 *
 *  「つながり」は 3 つの事実の組でできている: **どのデータとどのデータ**が、
 *  **どの項目の値**で、**何を同じとみなして**つながるか。これまでは同じ 3 つが
 *  「参加チップの行」「件数のバッジ」「同一視の 1 文」として画面の別々の場所に
 *  散っていて、読み手が頭の中で組み立て直す必要があった。
 *
 *  SVG ではなく flexbox。ノードの中身は利用者のデータ名と項目名なので、長さが
 *  読めない — 折り返せる普通のテキストである方が安全で、拡大縮小にも耐える。 */
export function XwLinkDiagram({
  sides,
  headline,
  note,
}: {
  /** つながる側。ふつうは 2 つ。 */
  sides: { key: string; name: string; field?: string; title?: string }[]
  /** 中央の 1 行（例「12 件の値が一致」）。 */
  headline: string
  /** 中央の副文（例「書き方のゆれは同じものとして扱います」）。 */
  note?: string
}) {
  const link = (
    <span className="xw-diagram-link">
      <span className="xw-diagram-line" aria-hidden="true" />
      <span className="xw-diagram-link-text">
        <span className="xw-diagram-head">{headline}</span>
        {note && <span className="xw-diagram-note">{note}</span>}
      </span>
      <span className="xw-diagram-line" aria-hidden="true" />
    </span>
  )
  const node = (s: { key: string; name: string; field?: string; title?: string }) => (
    <span key={s.key} className="xw-diagram-node" title={s.title}>
      <span className="xw-diagram-name">{s.name}</span>
      {s.field && <span className="xw-diagram-field">{s.field}</span>}
    </span>
  )
  // 3 つ以上つながる設計もある。そのときは横一列の「A—B」の形が崩れるので、
  // 中央のラベルを 1 行に出して、ノードは下に並べる（同じ事実を同じ順で言う）。
  if (sides.length !== 2) {
    return (
      <div className="xw-diagram xw-diagram--stack">
        <span className="xw-diagram-link-text">
          <span className="xw-diagram-head">{headline}</span>
          {note && <span className="xw-diagram-note">{note}</span>}
        </span>
        <div className="xw-diagram-nodes">{sides.map(node)}</div>
      </div>
    )
  }
  return (
    <div className="xw-diagram">
      {node(sides[0])}
      {link}
      {node(sides[1])}
    </div>
  )
}
