# Phase 3 #3 — LinkML vs rdf-config 実験

Phase 3 で **model.yaml 1 ファイルから TBox / Mermaid / ShEx / Python class / SHACL / JSON-Schema を auto-gen** するための schema-modeling 言語の比較実験。

両方のツールに対し、Phase 1 [`docs/ontology/starrydata.ttl`](../../docs/ontology/starrydata.ttl) と [`data/togomcp/mie/starrydata.yaml`](../../data/togomcp/mie/starrydata.yaml) の `shape_expressions` を **再現** することを target に置く。

最終結論は [`../../docs/architecture/linkml-vs-rdf-config.md`](../../docs/architecture/linkml-vs-rdf-config.md) に。

---

## 実験環境

| 項目 | 値 |
|---|---|
| Date | 2026-05-28 |
| LinkML version | 1.11.1 (pip install via uv, isolated venv) |
| rdf-config version | upstream main (2026-05-28 時点の `dbcls/rdf-config` HEAD) |
| Python | 3.11.6 |
| Ruby | 2.6.10 (system default on macOS Darwin 25.3.0) |

---

## ディレクトリ構成

```
.
├── README.md                              ← 本ファイル
├── linkml/
│   ├── .venv/                             ← uv venv (linkml install)
│   ├── starrydata.yaml                    ← LinkML model (483 lines)
│   └── generated/
│       ├── starrydata.ttl                 ← OWL TBox (910 lines / 972 triples)
│       ├── starrydata.shex                ← ShEx (157 lines)
│       ├── starrydata.shacl.ttl           ← SHACL (423 lines / 449 triples)
│       ├── starrydata.py                  ← Python dataclasses (727 lines)
│       └── starrydata.schema.json         ← JSON Schema (479 lines)
└── rdf-config/
    ├── config/starrydata/                 ← 安定保存 (committed)
    │   ├── model.yaml                     ← rdf-config model (149 lines)
    │   ├── prefix.yaml                    ← 10 lines
    │   └── endpoint.yaml                  ← 2 lines
    ├── upstream/                          ← git clone した dbcls/rdf-config (gitignored)
    └── generated/
        ├── starrydata.senbero.txt         ← ASCII art schema (121 lines)
        ├── starrydata.shex                ← ShEx (88 lines)
        └── starrydata.svg                 ← schema diagram SVG (69 KB)
```

---

## 再現手順

### LinkML

```bash
cd linkml
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python linkml
mkdir -p generated
.venv/bin/gen-owl starrydata.yaml > generated/starrydata.ttl
.venv/bin/gen-shex starrydata.yaml > generated/starrydata.shex
.venv/bin/gen-shacl starrydata.yaml > generated/starrydata.shacl.ttl
.venv/bin/gen-python starrydata.yaml > generated/starrydata.py
.venv/bin/gen-json-schema starrydata.yaml > generated/starrydata.schema.json
```

### rdf-config

```bash
cd rdf-config

# 1) clone upstream (gitignored)
git clone --depth 1 https://github.com/dbcls/rdf-config.git upstream

# 2) install gems locally (avoids sudo)
cd upstream
mkdir -p .bundle && printf -- "---\nBUNDLE_PATH: \".bundle\"\n" > .bundle/config
bundle install   # Ruby 2.6.10 で動く

# 3) link our committed config into upstream/config/
ln -sf "$(pwd)/../config/starrydata" config/starrydata
# (or cp -r ../config/starrydata config/starrydata)

# 4) generate
mkdir -p ../generated
bundle exec rdf-config --config config/starrydata --senbero > ../generated/starrydata.senbero.txt
bundle exec rdf-config --config config/starrydata --shex    > ../generated/starrydata.shex
bundle exec rdf-config --config config/starrydata --schema  > ../generated/starrydata.svg
```

---

## 観察された違い (要約)

詳細レポートは [`../../docs/architecture/linkml-vs-rdf-config.md`](../../docs/architecture/linkml-vs-rdf-config.md)。

| 項目 | LinkML | rdf-config |
|---|---|---|
| model.yaml 構文 | type-driven (classes + slots 宣言) | example-driven (代表 1 インスタンスを書く) |
| model.yaml 行数 | 483 | 149 + 10 + 2 = 161 |
| OWL TBox 生成 | ✓ (972 triples, **bnode 170 個**) | ✗ |
| ShEx 生成 | ✓ (157 行、`CLOSED` + mixin idiom) | ✓ (88 行、**Phase 1 MIE と nearly drop-in**) |
| SHACL 生成 | ✓ (449 triples) | ✗ |
| Python class | ✓ (727 行 dataclasses) | ✗ |
| JSON Schema | ✓ (479 行) | ✗ |
| SVG schema 図 | ✗ (要 gen-graphviz) | ✓ (69 KB) |
| ASCII schema 図 | ✗ | ✓ (senbero, 121 行) |
| 型推論 | 明示宣言 (`range: date` → xsd:date) | sample-driven (`"2014-04-15"` → **xsd:string にしか落ちない**) |
| 既存 ontology の reuse | `exact_mappings` → `skos:exactMatch` (rdfs:subClassOf にならない) | ShEx 出力に乗らない (rdf-config の範疇外) |
| 複合 IRI key | native 非対応 (identifier slot + comment で workaround) | native 非対応 (literal 値で書くだけ) |
| インストール | `uv pip install linkml` | `git clone + bundle install` (gem として公開されていない) |
| Ruby 互換 | n/a | 2.6 で動く (gemspec は ≥ 2.4) |

### key finding 1: rdf-config の ShEx は **Phase 1 MIE と nearly drop-in**

```diff
- # Phase 1 hand-written MIE shape_expressions
+ # rdf-config 生成 ShEx
  <PaperShape> {
-   a [ sd:Paper schema:ScholarlyArticle prov:Entity ] ;
+   a [ sd:Paper ] ;
    dcterms:identifier xsd:string ;
    schema:identifier xsd:string ? ;            # DOI
    schema:url IRI ? ;
    schema:name xsd:string ? ;                  # title
-   schema:datePublished xsd:date ? ;
+   schema:datePublished xsd:string ? ;          # ← rdf-config は sample から xsd:string と推論 (型ヒント未指定)
    schema:author @<PersonShape> * ;
    schema:isPartOf @<PeriodicalShape> ? ;
    ...
  }
```

唯一の不一致は (i) 多重 type の `a [ ... ]` (rdf-config は 1 つしか書けない)、(ii) 型推論 (xsd:date / xsd:dateTime にならない) の 2 点。両方とも post-process script で fix 可能。

### key finding 2: LinkML の OWL TBox は **bnode を 170 個含む**

cardinality restriction を `[ a owl:Restriction ; owl:onProperty ... ; owl:maxCardinality 1 ]` で書くため、TBox に **blank node が 170 個**入る。Phase 1 の bnode-free 方針 ([design-rationale §2](../../docs/architecture/design-rationale.md#2-blank-node-不使用-bnode-free)) との対比が大きい。

ABox (実データ) は影響を受けない (LinkML は ABox を出さない) が、TBox の見た目は別物になる。

### key finding 3: LinkML の `exact_mappings` は `skos:exactMatch` を emit、`rdfs:subClassOf` ではない

```turtle
# LinkML 出力
sd:Paper a owl:Class ;
    rdfs:label "Paper" ;
    skos:exactMatch <http://schema.org/ScholarlyArticle>,
                    prov:Entity ;
    rdfs:subClassOf [ a owl:Restriction ; owl:onProperty sd:SID ; owl:minCardinality 1 ] ,
                    ...
```

Phase 1 は:

```turtle
# Phase 1 hand-written
sd:Paper a owl:Class ;
    rdfs:label "Paper"@en, "論文"@ja ;
    rdfs:subClassOf schema:ScholarlyArticle , prov:Entity .
```

`skos:exactMatch` は **OWL reasoner では推論されない** (SKOS は subClassOf の semantic を持たない)。Phase 1 の方が ontology reuse として強い。

LinkML で `rdfs:subClassOf` 相当を出すには `is_a` を使うが、それは **LinkML 内部のクラス階層**を表現するもので、外部 ontology へのリンクには不向き (mixin として使えなくはないが冗長)。

### key finding 4: 両者とも **複合 IRI key を native でモデルできない**

Phase 1 design-rationale §1 の核心 (`sdr:sample/{SID}-{sample_id}`) は、両ツールとも:
- LinkML: identifier slot + comment で人間にだけ伝わる
- rdf-config: 例値で `"6-113"` を書くだけ。slot 名 (`sample_composite_key`) で意図を示すしかない

どちらも **uniqueness validator は外で書く**必要がある。これは Phase 3 の `validate_schema` MCP tool の implementation 機会。

### key finding 5: schema.org URI の HTTP/HTTPS 衝突

LinkML の `linkml:types` 内部で `schema:` prefix が `http://schema.org/` に hardcode されている。Phase 1 は `https://schema.org/` (modern) を使うため、`schemaorg:` という別名 prefix にする workaround を入れた。それでも LinkML 出力の一部 (`skos:exactMatch <http://schema.org/ScholarlyArticle>`) で http variant が漏れる。

rdf-config はこの問題がなく、`prefix.yaml` で `schema: <https://schema.org/>` と書けばそのまま使われる。

---

## 結論 (preliminary)

Phase 3 の auto-gen target としては **rdf-config (ShEx だけ) + 手書き別ファイル** が現実的、と暫定結論。**LinkML を完全に置き換えに使うと既存 4 artifacts (TBox / Mermaid / MIE / Python) と style が乖離**する。

詳細は [`../../docs/architecture/linkml-vs-rdf-config.md`](../../docs/architecture/linkml-vs-rdf-config.md) に。

## TODO

- [ ] LinkML の OWL TBox を post-process で bnode 除去 (`skip_vacuous_min_zero_cardinality_axioms` 等の option を試す)
- [ ] rdf-config の ShEx を `xsd:date` / `xsd:dateTime` で type 指定する syntax extension が無いか調査
- [ ] LinkML の Python class が ingester に組み込めるか試す (Phase 1 `_emit_paper` の代替)
- [ ] `propose_schema` MCP tool が出す model.yaml の format を rdf-config / LinkML どちらにするか確定
