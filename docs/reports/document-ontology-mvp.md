# 検証レポート: 文書オントロジー層 MVP（論文本文の構造化 + data↔text 融合）

2026-06-11 / 関連 ADR: [`document-ontology-layer.md`](../architecture/document-ontology-layer.md)

## Question

Asterism のコア命題（設計オントロジー＋安定 IRI＋決定論的派生＋PROV＋引用）は「論文という文章」にも
成立するか。具体的に、**実在の PMC OA 論文 1 本**で次が同時に通るか:

1. **構造往復** — `paper ↔ 節 ↔ 段落 ↔ 文` を双方向に辿れる。
2. **引用** — 文ノードが verbatim ＋ 解決可能 IRI ＋ 構造パス ＋ PROV を返す。
3. **融合** — 測定 curve から「その図」と「測定条件を述べた文」へ決定論的に飛び、両方を引用できる。
4. **idempotency** — 同一 JATS を再取り込みして同一グラフ。
5. **crosswalk 回帰** — 文書層 promote の前後で hub の shared compositions と既存 typed ツールが不変。

## Method

- **題材**: DOI `10.3390/ma11040649` → **PMC5951533**（Schwall & Balke, MDPI *Materials*, **CC-BY 4.0**,
  n 型 half-Heusler 熱電・Starrydata の実ソース論文＝47 samples）。実 JATS を Europe PMC の
  `fullTextXML` から取得しコミット（`datasets/papers/jats/PMC5951533.xml`・314 KB）。
- **取り込み**: 宣言 RML（`jats/PMC5951533.rml.ttl`・`ql:XPath`・Tier0 のみ）が id 付き構造骨格を、
  決定論的後段パス（`seed/build_paper_graph.py`・stdlib・LLM 非介在）が段落/文/offset/verbatim/融合と
  `lit:DocumentParsingActivity` claim を出す（ADR §4）。promote するのは完全な `seed/paper.ttl`。
- **スタック**: 実 morph-kgc 2.10 ＋ **使い捨て Oxigraph（:7879・共有 :7878 不可侵）**。production の
  取り込みコード（`materialize_to_nt_file` → `stream_nt_file_to_oxigraph`）と typed ツール
  （`run_query_tool`・canonical FROM-merge）と crosswalk runtime（`build_hub`）を**そのまま**駆動。
- **再現**: `scripts/verify_document_ontology_mvp.py`（11 ゲートを PASS/FAIL で出力）＋
  `ingest/tests/test_papers_dataset.py`（rdflib FROM-merge での同等検証）。

## Result（全 11 ゲート PASS・実 :7879）

| # | ゲート | 結果 |
|---|---|---|
| 1 | production materialize（`ql:XPath`・実 JATS）→ Oxigraph | **86** 骨格 triple を production 経路でストリーム |
| 2 | 宣言 RML 出力 ⊆ promote 済 paper グラフ | 86/86 が paper グラフに在る（宣言経路は忠実） |
| 3 | idempotency（同一 JATS 再取り込み → 同一グラフ） | 1821 == 1821 triple（再 POST 後不変） |
| 4 | 決定論的 build tool（再実行でバイト一致） | sha256 一致 |
| 5 | 構造往復 `paper↔sec↔para↔sent` | `po:contains` 下降 ＋ `nif:referenceContext`/`prov:wasQuotedFrom` 上昇 |
| 6 | `search_text("PPMS")` | §4（structuralPath="4"）の PPMS 文ノードを返す |
| 7 | `quote_with_citation` | verbatim ＋ IRI ＋ パス ＋ PROV（parser=`asterism-jats/0.1`・offset 27209–27431） |
| 8 | `fetch_passage(PMC5951533, "4")` | Materials and Methods の 2 段落（"argon atmosphere" 含む） |
| 9 | **融合** `measurement_provenance` | curve(Ti0.5Zr0.25Hf0.25NiSn,ZT) → **Figure 3** ＋ PPMS/TTO 文（両方を実 IRI で引用） |
| 10 | crosswalk 回帰: hub shared compositions 不変 | `{Bi2Te3, PbSe, SnSe}`（total 3）が paper promote 前後で同一 |
| 11 | crosswalk 回帰: 既存 mp typed ツール不変 | `structure_by_composition(Bi2Te3)` 1 行・不変 |

完全グラフ規模: paper.ttl = **1821 triple**（1 ResearchPaper・16 Section・32 Paragraph・196 Sentence・
13 Figure・13 Caption・1 Context・1 DocumentParsingActivity・DEO 役割 6）。

ユニットテスト（`pytest`）: ingest 261 passed / step0 40 / api 88、ruff clean、全パッケージ緑。

## Conclusion

コア命題は「文章の形」でも成立する。**宣言 RML（構造骨格）＋ 決定論的後段パス（段落/文/offset を
日付つき claim）のハイブリッド**で、実在 CC-BY 論文を文単位まで解決可能・引用可能にできた。**融合**は
RAG が原理的に出せない「この数値 → この図 → この測定条件文」の決定論的横断結合を、両端の解決可能 IRI
付きで実証。crosswalk とは直交（promote しても hub 不変）。

## Limitations

- **文分割は parser の claim**（高信頼の構造とは分離・`DocumentParsingActivity` に記録）。保守的な
  決定論的分割器で、略語/小数は守るが完璧ではない。`oa:TextQuoteSelector` 等の再分割耐性は後続。
- **忠実 verbatim は後段パス専任**: morph-kgc の XML リーダ（ElementTree）は `.text` のみ＝混在内容の
  題名/キャプションは RML 側で切れる（ADR §4）。RML は構造骨格に限定。
- **融合の curve 側は demo fixture**: 実 starrydata 行はライセンスで非コミット。組成は題材論文に忠実、
  図・条件文は実グラフの実ノード。
- 1 論文・JATS のみ。任意 PDF・複数論文・hub への論文参加 Rule・propose の実 LLM dogfood は後続（ADR §8）。

## Reproduce

```bash
# 使い捨て Oxigraph（共有 :7878 を触らない）
docker run -d --rm --name asterism_doclayer_verify -p 7879:7878 \
  ghcr.io/oxigraph/oxigraph:latest serve --location /data --bind 0.0.0.0:7878

# 全ゲート（実 morph-kgc + 実 Oxigraph・production コード経路）
PYTHONPATH=ingest/src ingest/.venv/bin/python scripts/verify_document_ontology_mvp.py

# 構造グラフの再生成（決定論・バイト一致）
PYTHONPATH=ingest/src ingest/.venv/bin/python \
  datasets/papers/seed/build_paper_graph.py \
  datasets/papers/jats/PMC5951533.xml datasets/papers/seed/paper.ttl

# ユニット同等検証（morph-kgc 任意・rdflib FROM-merge）
PYTHONPATH=ingest/src ingest/.venv/bin/python -m pytest ingest/tests/test_papers_dataset.py -q
```
