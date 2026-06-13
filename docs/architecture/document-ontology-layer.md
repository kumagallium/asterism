# 文書オントロジー層（論文本文の構造化 + data↔text 融合）ADR

status: **MVP 着地**（`datasets/papers/` = 3 個目の example dataset・実 PMC OA JATS で
全 §B.4 ゲート PASS。実 morph-kgc + 実 Oxigraph 検証済 — `docs/reports/document-ontology-mvp.md`）。
決定母体: [`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md)（dataset 一般化・
FROM-merge・promote・TBox 投影）/ [`non-csv-sources.md`](non-csv-sources.md)（#19 = 取り込みは
ソース形式非依存・`referenceFormulation` で読み分け）/ [`crosswalk-hub.md`](crosswalk-hub.md)（直交）。
de-risk spike = PR #188（`experiments/jats-xpath-spike/`）。

## 0. 一行サマリ

Asterism のコア命題（**設計オントロジー＋安定 IRI＋決定論的派生＋PROV＋引用**）を「データの形
（CSV/JSON）」から「**文章の形（論文本文）**」へ当てる。`論文 → 節 → 段落 → 文` まで解決可能な IRI を
与え、recall は paper でなく**文ノード**を返す（verbatim ＋ 構造パス ＋ PROV）。さらに本文ノードと
既存データ（curve）を結ぶ（**融合**）。crosswalk とは直交（縦 vs 横）。

## 1. 動機と非ゴール

`sd:Paper` は `prov:Entity` だが中身のない書誌ノードで、「paper SID-6」までしか指せない。本層は
「§4 第2段落の1文目」まで解決可能にする。

- **ゴール**: 文書内アドレッシング（どこにあるか）＋データへの選択的リンク。
- **非ゴール**: 文の**意味**を全 triple 化しない（オープンワールド普遍オントロジーの失敗教訓）。
  構造化するのは**位置と選択的リンクだけ**、文の中身は引用・解決できる不透明リテラルのまま。
- **非ゴール**: RAG の再発明。固有価値は **join（text↔data, 共有エンティティ経由の text↔text）と
  来歴付きの解決可能な引用**にしかない。

## 2. 採用オントロジー（標準を引く＝QUDT/PROV と同じ流儀）

独自語彙を作らず SPAR + NIF を引く。

| prefix | URI | 用途 |
|---|---|---|
| `fabio` | `http://purl.org/spar/fabio/` | 書誌実体（`fabio:ResearchPaper`） |
| `doco` | `http://purl.org/spar/doco/` | 文書構造（`Section`/`Paragraph`/`Sentence`/`Figure`/`Caption`） |
| `deo` | `http://purl.org/spar/deo/` | 修辞役割（`Introduction`/`Methods`/`Results`/`Discussion`/`Conclusion`） |
| `cito` | `http://purl.org/spar/cito/` | 引用関係（`cito:usesDataFrom` — curve→図） |
| `po` | `http://www.essepuntato.it/2008/12/pattern#` | 構造包含（`po:contains`） |
| `nif` | `http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#` | 文字オフセット（`isString`/`anchorOf`/`referenceContext`/`beginIndex`/`endIndex`） |
| `prov` `dcterms` `rdfs` | （既存） | 来歴・DOI/題名・ラベル |

dataset 固有述語だけ `lit:` = `https://kumagallium.github.io/asterism/papers/ontology#` に置く
（`pmcid` / `structuralPath` = アドレッシング、`conditionStatedIn` = 融合リンク、
`DocumentParsingActivity` / `sourceFormat` / `parser` = 解析来歴の封筒）。**`sd:` には結合しない**
（§A.7 の脱結合方針）。

## 3. IRI 設計（@id を一次キーに — spike の理想化を実データへ）

```
…/papers/resource/paper/<PMCID>
  /sec/<jats @id>            doco:Section（po:contains で木）
  /sec/<id>/para/<k>         doco:Paragraph（節内の位置 k — 後段パス）
  /sec/<id>/para/<k>/sent/<j> doco:Sentence（分割 — 後段パス）
  /fig/<jats @id>            doco:Figure → /caption  doco:Caption
  /fulltext                  nif:Context（全文・offset の参照先）
```

**決定: 節/図の IRI は JATS の `@id` をキーにする**（spike は合成 `s3-2` を使ったが、実 JATS の
`@id` こそ「出版社が付与した安定識別子＝決定論的転写」）。人間可読な構造番号は IRI でなく
`lit:structuralPath`（`"4"` / `"2-1"`、`fn:structural_slug` 由来）として**プロパティ**で持つ
（IRI の安定性とトレードオフしない）。一度発行した文ノード IRI は固定（Graphium ノートや curve が
引用した後に変えると壊れる）。

## 4. 取り込み経路 — 宣言 RML ＋ 決定論的後段パスの**ハイブリッド**（実データで判明した本筋）

spike は「paper→sec→para は純宣言 RML」と示したが、それは合成データに段落 `@id` があったから。
**実 PMC JATS では段落 `<p>` に `@id` が無い**（PMC5951533: 55 段落中 0）。さらに morph-kgc の
XML リーダは **stdlib ElementTree**（lxml でない）で、参照に強い制約がある。よって責務を分ける:

**(A) 宣言 RML（`jats/<PMCID>.rml.ttl`・`ql:XPath`・Tier0 のみ・生成コード無し）** が
**id を持つ構造骨格**を出す: paper ＋ section 木（入れ子 sec 含む・全て `@id`）＋ figure（`@id`+label）
＋ `po:contains` ＋ `lit:structuralPath`（`fn:structural_slug`）。これが spike の主張を実データで実証。

**(B) 決定論的後段パス（`seed/build_paper_graph.py`・content tool・stdlib・LLM 非介在）** が
RML で出せない残りを出す: `doco:Paragraph`（節内位置）・`doco:Sentence`（分割）・忠実な
`nif:isString` verbatim・`nif:` offset・`nif:Context`・融合リンク。**`lit:DocumentParsingActivity`
（`sourceFormat`/`parser`/`prov:endedAtTime`）に日付つき claim として記録**＝構造（=§4 である）は
高信頼、文境界は parser の claim（低信頼）と分けて監査・再実行可能にする。これが LLM 自動抽出との差分。

RML の出力は seed（B が出す完全グラフ）の**厳密な部分集合**（検証で 86 ⊆ 1821 を実証）＝宣言経路が
骨格を忠実に出すことの証明。promote されるのは完全な `seed/paper.ttl`。

### morph-kgc XML の恒久知見（MVP 設計に直結・後続の罠回避）

- **iterator は完全な XPath 3.0**（`elementpath` ライブラリ）: `/article/body//fig`・述語 OK。
- **参照/テンプレートは stdlib ElementTree.findall**: (1) `[@a='v']` 述語は `@` 素朴分割で壊れる
  ＝**参照に述語を書けない**。(2) 親/祖先軸も不可。(3) 要素の **`.text` のみ**返す＝混在内容
  （`<sub>`/`<italic>` 入り題名・キャプション）は**切れる**＝忠実 verbatim は後段パス。
- **テンプレートは最低 1 `{ref}` 必須**＋ancestor article-id は子 iterator から不可 ⇒
  **paper-IRI base は per-paper 定数**（`rr:constant` subject・ingest は per-paper）。
- containment は親→子の多値子参照（`{sec/@id}`・`{fig/@id}`）＝1 子 1 triple。
- 安全 allowlist に `.xml` を意図的に追加（`rml_safety._ALLOWED_SOURCE_SUFFIXES`）＝**読み込み形式**を
  広げるだけで、実行できる関数（Tier0 のみ）/到達できるパス（confined）は不変。

容器は既存と同一: 別 canonical graph（`…/graph/canonical/papers`）＋ control-flag `promoted` ＋
registry dataset（`dataset.toml`/`model.yaml`/`diagram` は README/`query_tools.yaml`）。FROM-merge は
promoted graph を束ねるので、論文構造グラフは「もう一枚の promoted graph」。

### 4bis. ランタイム文書取り込み（アップロードした文書を UI 経由で文まで・PR-1）

MVP の後段パス(B)は **オフラインの content tool**（`build_paper_graph.py`・例データセット用）。これを
**再利用ライブラリ `asterism.documents`** に一般化し、**api の取り込み経路に配線**した＝ユーザーが
カタログから JATS 文書をアップロード→`/api/datasets/{id}/ingest` で**スキーマ設計なしに文まで構造化＋
引用可能**になる。

- `asterism.documents.structure_jats(xml, *, paper_iri) -> rdflib.Graph`: 汎用 JATS→doco/nif（節木+
  `structuralPath`+DEO 役割、段落、文+`nif`offset+PROV、図+caption、Context、`DocumentParsingActivity`）。
  `sentence_spans` は `build_paper_graph` と共有（splitter の単一の真実源）。
- **api 分岐**: `source_kind == "xml"`（document）は **RML 不要・morph-kgc 不要**で structurer 1 本（`materialize_to_nt_file` の代わりに `documents.document_to_nt_file`）→ 既存の version graph ストリーム＋人間ゲート promote にそのまま乗る。CSV/JSON は従来の RML 経路。
- **信頼境界（不変）**: structurer は **閉じた・一度 vet した・決定論的パーサ**＝文書から**コードを実行しない**（Tier0/Morph-KGC と同じ trust model）。アップロード XML は untrusted なので **defusedxml** で entity 展開（billion laughs）/外部実体（SSRF・ローカルファイル読み）を拒否（無害な JATS DOCTYPE 宣言は許可）。
- **決定論/idempotency**: `now()` 不使用（`prov:endedAtTime` は文書の pub-date・activity IRI は内容ハッシュ）＝同一バイトの再取り込みで同一グラフ。
- **引用ツールは横断**: `search_text`/`quote_with_citation` は dataset 非スコープ（FROM-merge 全体を検索）＝**アップロードした文書も promote 済なら既存ツールでそのまま引用可能**（実機実証済）。
- **変換前段（PDF/Word）**: `experiments/{pdf-docling,word-pandoc}-spike/` で de-risk 済。変換は **オフライン・PROV `lit:DocumentConversionActivity`(変換器+版+日付)** の日付つき主張＝信頼度ラベル（JATS=高/Word(pandoc)=高/born-digital PDF(Docling)=中/scan=低）。upload 導線への変換統合は後続 PR。

## 5. recall（文単位の引用）— `query_tools.yaml`

key-free・citable・再現可能な typed ツール（`asterism.query_tools`・FROM-merge・LLM-free）:

- `search_text(query)` — 全文（`nif:anchorOf`）検索 → ヒット文ノード＋offset＋構造パス。
- `quote_with_citation(node)` — verbatim ＋ 解決可能 IRI ＋ 構造パス ＋ PROV（parse activity）。
- `fetch_passage(paper, section?)` — 構造ノードと配下段落の verbatim。
- `measurement_provenance(composition?)` — **融合**（§6）。

## 6. 融合（data↔text）— 価値の本命

curve を**図ノード**と**測定条件の文**に結ぶ:

```turtle
sdr:curve/X  prov:wasDerivedFrom <…/fig/3> ; cito:usesDataFrom <…/fig/3> .
sdr:sample/X lit:conditionStatedIn <…/sec/4/para/1/sent/0> .   # "…PPMS…TTO…" の文
```

問い「この curve はどの条件で測られたか」に、データノード→該当文へ飛んで**両方を引用して**答えられる
（RAG が原理的にできない横断結合）。MVP では PMC5951533 の Figure 3（transport 図）と §4 の PPMS/TTO
文に実リンク。real starrydata 行はライセンスで非コミットのため curve 側は明示ラベル付き demo fixture。

## 7. crosswalk との関係（直交・無矛盾）

- **crosswalk = 横**（dataset 横断・共有値で entity を結ぶ・breadth）／**文書層 = 縦**（単一ソースを
  内部分解・depth）。両者は同じ統治哲学（薄い・決定論的派生＝日付つき PROV claim・加算オーバーレイ）。
- **守る一線（§A.7-4）**: crosswalk は **entity と明示・正規化済みの値**に掛ける。`xw:hasComposition`
  を `doco:Sentence` に下げない。**prose-location は文書層が `lit:conditionStatedIn` で持つ**。
  running prose への NER 推測を決定論的 join に混ぜない。
- 検証（回帰）: 文書層 promote の**前後で hub の shared compositions 数・既存 typed ツールが不変**
  （実証済: `{Bi2Te3, PbSe, SnSe}` 不変・mp `structure_by_composition` 不変）。論文を hub に参加させる
  Rule（`crosswalk_hub`）は**別 PR・後回し**（join-key 一般化と合わせて）。

## 8. 非ゴール / 後続

任意 PDF（レイアウト解析＋NLP・別物）、文の意味の構造化、hub join-key 一般化（composition→任意キー・
DOI 共有 paper）、`oa:TextQuoteSelector` の prefix/suffix 再分割耐性、Graphium ノートからの文単位引用 UI、
propose の実 LLM dogfood（XML 経路は inspect `## XML:` ＋ §9 ガイダンス済・要キー）、複数論文への拡張。

## 参考

- SPAR Ontologies <http://www.sparontologies.net/>（FaBiO/DoCO/DEO/CiTO/PO）/ NIF 2.0 / PROV-O /
  W3C Web Annotation。原ハンドオフ: `handoff_to_claude_code_document_ontology.md`。
