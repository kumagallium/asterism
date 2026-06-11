# ADR: native JSON の入れ子配列を取り込む（source 境界で tabularize → 既存 Tier0 explode）

Status: **承認済・配線着地（コア＋ランタイム＋design-time）**。中核仮説はスパイク
([`experiments/native-json-denormalize-spike/`](../../experiments/native-json-denormalize-spike/))
で実証、`asterism.tabularize` 製品化＋substrate 自動 tabularize＋propose/inspect の CSV+tabularize
出力まで実装し ingest 255 / step0 186 緑。**MP 後方互換確認済**（既存 JSONPath RML は無傷で 143 triples）。
✅ coverage 再計測（PR #192・`…Raw` 0.0%・ゲート 5%）・✅ MP 例 RML の CSV 移行（JSONPath 使用ゼロに）・
✅ CSV 直取込の予約列ガード（`sanitize_csv_sources`＋inspect）・✅ `{key:[...]}` wrapped 配列の自動検出・
✅ inspect↔tabularize の drift 対策（依存結合でなく sync guard・§8.8）。**全 follow-up 完了**。

決定母体:
[`ingestion-execution-safety.md`](ingestion-execution-safety.md)（生成コード非実行・Tier0 閉集合のみ）/
[`tier0-coverage-gate.md`](tier0-coverage-gate.md)（Tier0「十分」の計測ゲート）/
[`phase5-declarative-substrate.md`](phase5-declarative-substrate.md)（宣言経路＝RML/Morph-KGC）/
[`non-csv-sources.md`](non-csv-sources.md)（非 CSV ソースの取り込み境界）/
[`scalable-declarative-ingestion.md`](scalable-declarative-ingestion.md)（source 永続化・draft 隔離）

関連レポート:
[`reports/tier0-coverage-sufficiency.md`](../reports/tier0-coverage-sufficiency.md)（本 ADR が片付ける「還元不能 raw 残3件」の出所）

---

## 0. 何を直すか

Tier0 充足レポートは、14 データセットのコーパスで computation を要する列の **11.1%** だけが
`…Raw` フォールバックに落ち、それらは「**還元不能**」と結論した。還元不能の正体はちょうど3件：

| 列 | 形 | ソース形式 | 欲しい結果 |
|---|---|---|---|
| `crossref-works.author` | object 配列 `[{given, family, …}, …]` | native JSON | 各著者 family/given を work にリンク |
| `openlibrary-books.subject` | 文字列配列 `["Textbooks", …]` | native JSON | 各 subject を book にリンク |
| `github-repos.topics` | 文字列配列 `["ai", …]` | native JSON | 各 topic を repo にリンク |

いずれも **native JSON ソースの入れ子配列**で、「配列の全要素を親にリンクしたい」という同じ要求。
Tier0 のスカラ関数では届かず raw に落ちる。これが「Tier0 は十分・ただし入れ子配列だけは別ワークストリーム」
という積み残しの実体である。本 ADR はこの一点を、信頼モデルを崩さずに閉じる。

---

## 1. なぜ Tier0 関数では届かないか（Morph-KGC 2.8.1 + JSONPath の境界）

スパイクで確定済みの境界（再発見しないこと）：

1. JSON ソースの多重参照 `rml:reference "author[*].family"` → **0 件**（JSONPath 多重展開は非対応）。
2. **native JSON 配列を関数に渡すと morph-kgc が `.str` アクセサでクラッシュ**（str/list 返り問わず）。
   ＝ JSON ソースの入れ子配列セルは Tier0 関数の入力にできない。
3. 入れ子 iterator `rml:iterator "$[*].author[*]"` は子エンティティを作れるが、**親キー（work の DOI 等）を
   参照できない**＝親にリンクできない孤立エンティティになる。
4. **対照的に、CSV/JSON の「文字列セル」に入った配列なら、`json_pluck`/`json_array`/`split` が
   list を返し → Morph-KGC が要素ごとに explode → 各要素が親行にリンクする**（starrydata の
   author/project_names がまさにこの形・`test_materialize_json_array_and_pluck_from_string_cells`）。

つまりギャップは狭く正確で、**「native list か文字列セルか」の一点だけ**。starrydata は
author を CSV の JSON 文字列セルで持つから既に動き、crossref/openlibrary/github は native JSON
だから落ちる。同じ explode 機構が、入力の見た目だけで効いたり効かなかったりする。

---

## 2. 決定（提案）: source 境界で **tabularize** し、既存 Tier0 explode に載せる

native JSON ソースを、取り込み境界で **平坦な表形式（CSV）** に正規化する。1つの固定・汎用・
データセット非依存な変換 `tabularize` を置く：

- **スカラ葉** → そのまま列（`full_name`, `stargazers_count` …）。
- **入れ子 object** → dotted 列へ平坦化（`owner.login`, `license.spdx_id` …）。
- **配列（スカラ配列も object 配列も）** → **1列に JSON 文字列としてそのまま入れる**
  （`author` 列のセル＝ `'[{"given":…,"family":…}, …]'` という文字列）。

これで native JSON ソースが「starrydata と同じ文字列セル形の CSV」になり、**既存の vetted Tier0 explode が
そのまま効く**：

- スカラ配列（subject/topics）→ `json_array`（または区切り文字なら `split`）で要素ごと explode・親リンク。
- object 配列の sub-field（author.family/given）→ `json_pluck(col, "family")` で要素ごと explode・親リンク。

**新しい関数は要らない。T9 閉集合は不変。** `tabularize` は per-dataset の生成コードではなく、
substrate が既に持つ `absolutize_rml_sources` / `normalize_fno_namespace` と**同じ信頼クラスの固定前処理**
（source を morph-kgc に渡す前に整える）。生成コード非実行の不変条件を一切崩さない。

> 補足: 引き継ぎ時の素案は「親キーを各子要素に注入して side-table 化し `rr:parentTriplesMap` で join」
> だった。スパイクの結論は**それより単純**で、配列を行に割らず**親行に JSON 文字列のまま残し**、Tier0 explode に
> 親リンクを任せれば join 不要。side-table/join は §5 の限界に当たる場合のみ。

---

## 3. スパイク実証（実 morph-kgc・実コーパス3件）

[`experiments/native-json-denormalize-spike/spike.py`](../../experiments/native-json-denormalize-spike/spike.py)
が `tabularize` ＋ §2 の RML パターン（`json_pluck`/`json_array`）を、コーパスの**実 JSON**に対して
materialize し、各要素が親（`ex:/r/<rid>`）にリンクすることを assert する。

```
=== crossref-works ===  rows=40  triples=116
  authorFamily  58 triples over 33 parents  linked=True   e.g. ['Akimoto','Alsamawi','Balestrino',…]
  authorGiven   58 triples over 33 parents  linked=True   e.g. ['Adithi','Alejandro','Alessandro',…]
=== openlibrary-books ===  rows=40  triples=270
  subject      270 triples over 39 parents  linked=True   e.g. ['Activity programs','Chemistry',…]
=== github-repos ===  rows=40  triples=347
  topic        347 triples over 34 parents  linked=True   e.g. ['ai','agents','awesome-list',…]
VERDICT: ALL PASS — 3つの還元不能 raw が tabularize + 既存 Tier0 で閉じる
```

3件すべて、**全要素が正しい親行にリンク**して materialize された。中核仮説（§2）は実データで成立。

再現:
```bash
cd experiments/native-json-denormalize-spike
PYTHONPATH=../../ingest/src <morph-kgc入り python> spike.py
```

---

## 4. スパイクが掘り当てた落とし穴: `subject`/`predicate` 予約列衝突

openlibrary の `subject` 列は最初 **0 triples** だった。原因は **Morph-KGC が term-map 中間 DataFrame で
`subject` と `predicate` を予約列名に使う**こと（大小区別あり）。ソース列が同名だと、関数入力
`rml:reference "subject"` が **CSV セルでなく生成済みの subject IRI** を読み、`json_array(IRI)` → None →
全行 drop（0 triples・例外も警告も出ない静かな失敗）。検証で確定：

```
col=subject -> 0 | col=predicate -> 0 | col=object -> 270 | col=graph -> 270
col=subj -> 270 | col=SUBJECT -> 270   （予約は subject / predicate のみ・大小区別）
```

`tabularize` は予約名（`subject`/`predicate`）を `…_` にリネームしてこれを回避する（`safe_col`）。
**これは入れ子 JSON 固有でなく、`subject`/`predicate` 列を持つ任意の表ソースに効く substrate レベルの
ハザード**で、openlibrary が文字通り `subject` 列を持つために露出した。既存 CSV 経路も同じ穴を持つため、
サニタイズは tabularize に閉じず **取り込み境界の共通防御**として置くべき（§8）。

---

## 5. スコープと限界（正直に）

この設計が閉じるのは **「配列の各要素を親にリンクする多値スカラ投影」**：著者名の列挙・subject の列挙・
topic の列挙——3つの還元不能 raw はすべてこれ。

閉じ**ない**のは **「相関を保った入れ子エンティティの再構成」**。`json_pluck` は1呼び出しで1 field を
独立に explode するので、author の `given` と `family` は**並行リスト**になり、`(given, family)` の組として
1著者エンティティに束ねられない（family の集合と given の集合が別々に親へ吊る）。subject/topic の単純列挙には
無関係だが、将来「first-class な Author エンティティ（given+family+sequence を相関させた子ノード）」が
欲しくなったら、§7 の side-table 正規化（親キー注入 + `rr:parentTriplesMap` join）が必要。

**判断: 相関エンティティ再構成は本 ADR のスコープ外**（YAGNI・コーパスも実需要も今は多値スカラ投影だけ）。
必要になった時点で side-table を別 ADR で足す。`published.date-parts` の入れ子配列の配列（`[[YYYY,M,D]]`）も
同類で、当面は dotted 列＋スカラ投影で足りる範囲に留める。

---

## 6. 不変条件への適合

- **生成コード非実行**: `tabularize` は固定・汎用の前処理（per-dataset コードなし）。`absolutize_rml_sources`
  等と同じ信頼クラス。`assert_rml_safe` の confined source 制約も維持（派生 CSV は dataset の source dir 内）。
- **T9 閉集合不変**: 新関数ゼロ。既存 `json_pluck`/`json_array`/`split` のみ参照。
- **IRI 名前空間安定**: 取り込み結果の IRI は従来どおり決定論的複合キー由来。tabularize は列の見た目を
  変えるだけで IRI 生成規則に触れない。
- **draft 隔離・read-only**: materialize/promote のゲートは不変。

---

## 7. 代替案と棄却理由

| 案 | 内容 | 判定 |
|---|---|---|
| **A. native iterator で nested TriplesMap** | `rml:iterator "$[*].author[*]"` で子を作り join | ✗ 親キー非参照（§1-3）で孤立・JSONPath 多重展開は 0件（§1-1）。morph-kgc 2.8.1 では不可 |
| **B. native JSON 配列を直接 Tier0 関数へ** | `json_array` 等に native list を渡す | ✗ `.str` クラッシュ（§1-2）。文字列セル前提を崩せない |
| **C. native JSON 用の新 explode 関数を Tier0 に追加** | native list を受ける関数を vet して足す | ✗ T9 を広げる方向＝閉集合の「小さく保つ」設計に逆行。tabularize で既存関数に載る以上不要 |
| **D. side-table 正規化（親キー注入 + parentTriplesMap join）** | 配列を子行に割り親キーを注入して join | △ 相関エンティティ再構成には正しいが、多値スカラ投影には過剰（join・追加 TriplesMap）。§5 の限界に当たる将来要件まで保留 |
| **E（採用）. source 境界で tabularize → 既存 explode** | 配列を JSON 文字列セルに、object を dotted 列に平坦化 | ✓ 新関数ゼロ・join 不要・実データ実証済・既存信頼クラス |

---

## 8. 実装計画（着地状況つき・ユーザー承認済「理想＝統一」方針）

非破壊な統一を選択：propose は JSON データセットで `rml:source "<stem>.csv"`（`ql:CSV`）を出す。substrate は
**「RML が参照する `X.csv` が disk に無く、同名 `X.json`/`X.geojson` がある」**ときだけ `X.json→X.csv` を
tabularize する（formulation 解析不要・宣言的シグナル）。既存 JSONPath RML（`X.json` 参照・MP）は `.csv` を
要求しないので**無傷**＝後方互換。

1. **✅ `tabularize` を ingest に製品化** — `asterism.tabularize`（`flatten_record`＝json_normalize 流儀＋
   `safe_col` 予約列サニタイズ／`tabularize_json_to_csv`）＋`test_tabularize.py`（ユニット＋ morph gated e2e 3形）。
2. **✅ substrate 自動 tabularize** — `tabularize_json_sources` を `materialize_to_graph`/`materialize_to_nt_file`
   の prepare（assert→tabularize→absolutize→fno 正規化）に注入。派生 CSV は work dir。`assert_rml_safe` は
   存在チェックなし＝相対 `.csv`（合格）→ tabularize で絶対 work csv へ書換、で整合。e2e で「.json だけ永続＋CSV RML
   →内部派生→親リンク explode」を確認。
3. **✅ inspect/propose が tabularized 列＋CSV を出力** — `inspect.render_markdown` の JSON ブロックを
   「`<stem>.csv`＋`ql:CSV`＋iterator なし・配列列は `json_array`/`json_pluck`」へ。`inspect._flatten_record` に
   `_safe_column`（予約列リネーム・`asterism.tabularize` と同期）。propose §9 の JSON logicalSource／json_pluck
   の native 制約／fallback の「nested TriplesMap」例外を撤去し CSV+tabularize 前提へ。テスト更新済。
4. **✅ 予約列サニタイズの共通防御** — JSON（tabularize）に加え**直接 CSV 取り込みも対応**: substrate
   `sanitize_csv_sources`（CSV ヘッダに予約列があれば work-dir コピーで `safe_col` リネーム・無ければ no-op・
   行ストリームで memory-bounded）＋ inspect の CSV 列にも `_safe_column` 適用。生 CSV の `subject`/`predicate`
   列が静かに 0 triples になる latent bug を解消（e2e で確認）。
5. **✅ coverage 再計測（PR #192）**: 3 proposal を新 §9（CSV+tabularize）で再生成→`…Raw` 11.1%→**0.0%**、
   ゲート `DEFAULT_RAW_RATE_GATE` を **0.15→0.05** に締めた。検証レポート Addendum 参照。
6. **✅ MP 例 RML の CSV 移行（本 PR）**: `mp.rml.ttl` を `ql:CSV`＋`mp.csv` 参照へ移行（dot-path 参照は不変・
   iterator 削除）。substrate が `mp.json`→`mp.csv` を auto-tabularize＝**143 triples で JSONPath 出力と set 一致**
   （実 morph-kgc 確認）。最後の `ql:JSONPath` 使用が消え JSON 経路が1本に統一。JSON が citable な source of record。
7. **✅ `{key:[...]}` JSON の wrapped 配列**: `_load_records` が top-level dict から **record 配列を自動検出**
   （最長の array-of-objects 値・inspect の `_detect_iterator` と同流儀）＝`{"docs":[...]}`/`{"data":[...]}`
   のような API レスポンス形（OpenLibrary 実 API がこれ）を record_path なしで explode。substrate の
   auto-tabularize は record_path を渡さないのでこの自動検出が要だった。単一 object 文書は 1 行のまま（非回帰）。
8. **✅ inspect↔tabularize の drift 対策（依存結合でなく検証）**: `flatten_record`/`safe_col`/予約列集合は
   `asterism.tabularize` が**正準**だが、step0 は**ハード依存ゼロ設計**（inspect は stdlib のみ・ingest の
   rdflib/httpx/watchfiles を引かない＝設計原則）なので import 結合は不可。代わりに step0 が軽量ミラーを保持し、
   **skip-guard 付き等価テスト**（`step0/tests/test_inspect_tabularize_sync.py`・`pytest.importorskip`）が
   ingest 在席時に両者の一致を検証＝SSOT-by-verification。step0 CI ジョブに ingest を **test-time だけ**導入
   （pyproject 不変＝standalone 維持）。**棄却案**: step0→ingest 依存（zero-dep 設計違反）／共有 5th パッケージ
   （30行に過剰）。**判断: 軽量さ（プロダクト原則）を犠牲にせず drift（重複の唯一の実害）を消すのが理想**。

---

## 9. 残課題 / 未決

- **相関エンティティ再構成（side-table・案D）** は将来要件が出たら別 ADR（§5）。
- **`tabularize` のキー選択**: 親 IRI 用の自然キーは dataset 依存（crossref=DOI・github=full_name・
  openlibrary は安定キー希薄）。propose/step0 の主キー選定に委ねる（本 ADR では `rid` 行番号で実証）。
- **深い入れ子（配列の配列 `[[YYYY,M,D]]`）** の扱いは当面 dotted 列＋スカラ投影の範囲に留める（§5）。
- **自動 tabularize の発火条件**: 「JSON source は常に tabualize」か「明示宣言時のみ」か（§8-2 は前者を提案）。
