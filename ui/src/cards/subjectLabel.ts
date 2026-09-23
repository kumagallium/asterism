// K4: ラベルが無い IRI でも生の識別子を人に見せない — 末尾の名前に落とす。
// SubjectPage.tsx（見出し・パンくず・Ask の質問文）・SubjectRail.tsx（検索結果の
// 種類ボタン）の両方で使う共通の純関数。

/** `label` があればそれを使う。無ければ `iri` の末尾の名前
 *  （末尾の `/` `#` を落としてから最後の区切りより後ろ）に落とす。
 *  それも取れなければ `iri` をそのまま返す（最後の砦）。 */
export function subjectDisplayLabel(label: string | null | undefined, iri: string): string {
  if (label) return label
  return iri.replace(/[/#]+$/, '').split(/[/#]/).pop() ?? iri
}
