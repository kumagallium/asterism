# 表の形をととのえる — 配列セル・値としての物性名・入れ子 JSON を、設計の前に決定論で派生表にする

status: 提案（2026-09-03。判事 3 レンズの指摘を反映した第 2 稿）
owner: kumagallium
関連: [`source-dialect.md`](source-dialect.md)（読み方の宣言＝直接の前例）/ [`native-json-denormalization.md`](native-json-denormalization.md)（§5 で「並行配列の zip・相関エンティティの再構成」を Tier 0 の外と判定）/ [`kantan-mode-two-tier-ux.md`](kantan-mode-two-tier-ux.md)（K2 3 問・K6 1 シート=1 表・K17 1 工程=1 画面・K20 発明した名前を問わない・K44 人の裁定の台帳）/ [`skeleton-from-easy-judgments.md`](skeleton-from-easy-judgments.md)（7 段フロー・D2「外とのつながり」＝組成のような決まった書き方）/ [`column-ownership-and-growth.md`](column-ownership-and-growth.md)（G7 沈黙の条件）/ [`incremental-ingest.md`](incremental-ingest.md)（A6 スキーマ固定・A7 追記式永続化）/ [`ingestion-execution-safety.md`](ingestion-execution-safety.md)（§3 option 2 = 人が一度 vet した固定関数の閉集合）/ [`data-shape-checks.md`](data-shape-checks.md)

## 0. 問題 — 「1 行 = 1 記録」でない表は、宣言経路の手前で止まる

Starrydata の公開 CSV（2026-05-27 snapshot・曲線 233,103 本）で実測した 3 つの形:

| 形 | 実例 | 取り込むとどうなるか | なぜ RML / Mapping IR で解けないか |
|---|---|---|---|
| **配列セル** | `x` = `[299.8, 324.8, …]`、`y` = `[-0.000148, …]`（並行する 2 列を添字で対応づけて初めて「点」になる） | 文字列リテラル 1 個。数値比較も最大値も取れない | CSV セルの中を反復する iterator が無く、2 列を添字で zip する手段も無い（`native-json-denormalization.md` §5 が「閉じない」と判定した領域） |
| **値としての物性名** | `prop_y` に "ZT" / "Seebeck coefficient" … 169 種、単位は `unit_y` | ZT も Seebeck も同じ述語の文字列。`schema_summary` に物性が現れず、Ask は語彙 169 種を知らないと問えない | 値ごとの条件付きマップを 169 本書けば形式上は可能だが、設計の対象にならない |
| **入れ子 JSON** | `sample_info` = `{"MaterialFamily":{"category":"Bi2Te3","comment":""},…}`、`comments` = JSON 文字列の中に JSON（二重符号化） | 1 文字列。`WHERE` で中を見られない | `json_pluck` は固定キー 1 つの取り出し。キーの表記ゆれ（`" coercivity"`, `"Measurement temperature\t"`）と 60 種超の裾野は拾えない |

これらは**マッピングではなく reshape**（行と列の作り直し）であり、宣言経路の入口で表が「1 行 = 1 記録・1 セル = 値 1 つ」になっていることを前提にしている以上、入口の手前で整えるしかない。いまは手元の決定論スクリプト（`starrydata_dataset/tidy/build_tidy.py`・別名表 `aliases.csv`）が代行しているが、これは Asterism が持つべき機械処理を利用者の手作業に押し付けている。旧ハンドコード ingest（`ingest/src/asterism/starrydata.py`）はまさにこの 3 処理を Python でやっていた。それを「データセット固有コード」として降格させた以上、汎用の層として戻すのが筋である。

## 1. 責務の線 — 判断はデータ所有者、機械処理は Asterism

| 何を | 誰の責務 | 根拠 |
|---|---|---|
| **何を同じとみなすか**（`thermopower` は Seebeck か、`ohm^(-1)*m^(-1)` と `S*m^(-1)` は同じか、どの物性を表にするか） | データ所有者（人） | 事実の curation。LLM に推測させると「データの事実は不変」が崩れる。K44 と同じく人の宣言として台帳に残す |
| **形の検出・決定論の reshape・保存則・人への確認** | Asterism | 汎用の機械処理。Excel のシート分割（K6）、装置ファイルの dialect、`json_pluck` と同じ層 |
| **意味づけ**（骨格・ID・述語・単位の接地） | Asterism（既存・無改修） | 派生表は普通の表として②以降に流れる |

**LLM はこの層に一切関与しない。** 検出も既定の提案も適用も決定論で、人が変えられるのは判断表（どれを畳むか・どれを表にするか・どの列を持ち回るか）だけ。

## 2. 前例に乗せる — 3 つの既存の型

| 型 | 前例 | reshape での使い方 |
|---|---|---|
| 1 ファイル → 複数の派生表を staging に置き、人が使う表を選ぶ | K6: xlsx を `<stem>__<sheetslug>.csv` に展開し `POST /api/staging/{id}/sources` で選ぶ（`main.py` `_expand_xlsx_sheets` / `sheets` meta） | 派生表を同じ命名で staging に書き、`sheets` と同型の `reshape` meta に由来を残す。以降のウィザードは派生表を普通の表として読む |
| 人の裁定を台帳に永続化し、attach のたびに raw から再適用する | K44 `column-decisions.json`（`(source, column)` で upsert・再 attach で列集合と突き合わせ `stale` を無効化）・xlsx の `keep`（attach は staging の派生物を信用せず raw から再変換） | 判断表を `<id>/reshape.json` に永続化。attach は raw ＋ 台帳から派生表を**再生成**する |
| 生の入力を貯め、決定論変換は取り込み側で再生する | dialect（append は native のまま蓄積・正規化は取り込み時） | append バッチは raw のまま受け、台帳で派生バッチを作ってから表ごとの既存 append に流す |

派生表は **attach 時に source_dir に正本化**する（xlsx と同じ）。materialize 直前の work_dir 一時生成（dialect / JSON と同じ）にしないのは、読み取り確認・③項目の意味・④外とのつながり・⑤の数の確認・advisory がすべて「派生後の形」を見る必要があるから。

## 3. 決定

| # | 論点 | 決定 | 理由 |
|---|---|---|---|
| R1 | 層の位置 | `ingest/src/asterism/reshape.py` に**検出・提案・適用・保存則**を持つ決定論の純関数群を置く。api が staging（①）・attach・append で呼ぶ。step0 には依存させない | tabularize.py（xlsx→CSV）と同じ層・同じ呼ばれ方 |
| R2 | 操作の閉集合 | `explode`・`pivot`・`flatten` の **3 つだけ**。新しい操作は人が vet して足す | `ingestion-execution-safety.md` §3 option 2（固定関数の閉集合＋宣言データ）。Tier 0 がセル変換、reshape は行×列の再構成 |
| R3 | 宣言の形 | `ReshapeSpec`（JSON, §4.0）。op ごとに入力表・出力表名・列名・判断表（別名・単位・持ち回り列・凍結したフィールド）・読み方（dialect）を持つ。適用のたびに `counts` を記録 | 判断表がそのまま宣言＝K44 と同じ「人の宣言が台帳」 |
| R4 | 検出は沈黙が既定 | 証拠が揃ったときだけ提案する。判定は**ファイル全体から等間隔に取った 20,000 行**（行数を数えてから stride = ⌈行数/20,000⌉ で拾う。決定論）で行う。**配列セル**: 非空セルの全部が JSON の数値配列。並行列は行ごとの長さが 95% 以上一致。**値としての物性名**: 文字列列の distinct が 2〜200 かつ 1 回しか現れない値の行が 60% 以下で、**単位らしき列がラベルに関数従属**（ラベルの 90% 以上が単位 1 つ）し、値列（配列または数値）が同じ行にある。partner（もう 1 組のラベル＋単位＋値）は distinct が 1 でもよい（x 軸が全部 Temperature でも partner）。distinct が 200 を超えたら「多すぎる」として黙る。**入れ子 JSON**: 非空セルの 90% 以上が JSON オブジェクト（JSON 文字列を 2 段までほどく）。キーが 1 種類だけで値がスカラでもオブジェクトでもない（配列だけ）なら黙る | G7「証拠が無ければ黙る」。先頭だけの接頭辞は分布が偏る（Starrydata は先頭 20,000 行が熱電だけで `prop_x` が 1 種）。単位列の従属を要求するのは、測定の long 表には必ず単位列があり、ただのカテゴリ列（`category` + 数値列）を誤爆しないため |
| R5 | 既定の提案は綴りだけ畳む | 既定の判断表は**全行**のラベル・単位列を走査して作る。群は**空白正規化＋大文字小文字**の同一視だけで作り、群の代表表記は最頻の綴り、群の単位は最頻の単位 1 つ。同数の tie は**ファイル内で先に現れた方**。`thermopower`→Seebeck のような語の同一視、`ohm^(-1)*m^(-1)`＝`S/m` のような単位の同一視、`T`→Temperature のような略記は**人が足す**。群には `enabled` があり、既定で有効なのは**行数上位 12 群**（同数は初出順）。残りは判断表に載るが表は作らず元表に残る。人が編集した判断表は機械の既定を**置き換える**（既定は種にすぎない） | 語の同一視は curation（§1）。169 種すべてに表を作ると裾野の表が 5 行単位で 150 枚並ぶ。tie-break と母集団を決めないと決定論にならない |
| R6 | 単位が違う行は畳まない | 群に入る行は（ラベル, 単位）の対が判断表にあるものだけ。**同じ (ラベル, 単位) は高々 1 つの群にしか属せない**（読み込み時に重複を拒否）。単位の違う行は元表に残るだけで、型付き表には入らない | 値を変えない。二重計上を許すと保存則が壊れる |
| R7 | 裾野は元表に残す | pivot は `enabled` な群の表だけ作る。どの群にも入らない行・無効な群の行は元表に残り、消えない。全ラベルを 1 つの long 表に展開する単独の `explode` は、同じ配列を pivot が使うときは提案しない（人のオプトイン） | 電池・磁性など 84,000 曲線を EAV で展開しても「値としての物性名」が復活するだけ。480 万行の表を黙って作らない |
| R8 | 持ち回り列 | 派生表は入力の **ID らしい列**（名前が `id` / `sid` / `key` / `index` / `no` で終わる）と、**決まった書き方の列**（文字列列で、非空値の 90% 以上が空白を含まず 40 文字以内。組成・DOI・図番号がこれ）を持ち回る。op が消費する列（配列・ラベル・単位・値・JSON）と、JSON の配列・オブジェクトが入った列（`project_names` のような `["…"]`）は除く。合計が 12 列を超えたら ID らしい列だけにして助言を出す。判断表に「追加で持ち回る列」を専用の行として出す | ④「外とのつながり」（D2）は組成のような決まった書き方の列を候補に見る。派生表にその列が無ければ判断のしようがない |
| R9 | 列名と出自 | pivot の値列はラベルの slug（`zt`, `seebeck_coefficient`）、partner 列は partner ラベルの slug（`temperature`）。単位は列名に入れず、派生表の**列メタ**（`columns[].unit`）に残す。列メタには**出自**（`origin`: 「もとの表で prop_y = "ZT" だった行から」＋畳んだ表記の一覧）も必ず持ち、③項目の意味の当該行に 1 行で添える | K13 の「名前は機械が導く」。K20: 機械が付けた名前を人に問うときは出自を必ず示す |
| R10 | 表名 | `<stem>__<slug>.csv`（xlsx の `<stem>__<sheetslug>.csv` と同じ）。flatten の wide は `<stem>__<colslug>-wide.csv`。衝突は名前のハッシュで解く | 既存の命名規約を増やさない |
| R11 | 保存則 | 適用のたびに op ごとの `counts` を計算し、次の式を検査する。崩れたら 422 で何も書かない。**explode**: `elements_in = rows_out + dropped_non_numeric + truncated_length_mismatch`、`parent_rows_in = source_rows`。**pivot**: `source_rows = Σ群 rows_matched + rows_unmatched`、`elements_matched = Σ表 rows_out + dropped_non_numeric + truncated_length_mismatch`。**flatten(long)**: `entries_in = rows_out + entries_empty`。**flatten(wide)**: `rows_out = source_rows`、`wide_key_collisions` を数える（黙って上書きしない） | reshape のバグは行を落とす／二重にする形で出る。式が op ごとに無いと検査できない |
| R12 | 画面 | ①「入れる」の読み取り確認の**直後・②「AI が読んでいます」の前**に、検出があるときだけ「表の形」の画面を出す（検出が無ければ出ない＝K2 の 3 問は増えない。dialect の読み取り設定パネルと同じ位置づけ）。**検出された op ごとに 1 タブ**（1 タブ 1 判断。タブ自体が ⚠/✓ を持ち、未判断をタブの裏に隠さない）、上部に常時「入力 N 行 → 派生表 M 行・捨てた要素 K 個・切った要素 L 個」の帯。①の任意パネル（dialect の読み取り設定 → preamble の命名 → 表の形）は出るものだけがこの順で別画面として続く。タブの中は判断表（群の一覧: 表記・単位・行数・使う/使わない・「同じとみなす」で別群へ合流／追加で持ち回る列／wide にする項目）。判断表を変えると同じ画面で再適用して帯が更新される。「ととのえて進む」で②へ、「このまま進む」で reshape 無しで②へ | K17（1 工程 = 1 画面）と D2（1 画面 1 判断）に合わせる。②より前でないと AI の下書きが生の `prop_y`/`x`/`y` に対して走り無駄になる |
| R13 | 永続化と再生 | **staging**: `meta.json` に `reshape`（spec・派生表の由来・counts・列メタ）。派生表は staging に書き、`sources` は派生表に置き換わる（raw の表は「使わない表」として残る）。**attach**: `_persist_source_uploads` が raw を書き直した**後**に、R14 の失効判定を通した op だけで台帳から派生表を再生成して source_dir に書く（順序: rmtree → raw 書き直し → 失効判定 → 再生成。xlsx の `keep` と同じく staging の派生物は信用しない）。台帳は `<id>/reshape.json`（`source/` の**外**。rmtree の対象外）。**append**: バッチ名が台帳の raw ソース名に一致すれば、台帳で派生バッチを作ってから表ごとの既存 append に流す。pivot の群と flatten のフィールドは閉集合なので、未知のラベル・未知のフィールドは元表／`value_json` にしか落ちず、**派生表のスキーマは変わらない**（A6） | K44 と dialect と xlsx の合成 |
| R14 | 失効判定 | attach と append のたびに op を実際の表と突き合わせる: op が参照する列（配列・ラベル・単位・値・partner・JSON・持ち回り）が無い、または R4 の検出条件を満たさない op は**無効化**して助言（`reshape.op_stale`）で知らせる。無関係なデータに古い台帳を黙って当てない | column-decisions の `stale_decisions` と同じ |
| R15 | 読み方との順序 | reshape は常に **dialect を通した行**を読む（`asterism.dialect` の行 iterator）。①では検出した dialect、attach/append では op に pin した dialect。派生表は UTF-8 カンマ CSV（既定 dialect）で書くので Mapping IR の `dialects:` に派生表は現れない。順序は raw → dialect → reshape → 設計/RML | CP932・タブ区切りの raw に対して JSON 検出が誤動作しないため |
| R16 | 数値の写し | 配列の数値は **JSON の元トークンをそのまま**書く（`parse_int=str, parse_float=str`）。float を経由しない。数値かどうかの判定だけ float で行う | 20 桁の整数（キャリア濃度）が `9.689579e+19` に化けた実測（`tidy/REPORT.md`）。値を変えない |
| R17 | 復元 | ウィザードの snapshot（sessionStorage）には判断表だけを持つ（`columnMeanings` と同じ枠）。派生表はサーバの staging にある | `source-staging.md`: ブラウザに File は置けない |
| R18 | 公開後の見直し | カタログの「見直す」から「表の形」に着地できる（③に着地する前例と同じ）。台帳を編集したら raw から派生表を再生成して設計に戻る。append で群に入らない行の割合が閾値（5%）を超えたら助言（`reshape.unmatched_growth`）で「群を見直す価値」を知らせる。助言は出すが黙って群を増やさない | K44 の再入編集と同じ。沈黙（G7）は検出ノイズのためであり、古くなった台帳を黙認する理由にならない |
| R19 | 詳細モード | Workbench の「ソース」に reshape の spec（JSON）と counts を**読める形**で出し、詳細モードでは spec の生 JSON を編集して再適用できる。検出は `/api/inspect` の応答にも `reshape` として載せ、advisoryPlain の marker で平易化する | K1（詳細モードで機能を削らない）と K4（専門表記の逃がし先）。data-shape-checks の D5 は受動的な検査表示の規約で、能動的な判断には当てはまらない |
| R20 | やらないこと | 換算（抵抗率↔伝導率）・補間・外れ値除去・LLM による別名推定・群の自動追加・pivot 群の自動命名以上の意味づけ | 値を変える操作はこの層に置かない。意味づけは既存の層 |
| R21 | 見直し（redesign） | raw を再アップロードしない見直しでは台帳は不変（派生表もそのまま）。raw を差し替えたら attach と同じく R14 の失効判定→再生成を通す | 判断は人のもの。機械が黙って捨てない |

## 4. 各操作の定義

### 4.0 ReshapeSpec

```json
{
  "version": 1,
  "ops": [
    {"kind": "explode", "source": "curves.csv", "dialect": {}, "table": "curves__points.csv",
     "arrays": ["x", "y"], "index": "point_index", "carry": ["SID", "figure_id", "sample_id", "DOI", "composition"]},
    {"kind": "pivot", "source": "curves.csv", "dialect": {},
     "explode": {"arrays": ["x", "y"], "index": "point_index"},
     "carry": ["SID", "figure_id", "sample_id", "DOI", "composition"],
     "label": "prop_y", "unit": "unit_y", "value": "y",
     "partner": {"label": "prop_x", "unit": "unit_x", "value": "x"},
     "groups": [
       {"slug": "zt", "label": "ZT", "unit": "-", "table": "curves__zt.csv", "enabled": true, "rows": 20684,
        "members": [{"label": "ZT", "unit": "-", "rows": 20684}],
        "other_units": [],
        "partner": {"slug": "temperature", "label": "Temperature", "unit": "K",
                    "members": [{"label": "Temperature", "unit": "K"}, {"label": "T", "unit": "K"}]}},
       {"slug": "electrical-conductivity", "label": "Electrical conductivity", "unit": "ohm^(-1)*m^(-1)",
        "table": "curves__electrical-conductivity.csv", "enabled": true, "rows": 13871,
        "members": [{"label": "Electrical conductivity", "unit": "ohm^(-1)*m^(-1)", "rows": 13871}],
        "other_units": [{"label": "Electrical conductivity", "unit": "S*m^(-1)", "rows": 7214}],
        "partner": {"slug": "temperature", "label": "Temperature", "unit": "K",
                    "members": [{"label": "Temperature", "unit": "K"}]}}
     ]},
    {"kind": "flatten", "source": "samples.csv", "dialect": {}, "column": "sample_info",
     "carry": ["SID", "sample_id"],
     "long": {"table": "samples__sample_info.csv", "fields": ["category", "comment", "extracted"]},
     "wide": {"table": "samples__sample_info-wide.csv", "keys": ["MaterialFamily", "Form"], "fields": ["category"]}}
  ],
  "tables": {
    "curves__zt.csv": {"from": "curves.csv", "op": 1,
      "columns": [{"name": "temperature", "unit": "K", "origin": "prop_x = Temperature, T"},
                  {"name": "zt", "unit": "-", "origin": "prop_y = ZT"}]}
  },
  "counts": {
    "0": {"source_rows": 233103, "elements_in": 4802978, "rows_out": 4802978, "dropped_non_numeric": 0, "truncated_length_mismatch": 0},
    "1": {"source_rows": 233103, "rows_matched": 147088, "rows_unmatched": 86015, "elements_matched": 2603946, "tables": {"curves__zt.csv": 285296}, "dropped_non_numeric": 0, "truncated_length_mismatch": 0},
    "2": {"source_rows": 104846, "entries_in": 505373, "rows_out": 375159, "entries_empty": 130214, "wide_rows_out": 104846, "wide_key_collisions": 0}
  }
}
```

`dialect` は op が読む raw の読み方（`source-dialect.md` の 6 フィールド。既定は `{}`）。`tables` と `counts` は適用が書く。判断表（`carry` / `groups` の `enabled`・`members`・`partner.members` / `long.fields` / `wide.keys`）だけが人の編集対象。`other_units` は同じ物性で単位の綴りが違う行（R6 で畳まれなかったもの）を人に見せるための候補で、人が `members` に移して初めて畳まれる。`rows` は全行走査の一致行数（ゲートの表示用）。

### 4.1 explode

入力: 表 T、配列列 A₁..Aₙ（並行）、持ち回り列 C、添字列名 i。
出力: 行ごとに zip(A₁..Aₙ) を展開した (C…, i, A₁, …, Aₙ)。行ごとに L = max(len(Aₖ))、M = min(len(Aₖ)) とし、添字 ≥ M は `truncated_length_mismatch` に、添字 < M で数値でない要素を含む組は `dropped_non_numeric` に数える。数値は元トークン（R16）。

### 4.2 pivot

入力: 表 T（配列列を含んでよい。その場合 `explode` を内包）、ラベル列 L、値列 V、単位列 U（任意）、partner（L′, V′, U′。x 軸のようにもう 1 組の「ラベル＋値」がある場合）、群 G。
出力: 群ごとに表。行は (L, U) ∈ members かつ（partner があれば）(L′, U′) ∈ partner.members のもの。列は C…, i, slug(partner.label) ← V′, slug ← V。既定の群は R5。既定の partner は**その群の members に一致する行だけ**を母集団にした最頻の (L′, U′)（tie は初出順）。

### 4.3 flatten

入力: 表 T、JSON オブジェクト列 J（JSON 文字列を 2 段までほどく）、持ち回り列 C、long（表名・凍結フィールド F）、wide（表名・凍結キー K・凍結フィールド F′）。
出力（long）: (C…, key, key_raw, value, F の各列, value_json)。`key` は空白正規化（前後を落とし、連続空白を 1 つに。**大文字小文字は変えない**＝人の付けたキーの綴りを保つ）、`key_raw` は元。値がスカラなら `value`、オブジェクトなら F にある field を列に、F に無い field は `value_json` に JSON のまま。値が空（"", "{}", "[]", null）のエントリは `entries_empty`。スカラ値・field 値も R16 と同じく元トークンのまま（オブジェクトも `parse_int=str, parse_float=str` で読む）。
出力（wide）: T の写し + K の各キーについて、スカラなら `<key>`、オブジェクトなら F′ の各 field を `<key>__<field>`。K に無いキーは列を作らない。K の選定と列名は long と同じ空白正規化を通した `key` で行う。正規化後に同じ `key` になる生キーが複数あれば（`" coercivity"` と `"coercivity"`）同じ列に入り、1 行の中で 2 つの生キーが同じ列を取り合ったら**初出の生キーが勝ち**、負けた側は long にだけ残して `wide_key_collisions` に数える。列名が既存列（T の列）と衝突したら `<key>__1` のように連番で逃がす。
既定: long は常に（F = 先頭 20,000 行で見えた field の充足率順・上位 8）、wide は充足率 25% 以上のキー上位 12 個（F′ = 各キーで最頻の field 1 つ）。充足率・頻度の同数 tie は**先頭 20,000 行の中での初出順**（R5 と同じ）。F・K・F′ は提案時に一度だけ計算して spec に**凍結**する（A6）。F′ を 1 つに絞るのはトレードオフで、選ばれなかった field は wide 表には現れない（long と raw には残るので保存則は破れない）。

母集団の使い分け: **検出（R4）と flatten の既定（F・K・F′）は等間隔の 20,000 行**（提案は速く、どのキーが落ちても long と raw に残る）。F′ は選ばれたキー全体で最頻の field 1 つ（キーごとに変えない。spec の `wide.fields` は 1 本のリスト）。**pivot の既定の群（R5）は全行**（稀な綴りも判断表に載せないと人は畳めない）。**適用（R11）は常に全行**。

## 5. 段階と受け入れ条件

| 段階 | 実装単位 | 受け入れ条件 |
|---|---|---|
| ① 検出と助言 | `asterism.reshape.detect()`・`propose()`・`/api/inspect` の `reshape`（ヘッダ `X-Asterism-Reshape`）・advisoryPlain の marker と i18n | 生の `starrydata_curves.csv` で配列セル（x, y）と値としての物性名（prop_y / unit_y、partner prop_x / unit_x）が、`starrydata_samples.csv` で入れ子 JSON（sample_info）が検出される。単位列の無いカテゴリ列（`project_names` 等）では黙る。同じ入力から同じ提案 |
| ② 決定論 reshape | `ReshapeSpec` の検証・`apply()`・保存則・`POST /api/staging/{id}/reshape`（spec を受けて適用し meta と sources を更新）・attach の再生成と `reshape.json`・append の再生と失効判定 | 生の 3 CSV から、`build_tidy.py` の `points_zt.csv` 等と（持ち回り列を除き）行数・値が一致する派生表が出る。20 桁の整数が壊れない。保存則が通る。append バッチに未知のラベルを混ぜても派生表のスキーマが変わらない。列を欠く表に古い台帳を当てると op が無効化され助言が出る |
| ③ ゲート | KantanWizard の「表の形」画面（タブ・帯・判断表）・snapshot・見直し着地・Workbench のソース表示・manual | ①に生の 3 CSV を置くと「表の形」に検出 3 件と既定の判断表が出て、「ととのえて進む」で②以降が派生表で進む。③項目の意味に派生列の出自が 1 行で出る。判断表の編集が staging と snapshot に残る |

## 6. 残る問い

- pivot の群を「物性 × 単位」でなく「物性 × 単位 × partner」まで既定で分けるべきか（いまは partner は最頻 1 組）。
- `reshape.json` を Mapping IR の `reshapes:` にも写すか（snapshot exchange で IR だけ持ち出す場合）。当面はデータセット台帳のみで、exchange は台帳ファイルごと運ぶ。
- R8 の「決まった書き方」ヒューリスティック（空白なし・40 文字以内）が、`Bi2Te3-xSex (x=0.1)` のように空白や括弧を含む組成表記でも組成列を拾えるか。①の受け入れ確認で実データの適合率を見る。
