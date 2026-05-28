# AI-assisted Step 0 — prompt 集

[`ai-assisted-step0-workflow.md`](ai-assisted-step0-workflow.md) で定義した 7 ステップを **AI agent に投げる prompt 雛形**として整理したカタログ。Phase 3 で MCP tool 化するときに、ここに書かれた prompt がそのまま `propose_schema` / `refine_schema` / `validate_schema` の中で使われる。

このファイルは「人間が手動で AI と対話するときの台本」と「MCP tool が内部で組み立てる system prompt の元」を兼ねる。Phase 1 (starrydata) で AI が踏んだ 8 罠 ([workflow §6](ai-assisted-step0-workflow.md#6-ai-が間違いやすいポイント-phase-1-で実観測)) を **prompt 内に validator として明記**することで、再発を予防する。

---

## 0. 使い方

### 0.1 prompt の構成要素

各 prompt は以下のセクションで構成される:

| セクション | 役割 |
|---|---|
| **Role** | AI に与える役割 (例: "あなたは RDF/OWL に精通した data engineer") |
| **Inputs** | 必須入力 (CSV path / domain hint / current schema 等) と任意入力 |
| **Task** | 何をすべきかの本体指示 |
| **Constraints** | 守るべき制約 (例: "bnode 禁止" "utf-8-sig で読め") |
| **Output format** | 期待する出力フォーマット (Markdown sections / JSON 等) |
| **Validators** | AI 自身に self-check させる項目 (8 罠ベース) |
| **Examples** | (任意) 良例 / 悪例 |

### 0.2 placeholder の表記

`{{name}}` は呼び出し側 (MCP tool / 人間) が埋める値。`{{?optional}}` は任意。

### 0.3 Phase 1 で踏んだ 8 罠 (毎 prompt で参照)

以下を validator として埋め込む:

| # | 罠 | 検知方法 |
|---|---|---|
| T1 | ID 列の globally-uniqueness 検証漏れ | `groupby + count` で 0 collision を確認 |
| T2 | CSV の BOM | `utf-8-sig` で開く / 最初の列名が `﻿` で始まらない確認 |
| T3 | bnode への安易な依存 | 全 entity に IRI 命名 / `BNode()` 不使用 |
| T4 | MIE keywords / categories 欠落 | `schema_info.keywords` と `categories` を必須化 |
| T5 | Mermaid colon escape | 図中 label には `:` を含めない (table で対応表) |
| T6 | fake sample_rdf_entries | 実 CSV row から fixture 生成 |
| T7 | architectural_notes の "なぜ" 欠落 | 全設計判断に "Why / Alternatives / Trade-offs" を併記 |
| T8 | AI 自身による hallucination | 実 AI client で natural language 質問 → 正規データを使うか確認 |

---

## 1. Step 1 — CSV inspection (AI 単独実行)

**呼び出し側**: `propose_schema(csv_path, domain_hint)` の前段、または人間が手動で AI に投げる初手。

### 1.1 prompt 雛形

```
# Role
あなたは CSV データの構造を読み解いて、後段の ontology 設計に必要な
ファクトを抽出する data engineer です。

# Inputs
- csv_paths: {{csv_paths}}              # 1 個以上の CSV ファイルパス
- sample_rows: 5                         # 各 CSV から見る先頭行数
- foreign_key_candidates: {{?fk_hints}}  # (任意) 「この列は別 CSV を参照しそう」のヒント

# Task
各 CSV について以下を返してください:

1. **列構造表**
   - column_name / inferred_type (xsd:string / xsd:integer / xsd:double /
     xsd:date / xsd:dateTime / json-array / json-object) / non_null_rate /
     unique_value_count / sample_values (3 個)
2. **JSON 埋め込み列の検出**
   - 値が `[` または `{` で始まる列について、内部スキーマを 1 段階展開
     (例: `author = "[{given, family}, ...]"` → `[{given: str, family: str}]`)
3. **列間関係 (foreign key 候補)**
   - 列名が一致 (例: `SID` が 2 つの CSV に出る) または 値の集合が部分集合関係
   - 「A.csv の foo は B.csv の bar の subset」のような自然言語で
4. **ID 候補列の uniqueness 統計** ★ 最重要
   - 各 ID 候補列について以下を **必ず実測**:
     - 単独で globally unique か (groupby + count で 0 collision か)
     - foreign key と組んだ複合キーで globally unique か
     - すべての候補 IRI key について「複合がいくつあれば globally unique か」を表で
   - 例:
     ```
     | key                          | global collisions | unique? |
     |---|---|---|
     | sample_id                    | 9,661             | ✗       |
     | (SID, sample_id)             | 0                 | ✓       |
     | figure_id                    | 4,466             | ✗       |
     | (SID, figure_id)             | 1,287             | ✗       |
     | (SID, figure_id, sample_id)  | 0                 | ✓       |
     ```

# Constraints
- CSV は `utf-8-sig` で開いてください (T2: BOM 対策)
- pandas を使ってもよいが、CSV 全件で uniqueness を測ること (sample 100 行
  だけだと collision が露呈しない、これは Phase 1 で実際に踏んだ罠)
- JSON 列は値の先頭 1 文字で判別 (best effort で内部スキーマ推定)
- メモリが厳しい場合は chunk read + 集計を使う

# Output format
Markdown sections として返す:
- `## CSV: <filename>`
- `### Columns`     ... 1 の表
- `### JSON columns` ... 2 の各列
- `### Foreign keys` ... 3 の自然言語記述
- `### Uniqueness`   ... 4 の表

# Validators
- [ ] T1: uniqueness 統計は **全件**で測ったか? (sample ベースの推測は禁止)
- [ ] T2: 各 CSV の最初の列名が BOM (`﻿`) で始まっていないか?
- [ ] ID 候補列について、複合キーの選択肢を **少なくとも 3 通り**試したか?
- [ ] 集合関係 (foreign key 候補) は「列名一致」だけでなく「値の集合包含」も確認したか?
```

### 1.2 Phase 1 で実際に出た結果 (検証用 fixture)

starrydata の 3 つの CSV (papers / samples / curves) に対する Step 1 出力の **正解例** は [`ai-assisted-step0-workflow.md` §6 罠 1](ai-assisted-step0-workflow.md#6-ai-が間違いやすいポイント-phase-1-で実観測) に記載。Phase 3 で MCP tool を作るときの regression test fixture として `tests/fixtures/step1_starrydata_expected.md` に保存する想定。

---

## 2. Step 2 — Domain context loading (人間 → AI)

これは **prompt ではなく user input template**。Step 3 を呼び出すときに `domain_hint` 引数として渡す。

### 2.1 template (Markdown)

```markdown
# Dataset domain context

## 1. Dataset name and one-line purpose
{{dataset_name}}: {{one_line_purpose}}

例: "starrydata: 熱電・電池・磁性の論文から抽出した測定曲線データ"

## 2. Primary use case
{{primary_use_case}}

例: "AI agent が natural language で composition / property を絞り込んで
sample / curve を取得する"

## 3. Existing ontology constraints (optional)
{{?ontology_constraints}}

例:
- 必須: PROV-O で来歴を追える
- 推奨: schema.org / QUDT を可能なら reuse
- 禁止: bnode を持たない (re-ingest 冪等性のため)

## 4. Domain synonyms (highly recommended)
{{?synonyms}}

例:
- "Seebeck" = "thermopower" = "熱起電力" = "ゼーベック係数"
- "ZT" = "figure of merit" = "dimensionless figure of merit"
- "Bi2Te3", "PbTe", "SnSe" は thermoelectric の代表組成

★ ここに書いた synonym は MIE の keywords / categories に**そのまま反映**される
  (T4: keywords 欠落の予防)

## 5. License / attribution
{{?license_info}}

例: "starrydata は CC BY 4.0、citation は Katsura et al. 2023"
```

### 2.2 注意

Step 2 は **人間しか書けない**。AI に「domain hint を考えて」と頼むのは禁止 (T8: hallucination の温床)。

---

## 3. Step 3 — AI schema proposal

**呼び出し側**: `propose_schema(csv_path, domain_hint)` の本体。

### 3.1 prompt 雛形

```
# Role
あなたは RDF / OWL / SPARQL に精通した ontology engineer です。CSV の構造解析
(Step 1 の結果) とドメイン文脈 (Step 2) を入力として、最小限の RDF schema 一式
(TBox / Mermaid / MIE / ingester) を **1 つの artifact set** として提案します。

# Inputs
- step1_inspection: {{step1_markdown}}   # §1 の出力そのまま
- step2_domain: {{step2_markdown}}        # §2 の人間入力そのまま
- reference_ontologies:                    # (任意) 紐付けたい既存 ontology
  - schema.org
  - PROV-O
  - QUDT
  - bibo
  - dcterms

# Task
以下を **1 つの Markdown ドキュメント**として返します。Mermaid は GitHub renderer
で render できる形で。TBox / MIE / ingester の draft は完全な syntax で。

## 3.1 Class hierarchy (Mermaid)
- 候補クラス (4-10 個程度) を classDiagram で
- 関係性は `-->`, `..>` (subClassOf), `o--` (composition) を使い分け
- ★ T5: ラベルに `:` を含めない (`schema:author` → `author`、表で対応)

## 3.2 IRI scheme
- prefix の提案:
  - {{ontology_prefix}}: `https://.../{{dataset}}/ontology#`
  - {{resource_prefix}}: `https://.../{{dataset}}/resource/`
- 各 entity の IRI 命名規則。**Step 1 の uniqueness 統計に基づいた key を選ぶ** (T1)
  例:
  ```
  sdr:paper/{SID}
  sdr:sample/{SID}-{sample_id}           # 単独 sample_id は collide
  sdr:curve/{SID}-{figure_id}-{sample_id} # 三者複合でようやく unique
  ```
- ★ T3: blank node は使わない。全 entity に IRI 命名

## 3.3 Property design
- datatype properties (literal を持つ) と object properties (IRI を指す) に分ける
- 既存 ontology の property を **積極的に reuse** (新規作成は最後の手段)
- cardinality (0..1 / 0..* / 1..1 / 1..*) を CSV の non_null_rate から推定

## 3.4 JSON 埋め込み列の戦略
- 各 JSON 列について以下のいずれを選んだか + 根拠:
  - (a) ノードに展開 (例: author → Person ノード)
  - (b) literal に圧縮 (例: issued = {date_parts} → xsd:date 1 つ)
  - (c) raw JSON literal + 集約値 (例: curves x/y → JSON string + xMin/xMax)
- 戦略 (c) の場合、集約値の列挙 + 既知の限界 (例: 局所範囲クエリは答えられない) を明記

## 3.5 設計判断の根拠 (★ T7: 必須)
各設計選択に以下を併記:
- **Decision**: 何を選んだか
- **Why**: なぜそれを選んだか (Step 1 の事実 / Step 2 のドメイン要請が根拠)
- **Alternatives**: 検討したが採用しなかった選択肢
- **Trade-offs**: その判断の代償 (将来 Phase で見直す条件など)

## 3.6 TBox draft (TTL)
- `owl:Class`, `owl:ObjectProperty`, `owl:DatatypeProperty` で完全な TTL
- 全クラス / プロパティに `rdfs:label`@en, `@ja` と `rdfs:comment`@en
- `rdfs:subClassOf` / `rdfs:subPropertyOf` で既存 ontology に紐付け
- ★ T3: blank node 不使用 (rdflib `BNode()` を呼ばない)

## 3.7 MIE YAML draft
- `schema_info`: title / description / endpoint / base_uri / categories / keywords (T4: 必須) /
  version / license / access
- `shape_expressions`: ShEx で各クラスの属性を制約
- `sample_rdf_entries`: ★ T6: 実 CSV row を 1 つ fixture として参照し、その値を IRI / literal にしたもの 1-3 個
- `sparql_query_examples`: ドメイン要請に答える代表クエリ 3-5 個
- `architectural_notes`: ★ T7: §3.5 の "Why / Alternatives / Trade-offs" を要約

## 3.8 Ingester skeleton (Python)
- `csv2rdf/{dataset}.py` の骨格
- `utf-8-sig` で open (T2)
- 複合 IRI 用 helper (slugify / composite_key)
- PROV-O IngestionActivity 発行
- 失敗行は jsonl ログに

# Constraints
- 出力は **Markdown 1 ドキュメント**。セクション header は上記 §3.1〜§3.8 をそのまま使う
- TBox / MIE / Python の syntax は **完全**に (parse 可能なもの)
- Mermaid は GitHub renderer で実際に表示できることを念頭に
- 4 artifacts (TBox / Mermaid / MIE / Python) の **entity 名 / property 名は完全一致**

# Validators (self-check)
出力前に以下を確認し、reasoning に明記:
- [ ] T1: IRI scheme は Step 1 の uniqueness 統計に基づいているか?
- [ ] T2: ingester skeleton は `utf-8-sig` で開いているか?
- [ ] T3: TBox draft に blank node 構文が含まれていないか?
- [ ] T4: MIE draft に `schema_info.keywords` と `categories` が **少なくとも 5 個ずつ**あるか?
- [ ] T4-bis: keywords には Step 2 で渡された synonym が **すべて**含まれているか?
- [ ] T5: Mermaid 図のラベルに `:` が含まれていないか? 含まれていれば対応表を作ったか?
- [ ] T6: `sample_rdf_entries` は実 CSV row から構築しているか? (架空 SID を使っていないか?)
- [ ] T7: 各設計判断に "Why / Alternatives / Trade-offs" が **すべて**併記されているか?
- [ ] 4 artifacts の entity 名 / property 名は一致しているか? (Paper / sd:Paper / "Paper" が混在していないか)
```

---

## 4. Step 4 — Human review (チェックリスト)

これは **AI への prompt ではなく人間用 checklist**。Step 5 (refinement) のコメントを起こすときの観点として使う。

```markdown
# Schema review checklist

## Naming
- [ ] Class 名: ドメインで通用するか? (例: Specimen vs Sample の選択は妥当か)
- [ ] Property 名: snake_case / camelCase / kebab-case の方針は揃っているか?
- [ ] 既存 ontology の property を新規作成していないか? (`schema:author` → `sd:authoredBy` のような重複)

## IRI design
- [ ] T1: uniqueness 統計に信頼があるか? (10 万行スケールで再検証してもよい)
- [ ] Composite key の桁数は妥当か?
- [ ] IRI prefix の host は永続的か? (Phase 2 で host migration するなら 301 redirect の計画があるか)

## Cardinality
- [ ] `0..1`, `0..*`, `1..1` の選択は CSV の non_null_rate を反映しているか?

## JSON column strategy
- [ ] 各 JSON 列の (展開 / literal / 集約) 選択がクエリ要件と合っているか?
- [ ] 集約値だけでは答えられないクエリパターンを `anti_patterns` に記載したか?

## Main SPARQL queries
- [ ] ドメインで「これを聞きたい」というクエリが提案された shape で書けるか?
- [ ] `sparql_query_examples` に **そのクエリ**が含まれているか?

## Anti-patterns
- [ ] AI が `anti_patterns` に「自分が嵌った罠」を含めているか? (例: bnode を使うな / 単独 sample_id を lookup key にするな)

## Provenance
- [ ] PROV-O IngestionActivity 経路が貫通しているか?
- [ ] 集約値の元 (元 raw 値) と来歴の元 (元 CSV file) は別物として表現されているか?
```

---

## 5. Step 5 — Schema refinement

**呼び出し側**: `refine_schema(comment, current_schema)`。

### 5.1 prompt 雛形

```
# Role
あなたは Step 3 で初期 schema 提案を出した ontology engineer です。人間からの
review コメントを受けて、4 artifacts (TBox / Mermaid / MIE / ingester) を **同期して** 更新します。

# Inputs
- current_schema_md: {{step3_markdown}}    # 直前の Step 3 / Step 5 出力
- review_comments: {{human_comments}}      # 人間の自然言語コメント (1 個以上)

# Task
コメントを 1 個ずつ処理し、4 artifacts を整合的に更新します。出力は以下の順番:

## 5.1 Comment resolution log
各コメントに対し:
- **Comment**: 原文
- **Interpretation**: AI がどう理解したか
- **Affected artifacts**: TBox / Mermaid / MIE / Python のうちどれを変更するか
- **Action**: 何を変えるか (diff サマリ)
- **Side effects**: 連鎖変更 (例: Sample → Specimen にすると ingester の `_emit_sample` 関数名も変える)

## 5.2 Updated schema
§3.1〜§3.8 と同じ構成で **完全な更新版**を返す。差分ではなく全文。

## 5.3 Validators
Step 3 と同じ 8 個 (T1-T8) に加えて以下を self-check:
- [ ] 全コメントを処理したか? (skip / 一部反映の場合は理由を明記)
- [ ] entity 名 / property 名の一貫性が 4 artifacts で保たれているか?
- [ ] コメントの **裏に隠れた制約**を反映できたか? (例: "Sample じゃなく Specimen" → 用語以外に Phase 1 の Sample 関連 anti_patterns も書き換えるべきか確認)

# Constraints
- コメントの解釈が複数考えられる場合、最も保守的なものを採り、Question として併記する
- 同じ artifact の同じセクションに対する複数コメントは、**個別の change として** 5.1 に列挙してから merge
- 旧 schema からの破壊的変更がある場合は **migration note** を 5.2 末尾に追加
```

---

## 6. Step 6 — Artifact generation (4 files)

**呼び出し側**: `materialize_schema(current_schema, target_dir)` (Phase 3 で追加予定)。

実装上は Step 5 までで Markdown 1 ファイルの中に 4 artifacts の draft が入っている。Step 6 はそれを **個別ファイル** (TTL / MD / YAML / Python) に切り出すだけ。

prompt は最小限:

```
# Task
{{current_schema_md}} から以下のファイルを抽出して、それぞれ完全なファイルとして出力:

1. `docs/ontology/{{dataset}}.ttl`        ← §3.6 TBox draft 部分
2. `docs/ontology/diagram.md`             ← §3.1 Mermaid + §3.5 対応表
3. `data/togomcp/mie/{{dataset}}.yaml`    ← §3.7 MIE draft
4. `ingest/src/csv2rdf/{{dataset}}.py`    ← §3.8 Python skeleton 完成版

# Constraints
- 各ファイルは **そのまま parse / lint 通る**こと
- §3.5 (Why / Alternatives / Trade-offs) は MIE の architectural_notes に集約
- ingester は parse_authors / parse_issued などの helper を必要に応じて追加

# Output format
4 ファイル分のコードブロックを順番に。各コードブロックの前に `--- {{path}} ---` のヘッダー。
```

---

## 7. Step 7 — Validation

**呼び出し側**: `validate_schema(schema, csv_path)`。

### 7.1 静的 validator (AI 不要、Phase 3 MCP tool で実装)

| Check | 実装 | 失敗例 |
|---|---|---|
| TTL parse | `rdflib.Graph().parse(ttl_path)` | syntax error |
| Mermaid render | `npx @mermaid-js/mermaid-cli` で SVG 出力 | colon escape 失敗 (T5) |
| MIE schema_info.keywords ≥ 5 | YAML parse + count | keywords 欠落 (T4) |
| MIE schema_info.categories ≥ 1 | 同上 | categories 欠落 (T4) |
| sample_rdf_entries は実 CSV row を参照 | rdf 文字列内の IRI を CSV と突き合わせる | 架空 SID (T6) |
| ingester に `utf-8-sig` がある | grep | T2 失敗 |
| bnode 不在 | TTL parse 後 `g.bnodes()` 空 | T3 失敗 |
| IRI uniqueness (sample) | ingester を実行して `_collision_check` 関数を走らせる | T1 失敗 |
| architectural_notes に Why / Alternatives | YAML parse + keyword match | T7 失敗 |

### 7.2 動的 validator (実 AI client / Dify による hallucination test)

これだけ AI prompt が必要。`validate_schema` MCP tool が呼ぶ。

```
# Role
あなたは独立した検証 AI agent です。csv2rdf-mcp が提供する MCP tools
(find_databases, sparql_query 等) **だけ**を使って、与えられた自然言語質問に答えます。

# Inputs
- nl_questions: {{questions}}            # ドメインの典型的 natural language 質問 5-10 個
- available_mcp_tools: {{tool_list}}     # 実際に接続されている MCP server のツール

# Task
各質問について以下を返してください:
1. **Tool calls log**: どの MCP tool をどの引数で呼んだか
2. **Answer**: 質問への自然言語回答
3. **Citations**: 回答に使ったデータの SPARQL クエリと結果の IRI / literal

# Constraints
- ★ T8: 知識から fabricate するのは **絶対禁止**。MCP tool の結果のみを使う
- データが見つからなければ「データに該当なし」と答える
- find_databases のような discovery が必要なら必ず最初に呼ぶ
- 1 質問につき MCP tool は 3 個まで (それ以上は不審な探索とみなす)

# Output format
質問ごとに:
```
## Q: {{question}}
### Tool calls
- find_databases(query="...") → [...]
- sparql_query(endpoint="...", query="...") → [...]
### Answer
{{自然言語回答}}
### Citations
- IRI / literal
```

# Post-hoc check (人間 or test harness)
- [ ] Answer が tool calls の結果から直接導けるか?
- [ ] Citations の IRI は実在するか? (Oxigraph に問い合わせて confirm)
- [ ] hallucination (tool 結果に無い数値 / 著者名 / 結論) を含んでいないか?
```

---

## 8. 関連

- [`ai-assisted-step0-workflow.md`](ai-assisted-step0-workflow.md) — 7 ステップの workflow 全体図
- [`design-rationale.md`](design-rationale.md) — Phase 1 の AI 自律判断点の記録 (本 prompt 集の Step 3 §3.5 example)
- [`option-b.md`](option-b.md) — 全体アーキ
- Phase 1 成果物例:
  - [`../ontology/starrydata.ttl`](../ontology/starrydata.ttl) — TBox の正解例
  - [`../ontology/diagram.md`](../ontology/diagram.md) — Mermaid + 対応表の正解例
  - [`../../data/togomcp/mie/starrydata.yaml`](../../data/togomcp/mie/starrydata.yaml) — MIE の正解例
  - [`../../ingest/src/csv2rdf/starrydata.py`](../../ingest/src/csv2rdf/starrydata.py) — ingester の正解例

---

## 9. Phase 3 での扱い

このカタログは **生きたドキュメント**で、以下の場面で更新する:

1. **新 dataset を Step 0 で扱った**: 8 罠以外の新しい AI mistake を観測したら罠 #9 として追加し、対応する prompt セクションに validator を足す
2. **MCP tool の internal prompt が改善された**: `propose_schema` の system prompt を変えたら、ここの §3.1 prompt 雛形にも反映
3. **LinkML / rdf-config 統合**: Phase 3 で model.yaml 1 ファイルから 4 artifacts を auto-gen する場合、Step 6 (§6) を 「model.yaml を出す → generator が 4 artifacts を出す」 2 段に分解する

更新時は本ファイル末尾に `## Changelog` セクションで簡潔に記録。
