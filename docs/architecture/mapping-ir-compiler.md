# ADR: Mapping IR — propose の段階分割と RML の決定論コンパイル（E 案）

Status: Accepted (2026-07-06)
Related: [step0-rml-emission.md](step0-rml-emission.md),
[propose-self-correction-loop.md](propose-self-correction-loop.md),
[phase5-declarative-substrate.md](phase5-declarative-substrate.md),
[ingestion-execution-safety.md](ingestion-execution-safety.md)

## 1. 背景

propose は 1 回の LLM 呼び出しで 9 セクションの設計 Markdown を出し、§9 に**生の RML Turtle**
を書かせている。RML+FnO の構文知識（`rmlf:` 名前空間・FnO パラメータ IRI・functionExecution の
入れ子・`rmlf:constant`・termType/datatype・テンプレート内は CURIE 展開されない等）は
SYSTEM_PROMPT の HARD RULES ~90 行として LLM に「暗記」させている。

2026-07-06 の本番ドッグフード（Qwen3.6-35B-A3B・レジストリ `dataset-548d5ca3`）で、この契約の
限界が確定した:

- 生成 RML は**構文の発明**だらけ: `rr:template "sdr:paper/{fn:doi_norm(DOI)}/…"`（テンプレート内
  関数呼び出し）×5、`rml:transform fn:datetime_iso`（存在しない述語）×7、`rr:predicate a`
  （Turtle 構文違反）×4。正しい `rmlf:functionExecution` 形は **0 回**（HARD RULES に明示済み
  にもかかわらず）。
- T9 はパースエラーで即 fail し、[自己修正ループ](propose-self-correction-loop.md)を何度回しても
  治らない。「N 行目で Bad syntax」粒度のフィードバックから「全関数使用を入れ子形に書き直す」を
  弱モデルは導けない。
- 一方で**意味判断は概ね妥当**だった: 列→述語の対応（`title`→`dcterms:title`）も関数の選択
  （`doi_norm`/`datetime_iso`）も正しい。皮肉にも Qwen の発明構文は「列＋述語＋関数名」を素直に
  並べた形 — つまり本 ADR で書かせたい IR そのものに近い。

結論: **LLM の仕事を「厳密な構文の再現」から「閉じた選択肢からの選択」に変える**。§RML を LLM に
書かせるのをやめ、小さな中間表現（Mapping IR）だけを書かせて、RML Turtle はシステムが決定論的に
コンパイルする。強いモデルにも同じ利得がある（構文事故クラスの消滅・プロンプト縮小・キャッシュ
安定・レビュー面の可読化）。

## 2. 決定（提案）

1. **Mapping IR を新設**する: 「ソース → subject（クラス・キー・transform）→ プロパティ行の表
   （列・述語・Tier-0 関数・datatype・termType）」だけを表す小さな YAML スペック（§3）。
   YAML は JSON の上位集合なので、guided JSON（vLLM 系の構造化出力）を使えるプロバイダでは
   IR の構文エラーも原理消滅させられる（任意・プロバイダ依存）。
2. **決定論コンパイラ** `compile_mapping_ir(ir, catalog) -> Turtle` を実装する（§4）。RML/FnO の
   構文知識を**すべてコード側に移す**。純関数・ユニットテスト厚め・既存代表 RML とのゴールデン
   テスト付き。
3. **propose の出力契約**: §9 を「turtle ブロック」から「Mapping IR の yaml ブロック」に置き換える
   （§6）。他 8 セクションは不変 = materialize の英語見出し契約・既存データセット再設計互換を保つ。
   **段階分割そのもの（クラス選定→プロパティ表の複数呼び出し化）は Phase 2 として分離**し、本 ADR
   では単一呼び出しのまま §9 だけ差し替える（既存契約への影響最小・handoff 推奨案）。
4. **検証の前倒し**: IR 段階の検証 `validate_mapping_ir`（列実在 did-you-mean・ソース実在・関数
   メニュー照合）を新設し、自己修正ループのフィードバックを IR の語彙で返す（§5）。既存の RML
   ゲート（`assert_rml_safe`/`validate_rml_design`/T9/422）は**防御として全て維持**する — コンパイラ
   のバグも同じ網で捕まえる。
5. **全面移行**: LLM に生 Turtle を書かせる経路は残さない（設定フラグも作らない）。手書き RML・
   既存レジストリの `mapping.rml.ttl`・旧 proposal の turtle ブロックは**そのまま一級市民**として
   動き続ける（§7）。プロダクト哲学（決定論・型付きが主役、LLM は edge）に沿う。

## 3. Mapping IR v1 スキーマ

設計原則: **LLM が書く全ての値は「閉じたメニューからの選択」か「小さなテンプレート文字列」**。
フィールド名は Qwen が自然に発明した形（列＋述語＋関数名の表）に寄せ、ドメイン非依存に保つ。

```yaml
version: 1
prefixes:                     # CURIE 展開表（§2 IRI scheme と同じ宣言を自己完結で再掲）
  sd:  "https://kumagallium.github.io/asterism/starrydata/ontology#"
  sdr: "https://kumagallium.github.io/asterism/starrydata/resource/"
  schema: "https://schema.org/"
  dcterms: "http://purl.org/dc/terms/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: paper                         # TriplesMap 名（<#PaperMap> に整形）
    source: papers.csv                  # inspection の列挙名そのまま（コピー厳守）
    subject:
      template: "sdr:paper/{SID}"       # {列} プレースホルダ・CURIE 可（コンパイラが展開）
      classes: [sd:Paper, schema:ScholarlyArticle, prov:Entity]
    properties:
      - predicate: schema:name          # 直接参照
        column: title
      - predicate: schema:datePublished # Tier-0 関数（fn: 接頭辞なしの関数名）
        column: issued
        function: date_iso
        datatype: xsd:date
      - predicate: schema:url           # IRI を返す関数は object_type で termType 指定
        column: URL
        function: iri_safe
        object_type: iri
      - predicate: sd:pointCount        # 多入力関数は columns（束縛順はコンパイラが解決）
        columns: [x, y]
        function: float_array_count
        datatype: xsd:integer
      - predicate: sd:tag               # 定数引数は python 引数名で（p_* IRI は書かせない）
        column: tags
        function: split
        args: { delimiter: "," }
      - predicate: sd:fromPaper         # IRI リンク（rr:template + rr:termType rr:IRI）
        object_template: "sdr:paper/{SID}"
      - predicate: dcterms:identifier   # リテラル合成テンプレート
        object_template: "{SID}-{sample_id}"
        object_type: literal
      - predicate: sd:authorsRaw        # 生文字列フォールバック（未展開フラグ）
        column: author
        fallback: true
  - name: composition                   # データ由来 IRI は生 template で安全（engine が
    source: samples.csv                 # R2RML 準拠 percent-encode。probe 実証・§4）
    subject:
      template: "sdr:composition/{composition}"
      classes: [sd:Composition]
    properties:
      - predicate: sd:hasFormula
        column: composition             # 生値はリテラルとして温存
  - name: periodical                    # 可読な IRI セグメントが欲しい共有ノードは
    source: papers.csv                  # transform（Tier-0 の単一入力関数）を宣言
    subject:
      template: "sdr:periodical/{container_title}"
      transform: { container_title: slug }
      classes: [sd:Periodical]
    properties:
      - predicate: schema:name
        column: container_title
  - name: section                       # XML（文書層）: iterator + 定数 subject
    source: PMC5951533.xml
    iterator: "/article/body/sec"
    subject:
      template: "…/paper/PMC5951533/sec/{@id}"   # 参照は iterator 相対パス（不透明に素通し）
      classes: [doco:Section]
    properties:
      - predicate: po:contains
        object_template: "…/sec/{sec/@id}"        # 多値の子参照 → 1 子 1 トリプル
      - predicate: lit:pmcid                       # 定数値（per-paper 注入）
        constant: "PMC5951533"
```

フィールド仕様（v1 で閉じる・未知フィールドはコンパイルエラー）:

| フィールド | 意味 | 検証 |
|---|---|---|
| `prefixes` | CURIE 展開表 | IRI 形式のみ |
| `maps[].name` | TriplesMap 名 | 識別子形式 |
| `maps[].source` | ソースファイル名 | **実在チェック**（did-you-mean） |
| `maps[].iterator` | XML のみ: XPath iterator | XML ソース時のみ許可 |
| `subject.template` / `subject.constant` | subject IRI（排他） | プレースホルダ列の**実在チェック** |
| `subject.classes` | `rr:class`（複数可） | CURIE 解決可能 |
| `subject.transform` / `properties[].transform` | template 内列の可読セグメント化（`{列: slug}` 等） | プレースホルダに限る・単一入力 Tier-0 のみ・多値不可・≤4 プレースホルダ |
| `properties[].predicate` | 述語 | CURIE 解決可能 |
| `properties[].column` / `columns` | 列参照（単/多入力） | **実在チェック**（tabular のみ・did-you-mean） |
| `properties[].function` | Tier-0 関数名 | **REGISTRY 照合**（閉集合） |
| `properties[].args` | 定数引数（引数名→値） | **関数シグネチャ照合**（引数名の過不足） |
| `properties[].object_template` | IRI リンク／リテラル合成 | プレースホルダ列の実在チェック |
| `properties[].constant` | 定数値 | — |
| `properties[].object_type` | `iri` / `literal`（template の termType） | enum |
| `properties[].datatype` | `rr:datatype`（CURIE） | CURIE 解決可能 |
| `properties[].language` | `rr:language`（任意） | BCP47 形式 |
| `properties[].fallback` | `…Raw` 未展開フラグ（coverage 計測用メタ） | bool |

**IR に書けないこと（意図的）**: 関数の任意入れ子合成（可読セグメントは transform 宣言で足りる）、
rr:joinCondition（IRI 決定論のリンクは template で足りる・現行コーパスに join ゼロ）、named
graph（substrate が取り込み時に決める）、来歴配線（substrate 定数・従来通り範囲外）。多値展開は
**関数の選択が含意**する（`split`/`json_array`/`json_pluck` は list 返却＝explode）— LLM に
フラグを書かせない。

**§6 rdf-config model.yaml との関係 = 併存**。model.yaml はオントロジー形（クラス・プロパティの
宣言、ShEx 生成の入力）、IR は「ソース→RDF の束縛」（列・関数・datatype）で責務が直交する。
model.yaml を拡張して束縛を混ぜると外部フォーマット（dbcls/rdf-config）の意味論を侵食する。

## 4. 決定論コンパイラ

`compile_mapping_ir(ir: MappingIR, catalog: FunctionCatalog) -> str`（Turtle）。

**コードに移る構文知識**（= 現 HARD RULES の本体・`step0-rml-emission.md` が仕様の原典）:

- prefix ボイラープレート（`rr:`/`rml:`/`ql:`/`rmlf:`=`http://w3id.org/rml/`/`fn:`/`xsd:`）—
  旧 `fnml#` 名前空間事故（#86）はクラスとして消滅。
- `rmlf:functionExecution` の形: `rmlf:function` + `rmlf:input [ rmlf:parameter … ;
  rmlf:inputValueMap [ rml:reference … | rmlf:constant … ] ]`。**パラメータ IRI
  （`fn:p_value`/`p_value1`/`p_value2`/`p_table`/`p_pattern`/`p_delimiter`/…）は catalog
  （`asterism.functions.REGISTRY` の `FunctionSpec.params`）から引く** — IR は python 引数名しか
  持たないので、パラメータ IRI 誤りは構造的に不可能。
- 定数引数は必ず `rmlf:constant`（レガシー `rml:` に `constant` は無い罠）。
- `rr:template` 内の CURIE を**完全 IRI に展開**（RML エンジンはテンプレート文字列内の prefix を
  展開しない — LLM が最も踏む罠のひとつ）。`{`/`}` エスケープも所有。
- termType/datatype/language の付与規則（IRI 関数は `rr:termType rr:IRI`、リテラル template は
  `rr:termType rr:Literal`、リンク template は IRI）。
- CSV / tabularized-JSON（= CSV 扱い）/ XML（`ql:XPath` + iterator + 定数 subject）の
  logicalSource 形。

**データ由来 IRI の扱い（実 Morph-KGC probe で確定・2026-07-06）** — 現 HARD RULES の
iri_safe 前処理指針は probe の結果**そもそも意味的に壊れていた**（`fn:iri_safe`=safe_url は
URL 以外の文字列に `""` を返す → composition 等に使うと行ごと消える）。確定した設計:

1. **生 template は安全**: Morph-KGC は `rr:template` のプレースホルダ値を R2RML 準拠で
   percent-encode する（空白/引用符/`<>`/バックスラッシュ/Unicode 全て有効 IRI 化・strict NT
   round-trip PASS）。よって IRI-safe ラップは**不要** — 既存 template ベースのデータと IRI が
   完全一致する（IRI 安定性の最善解）。この挙動は gated 回帰テストでピン留め（将来の
   morph-kgc 更新で挙動が変われば本番 ingest でなくテストが落ちる）。
2. **可読セグメントが欲しい場合だけ `transform`**（periodical の slug パターン等）: コンパイラは
   `fn:template`（定数 template + 位置トークン）に**入れ子 functionExecution**（`fn:slug` 等の
   単一入力 Tier-0）を合成して emit する。入れ子は実 morph-kgc で動作実証済み。
3. **bare column の IRI 化（`rml:reference` + `rr:termType rr:IRI`）は禁止**: reference 値は
   エンコードされず、空白入り値が invalid IRI としてストア投入で即死する（probe 実証 —
   本番の「Invalid IRI code point」の正体）。パーサ/コンパイラ両方が拒否し、URL 列は
   `function: iri_safe` + `object_type: iri`（clean URL には恒等）へ誘導する。

**fail-closed 原則**: コンパイラは閉集合を**狭める方向にのみ**働く。catalog に無い関数名・仕様外
フィールド・未解決 CURIE・引数名の過不足は**コンパイルエラー**（該当なしをでっち上げない・関数を
勝手に追加しない）。エラーは IR の語彙で actionable に（`Issue` 分類可能な形・§5）。

**配置**: `step0/src/asterism_step0/mapping_ir.py`（パーサ+スキーマ検証）+ `rml_compile.py`
（コンパイラ）。**関数メタデータは `FunctionCatalog` として引数注入**し、既定プロバイダが
`asterism.functions.REGISTRY` を import する（graceful ImportError・design_loop の
「Tier 0 registry is not importable」env-bail と同じ既存パターン）。単一の真実源を REGISTRY に
保ったまま、コンパイラ本体は純関数としてテスト可能（step0 CI は ingest を test-time 導入済み）。

## 5. 検証の前倒し（自己修正ループの収束性）

新設 `validate_mapping_ir(ir, source_dir, catalog)`: スキーマ形・ソース実在・列実在
（`read_csv_header` と同じ BOM-safe 読み・difflib did-you-mean）・関数メニュー照合・引数名照合。
`rml_validate.py` と同様に**全 issue を収集**（先頭で止めない）。

`design_loop` の 1 ラウンド評価は次の直列になる:

1. materialize（抽出）で IR ブロックを取得 — 欠落なら「Mapping spec 欠落」で即停止（従来の
   §RML 欠落と同型）
2. `validate_mapping_ir` — **フィードバックが IR の語彙になる**:
   「`titel` という列は `papers.csv` に無い。`title` では?」「`datetime_iso` は存在する。
   `rml:transform` という書き方は無く、`function: datetime_iso` と書く」ではなく後者はそもそも
   書けない。弱モデルが実際に直せる粒度。
3. `compile_mapping_ir` — 通常ここは通る（IR 検証通過後のコンパイルエラー = コンパイラのバグ or
   スキーマ検証漏れ・env 扱いで surface）
4. 既存 RML ゲート（`assert_rml_safe` → `validate_rml_design`）— **防御として維持**。コンパイラの
   バグ・IR 検証の盲点を捕まえる二重底。

これにより、issue の主要カテゴリ（turtle 構文・関数パラメータ IRI・非 Tier-0 関数・fnml 名前空間）
が**原理的に発生しなくなり**、残るのは列名・ファイル名・述語選択という「LLM が直せる意味エラー」
だけになる。Tier-0 オラクル（`build_oracle`）は簡素化して維持: パラメータ IRI の列挙は不要になり、
関数名＋引数名＋一行説明のメニューになる。

**erase-to-green 対策の移植**: `_reference_count`（rml:reference 数の粗い proxy）は IR の
プロパティ行数 + 列束縛数のカウントに置き換える（より正確になる）。

## 6. 出力契約と配線

- **propose SYSTEM_PROMPT**: §9 の見出しは「### 9. Declarative mapping spec」（materialize の
  `_RML_HEADERS` は "declarative mapping" で命中・英語見出し契約維持）。中身は ```yaml フェンス 1 個。
  HARD RULES ~90 行（RML 構文）は削除され、(a) IR スキーマの簡潔な記述、(b) Tier-0 関数メニュー
  （名前・引数名・一行意味 — これは意味選択なので残る）、(c) ファイル名/列名コピー厳守・transform
  宣言・fallback 規則、に縮む。byte-stable・cache 安定は不変。
- **materialize**: IR ブロックを抽出する `mapping_ir` を追加（header keyword + yaml、model/MIE の
  yaml ブロックと既存の優先順位ロジックで確実に排他）。IR があれば `{name}-mapping.yaml`（レビュー・
  再編集用）と**コンパイル済み** `{name}-mapping.rml.ttl` の両方を書き、`MaterializeResult.rml_ttl`
  にはコンパイル結果を入れる — **materialize より下流（registry・ingest・422 ゲート・T9・
  workbench・UI）は無改修**。turtle ブロックしか無い旧 proposal は従来通り抽出（additive・
  `complete` 判定は不変）。
- **人間ゲートの改善（副産物）**: レビュー面が「Turtle を読む」から「列→述語→関数の表（YAML）を
  読む」になる。phase5 の理念（人間が承認するのは列→述語の対応＋関数参照であってコードではない）に
  実態が一致する。
- **設計図（diagram.md）も IR からコンパイル（2026-07-23 追記）**: §1 の LLM スケッチは実運用で
  「空のクラス箱＋全述語が図の外に孤立」を生んだ（ZEM dogfood — レビューの主戦場である図に確認材料が
  無い）。parse 可能な §9 がある場合、materialize は diagram.md を IR から決定論生成する
  （`asterism_step0.ir2mermaid`）: クラス箱= `subject.classes`・箱内メンバー= literal 述語
  （`+name xsd_type [unit]`・**IR 記述順=列レビュー順**・label/unit 表示メタを活用）・エッジ=
  `object_template` が他 map の `subject.template` に一致する IRI リンク。フェンス下に
  Property↔column↔unit↔meaning の対応表を併記（「この列はどこへ行ったか」の来歴面）。
  §1 スケッチは spec 無し/parse 不能時の fallback に残り、RML コンパイル失敗でも parse 成功なら
  図は出す（構造こそ設計修正時に見るべきもの）。renderer は ttl2mermaid と共用・T5 lint の安全文法
  内で生成（メンバー行コロン無し・Mermaid 11 実パーサで受理確認）。既存 registry は
  `asterism-ir2mermaid <name>-mapping.yaml --name <name> --output diagram.md` で再生成できる。
- **diagram.md の形は 1 箇所（2026-07-24 追記）**: 当初、対応表が乗るのは materialize が**ファイルに
  書く**経路だけで、api（`/api/materialize`）は `MaterializeResult.mermaid`（フェンス中身だけ）を
  registry に保存していた＝**api 経由で作った設計は対応表が失われる**（本番 ZEM で発現: CLI 再生成物には
  表があるのに registry の diagram.md には無い）。修正＝`ir2mermaid.render_diagram_doc()` を
  「diagram.md の唯一の形」（タイトル＋フェンス＋対応表）とし、materialize の書き出し・
  `MaterializeResult.diagram_md`（api が保存する値）・`render_dataset_doc()`（再生成 CLI）の
  3 producer が同一関数を通る。**`mermaid` はフェンスの中身、`diagram_md` がアーティファクト本体**。
  読み手（UI `extractMermaid` / `registry.mermaid_of`）は先頭フェンスのみ抽出するので図は不変。

## 7. 後方互換・移行

- **既存レジストリのデータセット**: 保存済み `mapping.rml.ttl` が実行アーティファクトのまま。IRI
  テンプレート意味論は不変なので **IRI 安定性に影響なし**。再設計時に新 propose が IR を出しても、
  コンパイル結果の IRI 形は従来規則と同一。
- **手書き RML**（`datasets/papers` の JATS mapping・MP の例・crosswalk）: 変更なし。RML は
  実行層の一級インターフェースであり続ける — IR は「AI に書かせる面」を狭めるだけで、RML への
  escape hatch を塞がない。
- **旧 proposal Markdown**: turtle ブロック抽出は残す（materialize additive 原則）。
- **LLM に Turtle を書かせるフラグは作らない**（全面移行）。強いモデルでも構文事故は起きており
  （#86 の fnml 名前空間 guess は Claude 系）、経路の分岐は検証マトリクスを倍にするだけ。

## 8. 不変条件（破らない）

- **信頼境界不変**: 生成コード非実行。コンパイラは Tier-0 閉集合を狭める方向にのみ働く。コンパイル
  済み RML も `assert_rml_safe` + `validate_rml_design` + T9 + hard 422 ingest gate を全通過する
  （**422 gate が本ゲートのまま**）。
- **汎用性**: IR・コンパイラ・プロンプトはドメイン非依存（材料特化の語彙・規則を入れない）。
- **materialize 契約**: 英語見出しによる artifact 抽出・additive 原則・`complete` 判定を壊さない。
- **IRI 安定性**: 複合キー・iri_safe 規則の決定論を変えない。
- Ask/ingest ランタイムは LLM-free のまま（変更は設計/オーサリング層のみ）。

## 9. 代替案（棄却）

- **(a) 現契約のままフィードバック強化**: 実証済みで不十分。構文粒度のフィードバックでは 3B
  アクティブ級が収束しない（本番ドッグフードの直接根拠）。
- **(b) Turtle の grammar-constrained decoding**: プロバイダ依存が強く、文法を通っても意味の罠
  （パラメータ IRI・入れ子・CURIE 非展開）は残る。文法定義も巨大でキャッシュ非親和。
- **(c) いきなり完全段階分割（複数 LLM 呼び出し）**: 契約変更が大きく呼び出し回数も増える。§9 の
  IR 置換で弱モデル問題の本体（構文事故）は消えるので、段階分割は Phase 2 の発展として分離
  （guided JSON の導入もそこで検討）。
- **(d) rdf-config model.yaml を IR に拡張**: 外部フォーマットの意味論を侵食し、rdf-config
  ツールチェーン（ShEx 生成）との互換を危険に晒す。束縛情報（列・関数・datatype）は model.yaml の
  語彙に無い。併存が正しい。

## 10. 受け入れ基準・検証計画

1. **ゴールデンテスト**: 代表 RML（`e2e/mappings.rml.ttl`・MP `mp.rml.ttl`(CSV 化後)・coverage
   コーパス proposal 数件）を IR に書き起こし、コンパイル出力がグラフ同型（rdflib パース後の
   トリプル集合一致・byte 一致は要求しない）。
2. **実 Morph-KGC e2e**（gated）: コンパイル済み RML の materialize 結果が既存 RML と triple 集合
   一致。template percent-encode / 入れ子 transform / 多値 explode の回帰ピンを含む。
3. **実測（本丸）**: `dataset-548d5ca3` と同じ Starrydata CSV を **Qwen3.6-35B-A3B /
   gpt-oss-120b** で propose:
   - T9 / Turtle 構文 / パラメータ IRI / fnml 名前空間の issue が **0 件**（原理消滅の実証）
   - 自己修正ループが収束（残 issue は列名/述語選択の意味エラーのみ・ラウンド内で単調減少）
4. **回帰**: 既存テスト全緑（step0/api/ingest）・旧 proposal fixture の materialize 不変・
   手書き RML 経路（papers/MP）不変。
5. `docs/reports/` に Q→Method→Result→Conclusion→Limitations→Reproduce 形式でレポート
   （恒久方針）。

## 11. 既知の限界（正直に）

- **意味エラーは残る**: 述語の選択ミス・キー列の選択ミス・transform の付け忘れ（可読 IRI に
  するかは意味判断。妥当性は engine encoding が守るので壊れはしない）は IR でも起きる。ループの列実在チェックと oracle が押し戻すが、保証はしない —
  従来同様 422 gate と人間ゲートが最終防衛線。
- **XML ソースの参照（XPath）は列レベル検証されない**（従来の JSON/XML 盲点と同じ・tabular のみ）。
- **IR の表現力は意図的に狭い**: 入れ子関数合成・join・条件分岐は書けない。書けない要求が実データで
  出たら、まず Tier-0 関数の追加（人間 vet）か substrate 前処理で吸収し、IR 拡張は最後の手段
  （閉じた選択肢という設計原理を守る）。
- **コンパイラ自体が新たな信頼対象**になるが、出力は既存の全ゲートを通過するため、バグの blast
  radius は「取り込み失敗」まで（不正データの侵入ではない）。
