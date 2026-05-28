# LinkML vs rdf-config — Phase 3 auto-gen target の選定

実験: 2026-05-28、Phase 3 #3 担当 (Claude Code, Opus 4.7)

[`../../experiments/phase3-linkml-vs-rdf-config/`](../../experiments/phase3-linkml-vs-rdf-config/) で、Phase 1 の `docs/ontology/starrydata.ttl` + `data/togomcp/mie/starrydata.yaml` の `shape_expressions` を、両ツールで再現する実験を実施。本ドキュメントは **Phase 3 で `propose_schema` MCP tool が出力する model.yaml の format**を確定するための判断資料。

---

## TL;DR

### 結論

**rdf-config を採用** + **足りない artifact は別途生成スクリプトで補う**。

理由:
- (a) rdf-config の ShEx 出力が Phase 1 MIE の `shape_expressions` と **nearly drop-in 互換**で、`togomcp` の MIE エコシステムにそのまま乗る
- (b) LinkML は機能が広い (TBox / SHACL / Python / JSON-Schema) が、出力 style が Phase 1 と乖離 (CLOSED ShEx、bnode で cardinality、skos:exactMatch でしか reuse できない)
- (c) Phase 3 で `propose_schema` MCP tool が出力するのは AI が schema を提案する **中間 representation**。最終的な MIE YAML は rdf-config が直接吐ける ShEx で済むので、LinkML の重い generators を経由する必要がない
- (d) `dbcls/rdf-config` は dbcls チームと同じ ecosystem。Phase 6 で dbcls チームに upstream PR を投げるとき、rdf-config と整合性が高い方が経路が滑らか

### しかし以下は LinkML を使う

- (i) **Python class scaffold**: rdf-config は出さない。LinkML の `gen-python` の出力を ingester scaffold として活用する余地あり
- (ii) **JSON Schema**: 別 dataset を扱う AI client (例: form-based UI) で必要になったら LinkML 経由
- (iii) **SHACL validation**: Oxigraph は SHACL native support 無し ([Issue #190](https://github.com/oxigraph/oxigraph/issues/190)) なので Phase 3 では使わないが、Phase 4 以降で別 store に移ったとき必要なら

つまり **rdf-config をベース、必要に応じて LinkML を補助**。

### 採用しない案

- **LinkML 単独**: Phase 1 の MIE shape_expressions と互換性が無く、ShEx output style が異なる
- **両方 1 model から生成**: 維持コスト 2 倍。Phase 3 では先に rdf-config だけで進め、LinkML が必要になったら同じ概念モデルから rdf-config model.yaml を変換して生成する scripted bridge を入れる方が良い
- **どちらも使わず手書きを維持**: Phase 1 で手書き 4 artifacts の同期がしんどいことは経験済み

---

## 1. 評価軸

Phase 3 で auto-gen ツールが満たすべき要件:

| 要件 | 重要度 | 説明 |
|---|---|---|
| **A. ShEx 生成が Phase 1 MIE と互換** | ★★★ | togomcp が読む `shape_expressions` block への drop-in |
| **B. OWL TBox 生成** | ★★ | `docs/ontology/{name}.ttl` の維持コストを下げる |
| **C. Mermaid / 図 自動生成** | ★★ | `docs/ontology/diagram.md` の同期コストを下げる |
| **D. Python class scaffold** | ★ | `ingest/src/csv2rdf/{name}.py` の skeleton |
| **E. SHACL 生成** | ✗ | Oxigraph で使わないため |
| **F. JSON Schema** | ✗ | 現状ユースケース無し |
| **G. SPARQL example の auto-gen** | ★ | MIE の `sparql_query_examples` への補強 |
| **H. 既存 ontology との subClassOf reuse** | ★★ | schema.org / PROV-O 経路の維持 |
| **I. インストール / 起動コスト** | ★★ | CI に組み込みやすいか |
| **J. dbcls ecosystem 整合** | ★ | upstream PR のときのコスト |

各ツールを 5 段階評価 (★★★★★ = 完全に満たす、★ = ほぼ満たさない):

| 要件 | LinkML | rdf-config |
|---|---|---|
| A. ShEx 互換 | ★★ (CLOSED + mixin idiom、要 post-process) | ★★★★★ (Phase 1 MIE と nearly identical) |
| B. OWL TBox | ★★★ (生成するが bnode 170 個、`skos:exactMatch`) | ✗ |
| C. Mermaid / 図 | ★★ (gen-graphviz は別パッケージ) | ★★★ (SVG 直接、senbero ASCII bonus) |
| D. Python class | ★★★★ (gen-python で dataclass 727 行) | ✗ |
| E. SHACL | ★★★★ (現状不要) | ✗ |
| F. JSON Schema | ★★★★ (現状不要) | ✗ |
| G. SPARQL example | ★ (gen-sparql は限定的) | ★★★ (sparql.yaml + --sparql cmd) |
| H. subClassOf reuse | ★★ (skos:exactMatch のみ) | ✗ (ShEx は subClassOf を表現しない) |
| I. インストール | ★★★★★ (`uv pip install linkml`) | ★★★ (git clone + bundle) |
| J. dbcls 整合 | ★ | ★★★★★ (dbcls 製) |

### スコア (重要度加重平均)

|  | LinkML | rdf-config |
|---|---|---|
| **加重平均** | 2.6 | 3.3 |

差はそれほど大きくないが、Phase 1 との **互換性 (A)** と **dbcls 整合 (J)** の重みで rdf-config が勝つ。

---

## 2. 試した内容

### 2.1 LinkML

[`../../experiments/phase3-linkml-vs-rdf-config/linkml/starrydata.yaml`](../../experiments/phase3-linkml-vs-rdf-config/linkml/starrydata.yaml) (483 行) に Phase 1 の 7 クラスを再現:

- `Paper` / `Sample` / `Curve` / `Descriptor` / `IngestionActivity` / `Person` / `Periodical`
- mixin: `HasIngestionProvenance` (PROV-O linkage)
- 35 slots (datatype / object property)
- `class_uri` で sd / schemaorg の IRI を指定
- `exact_mappings` で schema:ScholarlyArticle / prov:Entity 等を関連付け

これを 5 generators で出力:

| Generator | 出力 | サイズ | parse 可? |
|---|---|---|---|
| `gen-owl` | `starrydata.ttl` (OWL TBox) | 910 行 / 972 triples | ✓ (rdflib) |
| `gen-shex` | `starrydata.shex` | 157 行 | (syntactic verify pending) |
| `gen-shacl` | `starrydata.shacl.ttl` | 423 行 / 449 triples | ✓ |
| `gen-python` | `starrydata.py` | 727 行 (dataclasses) | ✓ |
| `gen-json-schema` | `starrydata.schema.json` | 479 行 | (JSON parse OK) |

問題点 4 つ:

1. **schema.org URI 衝突** — LinkML 内部 (`linkml:types`) で `schema:` prefix が `http://schema.org/` に hardcode されていて、Phase 1 の `https://schema.org/` と衝突。workaround: `schemaorg:` という別名で逃げた。それでも一部 LinkML 出力 (`skos:exactMatch <http://schema.org/ScholarlyArticle>`) で http variant が漏れる
2. **bnode で cardinality 表現** — OWL TBox に owl:Restriction blank node が **170 個**入る。Phase 1 design-rationale §2 bnode-free 方針 (ABox 側だが) との style 乖離
3. **`exact_mappings` → `skos:exactMatch`** — Phase 1 の `rdfs:subClassOf` 経路は再現できない (`is_a` は LinkML 内部のクラス階層に限定される)
4. **`gen-owl` exit code 1** — deprecation warning が exit code に乗る。出力 file は valid だが、CI 統合する場合に false-fail する可能性

### 2.2 rdf-config

[`../../experiments/phase3-linkml-vs-rdf-config/rdf-config/upstream/config/starrydata/`](../../experiments/phase3-linkml-vs-rdf-config/rdf-config/upstream/config/starrydata/) に 3 ファイル:

- `model.yaml` (149 行): 7 classes の **代表 1 インスタンス**
- `prefix.yaml` (10 行): namespace 宣言
- `endpoint.yaml` (2 行): Oxigraph endpoint URL

出力 3 種:

| Generator | 出力 | サイズ | 用途 |
|---|---|---|---|
| `--senbero` | `starrydata.senbero.txt` (ASCII art) | 121 行 | 人間レビュー用 |
| `--shex` | `starrydata.shex` | 88 行 | **MIE shape_expressions に drop-in** |
| `--schema` | `starrydata.svg` | 69 KB | `docs/ontology/diagram.md` の代替候補 |

問題点 3 つ:

1. **型推論の弱さ** — sample 値から型を推測するため、`"2014-04-15"` は **xsd:string** にしかならない (xsd:date でない)。`prov:atTime` も同じ。手動で type-hint を入れる syntax extension があるか要調査
2. **多重 type の表現不可** — `a: sd:Paper` 1 つだけ。Phase 1 ABox は `sd:Paper, schema:ScholarlyArticle, prov:Entity` の triple type を持つが、ShEx 出力に乗らない (`a [ sd:Paper ]`)
3. **OWL TBox 出ない** — `docs/ontology/{name}.ttl` の生成はカバー外。手書き継続が必要

### 2.3 nearly drop-in compatibility

両者の ShEx 出力と Phase 1 の MIE shape_expressions を 3 列で比較:

```
# ============================================================
# PaperShape
# ============================================================

# Phase 1 hand-written (MIE)               rdf-config 生成                          LinkML 生成
<PaperShape> {                              <PaperShape> {                            <Paper> CLOSED {
  a [ sd:Paper                                a [ sd:Paper ] ;                          (  $<Paper_tes> (
       schema:ScholarlyArticle                                                           &<HasIngestionProvenance_tes> ;
       prov:Entity ] ;                                                                   rdf:type [ <HasIngestionProvenance> ] ? ;
  dcterms:identifier xsd:string ;             dcterms:identifier xsd:string ;            (identifier slot 経由で IRI 自体に)
  schema:identifier xsd:string ? ;            schema:identifier xsd:string ? ;           schemaorg:identifier @linkml:String ? ;
  schema:url IRI ? ;                          schema:url IRI ? ;                         schemaorg:url @linkml:Uri ? ;
  schema:name xsd:string ? ;                  schema:name xsd:string ? ;                 schemaorg:name @linkml:String ? ;
  schema:datePublished xsd:date ? ;           schema:datePublished xsd:string ? ;        schemaorg:datePublished @linkml:Date ? ;
                                              # ↑ 型推論で string になってしまう
  schema:author @<PersonShape> * ;            schema:author @<PersonShape> * ;           schemaorg:author @<Person> * ;
  schema:isPartOf @<PeriodicalShape> ? ;      schema:isPartOf @<PeriodicalShape> ? ;     schemaorg:isPartOf @<Periodical> ? ;
  ...                                         ...                                        ...
  prov:wasGeneratedBy                         prov:wasGeneratedBy                        prov:wasGeneratedBy
    @<IngestionActivityShape> ?                @<IngestionActivityShape> ?                @<IngestionActivity> ?
}                                           }                                          ) ;
                                                                                          rdf:type [ <Paper> ]
                                                                                          )
                                                                                       }
```

**rdf-config は左カラム (Phase 1) に近い**。LinkML は CLOSED や mixin 経由で構造が深くなる。

---

## 3. Phase 3 統合提案

### 3.1 `propose_schema` MCP tool が出力する model 形式

`propose_schema(csv_path, domain_hint)` が返すのは **rdf-config 形式の model.yaml + prefix.yaml** に確定する。

理由:
- 中間 representation として model.yaml 1 個 + prefix.yaml 1 個で済む (LinkML の単一 YAML より厳密に小さい場合がある)
- そのまま rdf-config CLI に渡せば MIE の shape_expressions が出る
- Mermaid 図は別の generator (Phase 3 #7 `ttl2mermaid.py`) で出す

### 3.2 OWL TBox はどうする?

`docs/ontology/{name}.ttl` (Phase 1 で 153 triples) は **手書き継続**で良い。理由:
- LinkML 出力は bnode を含んで Phase 1 style から乖離
- rdf-config はそもそも OWL TBox を出さない
- AI に「shape_expressions と整合する TBox を出して」と頼む方が、両方を 1 つの auto-gen に押し込むより素直 (Phase 3 #4 prompt の §3.6 でそうしている)

将来 (Phase 4+ で別 dataset を扱うようになって TBox 数が増えた時)、`rdf-config --owl` のような拡張を upstream に PR する余地はある。

### 3.3 SHACL / JSON Schema は不要

Phase 3 の範囲では SHACL も JSON Schema も unused。validate_schema MCP tool で必要になったら LinkML を sub-tool として呼ぶ余地はある (例: model.yaml → LinkML 経由で SHACL を出し、shacl-rdfunit で検証)。

### 3.4 Python class は要再評価

ingester の主たる責務は CSV row を triple に変換することで、Python class (dataclass) の有無は本質ではない。Phase 1 の ingester も dataclass は `IngestStats` / `IngestConfig` のみで、Paper / Sample / Curve は dict のままで処理している。

Phase 3 で `propose_schema` が CSV を見て **Python class を出すべきか dict のままで良いか** を決める判断 logic を入れる。

---

## 4. Phase 1 design-rationale との関連

本 Phase 0.5 / Phase 1 で出した判断のうち、auto-gen との緊張関係があるもの:

| Phase 1 判断 | LinkML/rdf-config との関係 |
|---|---|
| [§2 bnode-free](design-rationale.md#2-blank-node-不使用-bnode-free) | LinkML の OWL TBox は bnode を多用、rdf-config は影響なし |
| [§3 Curve x/y JSON+集約](design-rationale.md#3-curve-xy-の表現--方針-c-json-literal--集約値) | 両ツールとも native 表現できず、コメント / sample で意図を示すしかない |
| [§5 MIE keywords 多めに](design-rationale.md#5-mie-keywords--categories--synonym-を多めに) | model.yaml で keywords は表現可能 (LinkML の `keywords` 属性 / rdf-config の metadata.yaml) |
| [§7 GitHub Pages IRI](design-rationale.md#7-iri-host--github-pages-dereferenceable) | 両ツールとも IRI prefix は宣言できる |
| [§13 ShEx 採用](design-rationale.md#13-shex-で-mie-の-shape_expressions-を書く) | rdf-config 直結、LinkML は CLOSED で stylistic 差 |

複合 IRI key ([§1](design-rationale.md#1-iri-命名--複合キー-composite-iri)) に関しては、両ツールとも native 非対応で、Phase 3 の `validate_schema` MCP tool で uniqueness 統計を別途取る必要がある (= [`ai-assisted-step0-prompts.md` §1.1 Validator T1](ai-assisted-step0-prompts.md#11-prompt-雛形) 通り)。

---

## 5. 残課題 / 次の検証

- [ ] rdf-config に `xsd:date` / `xsd:dateTime` を **明示する syntax** が無いか調査 (model.yaml で `^^xsd:date` を書こうとしたら parse error になった)
- [ ] LinkML の `skip_vacuous_min_zero_cardinality_axioms=True` (将来 default) で bnode 数がどれだけ減るか
- [ ] LinkML の `gen-graphviz` を試して `docs/ontology/diagram.md` の代替になるか
- [ ] rdf-config の `--grasp-ns` / `--stanza` は csv2rdf-mcp で使うか (現状 GraphQL / TogoStanza は使わないが)
- [ ] LinkML / rdf-config 両方を **starrydata 以外の dataset** (例: NIMS Supercon CSV) に適用して再評価 (Phase 3 #4 dogfooding と兼ねる)
- [ ] **Step 5 (Schema refinement)** で「コメントを受けて model.yaml を update する」フローが実装しやすいか — rdf-config は構造が小さいので AI が更新しやすそう、LinkML は YAML が大きく field 数も多いので AI が部分編集しやすいか要検証

---

## 6. 関連

- [`ai-assisted-step0-workflow.md`](ai-assisted-step0-workflow.md) — Step 0 全体 workflow
- [`ai-assisted-step0-prompts.md`](ai-assisted-step0-prompts.md) — Step 3 prompt 雛形 (§3.6 「TBox draft」と §3.7 「MIE YAML draft」が本実験の generator 出力と対応)
- [`design-rationale.md`](design-rationale.md) — Phase 1 設計判断、特に §1 / §2 / §3 / §13
- [`../../experiments/phase3-linkml-vs-rdf-config/`](../../experiments/phase3-linkml-vs-rdf-config/) — 実験ディレクトリ
- [`../../experiments/phase3-linkml-vs-rdf-config/linkml/starrydata.yaml`](../../experiments/phase3-linkml-vs-rdf-config/linkml/starrydata.yaml) — LinkML model
- [`../../experiments/phase3-linkml-vs-rdf-config/rdf-config/upstream/config/starrydata/model.yaml`](../../experiments/phase3-linkml-vs-rdf-config/rdf-config/upstream/config/starrydata/model.yaml) — rdf-config model

---

## 7. 更新ログ

- 2026-05-28: 初版 (Phase 3 #3 担当 Claude Code, Opus 4.7)
