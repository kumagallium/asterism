# ADR: 取り込んだ実データが「設計どおり」かを機械が確かめる（shape 検査）

- **状態**: 実装中（本 PR）
- **決定者**: kumagallium + Claude Code
- **契機**: 「データに意味の層を足す（オントロジー／セマンティックレイヤー）」論
  （2026-08 Qiita 記事）との突き合わせ。記事の中心主張は
  **意味層は「ドキュメント」ではなく実行可能なスキーマであり、定義に反する
  データ／クエリが通らないこと**。Asterism はこの主張の大半を既に満たすが、
  **一箇所だけ構造的な穴**があった（下記）。

## 問題 — 検証は「設計の中」で閉じていた

Asterism の既存の検証は 2 層あり、どちらも**実データを見ていない**。

| 層 | 実体 | 見ているもの |
|---|---|---|
| 設計の自己整合 | `asterism_step0.validate` の T1–T10 | 設計文書どうしの一貫性（ID 一意性・BOM・bnode・§7 例クエリ…） |
| 設計 vs ソース | `asterism.rml_validate`（`validate_rml_design` / `design_advisories`） | RML が参照する**列**が実 CSV にあるか・Tier 0 関数の引数・エンティティの接続性 |

つまり「**取り込みが完了したグラフが、設計の宣言どおりの形をしているか**」は
誰も見ていない。RML が構文的に正しく、列も実在し、Morph-KGC が例外なく完走
しても、出来上がったグラフは次のように壊れうる:

- 宣言した述語が **一度も materialize されていない**（変換が全行で空を返した・
  値が全行欠損・関数が黙って None を返した）。T9/`validate_rml_design` は
  「列がある」までしか見ないので通過する。
- リンク先の IRI が **グラフに存在しない**（親子で IRI テンプレートの片方だけ
  変わった・キーの正規化がリンク側だけ抜けた）。接続性 advisory は
  「設計上リンクが書かれているか」を見るだけで、**実際に着地しているか**は
  見ない（観測: 設計上は繋がっているのに実データでは 0 本、が起こりうる）。
- リンクの相手が **想定と違うクラス**（記事の言う関係制約＝「仕入先は発注
  できない」）。
- 宣言した `xsd:double` に **数値でないリテラル**が入っている。

これらはいずれも Ask（引用できる事実）を静かに壊す。「答えが出ない」ではなく
「答えが**空**」になるため、利用者は自分の質問が悪いのだと誤解する。

## 決定

### D1. shape は設計から決定論コンパイルする（AI 非介在）

`asterism.shapes.compile_shapes(rml_ttl)` が、**取り込みに実際に使われた RML**
から node shape 群（クラス → 述語 → 値の種別／datatype／リンク先クラス）を
導出する。設計の別表現（MIE・図・model.yaml）ではなく RML を唯一の入力とする
のは、RML こそがデータを作った当人であり、他の表現とのずれが検査の偽陽性に
ならないため。LLM は一切関与しない（`ir2mermaid` / `rml_compile` と同型）。

### D2. 検査は SPARQL で store 上を走る（pySHACL を持ち込まない）

shape は **SPARQL クエリに決定論変換**し、既に Oxigraph に載っているグラフに
対して実行する。

- pySHACL を採らない理由: グラフを丸ごと Python 側に読み直す必要があり、
  数百万トリプル規模で成立しない。Oxigraph は既にそのデータを索引済み。
- 新しい依存はゼロ。`no-codegen` 不変条件も守る（生成されるのは read-only の
  SPARQL であって実行コードではない）。
- `COUNT` を使わず **`LIMIT` 付きで違反例を最大 5 件**取る。「何件あるか」より
  「どれが壊れているか」の方が直せる情報であり、巨大グラフでも軽い。

### D3. 標準成果物としての SHACL も出す（相互運用）

`shapes_to_shacl()` が同じ shape を **標準 SHACL** の Turtle で出力し、
`GET /api/datasets/{id}/shapes.ttl` で取れる。検査エンジンとしては使わないが、
「この意味層は実行可能なスキーマである」ことを Asterism の外（pySHACL・
TopBraid・他社のセマンティックレイヤー）に持ち出せる形で示す。記事が触れる
業界標準化（OSI）方向への接続点でもある。

### D4. 検査は advisory であってゲートではない

不合格でも取り込み・昇格をブロックしない。理由は記事も挙げる書き込み時／
読み取り時検証のトレードオフで、Asterism は一貫して**後者**を採ってきた
（入口で弾くと取り込みが止まり、研究データは大抵どこかが欠けている）。
壊れていることが**見える**ことが価値であり、止めることではない。

### D5. UI は増やさない

所見は既存の advisory 経路（`/api/datasets/{id}/validate-design` の
`advisories`、データセット詳細の「気になる点」）に**合流**する。新しいタブ・
パネル・ボタンはゼロ。`advisoryPlain.ts` に分類ルールを 4 つ足すだけで、
平易な日本語 1 行＋生テキストの fold という既存の見せ方に乗る。

固定 marker phrase（`advisoryPlain.ts` が照合する、決定論生成の英文）:

| 検査 | marker |
|---|---|
| 宣言した述語がデータに 0 件 | `declared but MISSING in the ingested data` |
| リンク先 IRI がグラフに不在 | `DANGLING reference` |
| リンク先が想定外のクラス | `WRONG class` |
| リテラルの datatype 不一致 | `datatype MISMATCH` |

## 検査項目（Phase 1）

| ID | 内容 | SPARQL の形 |
|---|---|---|
| S1 | クラス C のインスタンスは存在するのに、設計が宣言した述語 P が 1 件も無い | `ASK` 2 本（C の存在／C+P の存在） |
| S2 | P の値 IRI が、グラフ内で**主語として一度も現れない**（リンク切れ） | `SELECT … FILTER NOT EXISTS { ?o ?p2 ?o2 } LIMIT 6` |
| S3 | P の値 IRI に型はあるが、設計が期待するクラスのどれでもない | `SELECT … ?o a ?t . FILTER(?t NOT IN (…)) LIMIT 6` |
| S4 | 宣言 datatype と実リテラルの datatype が違う | `SELECT … FILTER(datatype(?o) != <D>) LIMIT 6` |

S2 を「型が無い」ではなく「主語として現れない」で定義したのは、`rr:class` を
持たない TriplesMap（型を付けない設計）でも偽陽性を出さないため。

**意図的に入れないもの**: 「クラス C のうち P を持つのは 62%」式の欠損率
advisory。研究データの欠損は正常であり、これを出すと画面が恒常的に警告で
埋まって他の所見が読まれなくなる（ZEM で 13 件の advisory が画面を占領した
2026-07-24 の観測と同じ失敗）。0% だけが異常なので S1 で捕まえる。

## 代替案

- **A. pySHACL を検証エンジンにする** — 標準準拠は最短だが D2 のとおり規模で
  破綻し、依存も重い。SHACL は D3 の**出力形式**としてだけ採る。
- **B. ShEx**（`design-rationale.md` §13 の既存決定・MIE `shape_expressions`）
  — AI に shape を「伝える」用途では引き続き ShEx でよい。だが実データ検証の
  ためのエンジンは Python 側に成熟した実装が無く、SPARQL 変換の方が確実。
  ShEx は人と AI 向けの表現、SHACL は外部ツール向けの表現、SPARQL は実行系、
  と役割を分ける。
- **C. 取り込みの入口で弾く（write-time validation）** — D4 のとおり不採用。

## 実 RML での確認（2026-08-14）

コンパイラを既存の実マッピングに当てた結果:

| マッピング | shape 数 | クエリ数 |
|---|---|---|
| `datasets/materials_project/json/mp.rml.ttl` | 3 | 32 |
| 弱モデル dogfood（gpt-oss 生成） | 6 | 80 |
| `datasets/papers/jats/PMC5951533.rml.ttl` | 4 | 12 |

- リンク先クラスの解決は実マッピングでも効いている
  （`hasCrystalStructure → CrystalStructure`、`ofMaterial → Material`）。
- 1 つの TriplesMap が複数クラス（`Material` と汎用 `Entity`）を宣言すると
  shape も複数できるが、S1 は「そのクラスの誰か 1 つでも持っていれば pass」
  なので、汎用クラスに述語が合流しても偽陽性にはならない。
- リンク先が解決できないとき `target_classes` は空になり、S3 は**走らない**
  （安全側に倒れる）。弱モデル生成のマッピングで実際に空が出た。

## 性能（2026-08-14 実測）

S2（dangling）は「その述語のどの値も主語として現れない」を探すため、違反が
**無い**場合は該当エンティティを走査しきる。そこで **125 万トリプル**の合成
グラフ（Sample 25 万＋Measurement 25 万・リンクは全て健全＝早期脱出が一切
効かない最悪ケース）を pyoxigraph に載せて全チェックを実測した:

| チェック | 述語 | 所要 |
|---|---|---|
| predicate-missing | value / hasMeasurement / mass | 0.006s / 0.000s / 0.087s |
| datatype-mismatch | value / mass | 0.577s / 0.583s |
| dangling-reference | hasMeasurement | **1.195s** |
| class-mismatch | hasMeasurement | **1.814s** |
| | **合計 7 本** | **4.26s** |

ingest / append の直後に一度だけ走る設計なので、この規模では問題にならない。
`ASK` で済む S1 はほぼ無料で、コストは全て走査を伴う S2/S3 に出る。数千万
トリプル級で問題になったら、内側を `LIMIT` 付きの部分集合に切る（「最初の
N 件を見た限り」に弱める）余地を残す。クエリ本数は `max_queries`（既定 400）
で上限を切っており、実マッピングの実測は 12–80 本。

## UI 実描画の確認（2026-08-14）

一時 registry の api（:8099）＋実 UI（:5199）に 4 種すべての所見を注入し、
実ブラウザで確認:

- カタログのデータセット詳細「設計図」タブ、構造図の直下に**「気になる点」**
  として 4 行が平易文で出る。専門用語（IRI・述語・datatype）は表に出ない。
- 「指摘の原文（技術情報）」の fold に英語原文が残る（AI 修正に渡す形は不変）。
- ja / en 両方で正しく切り替わる（`shapeMissing` / `shapeDangling` /
  `shapeWrongClass` / `shapeDatatype`）。
- 新しい画面要素はゼロ。既存 advisory ブロックに行が増えるだけ。

## トレードオフ

- クラス数 × 述語数のクエリが走る。実測で足りなければ shape 単位に束ねる
  （現状の設計規模なら数十本で、いずれも `LIMIT` 付き）。
- 検査対象は「そのデータセットのグラフ」。crosswalk で合流した外部グラフ上の
  リンク先は S2 で「不在」と出る可能性があるため、検査は**データセットの
  グラフのみ**をスコープし、FROM-merge した合体ビューでは走らせない。

## Cross-refs

- `ingest/src/asterism/shapes.py`（実装）
- `ingest/src/asterism/rml_validate.py`（既存の設計 vs ソース検証・advisory の作法）
- `docs/architecture/design-rationale.md` §13（ShEx 採用の既存決定）
- `docs/architecture/ingestion-execution-safety.md`（no-codegen 不変条件）
