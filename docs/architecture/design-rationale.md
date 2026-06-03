# Phase 1 設計判断の記録 (design rationale)

このドキュメントは Phase 1 (2026-05-27〜28) で AI agent (Claude Code, Opus 4.7) と人間 (kumagallium) が **co-design** で出した設計判断を、判断時点の根拠とともに記録したものです。Phase 2 以降で「なぜそうなっているのか」を辿れるようにし、また Phase 3 の AI-assisted builder が **同じ判断を再現**できるよう、各決定に `Decision / Why / Alternatives / Trade-offs / Re-evaluation triggers` の 5 項目を併記しています。

ドキュメントの位置づけ:

- [`ai-assisted-step0-workflow.md`](ai-assisted-step0-workflow.md) は「**プロセス**」を整理した
- [`ai-assisted-step0-prompts.md`](ai-assisted-step0-prompts.md) は「**台本**」を整理した
- 本ファイルは「**判断の中身**」を retrospective に整理する

---

## 1. IRI 命名 — 複合キー (composite IRI)

### Decision
`sd:Sample` / `sd:Curve` / `sd:Descriptor` の IRI は **複合キー**で構築する:

- `sdr:sample/{SID}-{sample_id}`
- `sdr:curve/{SID}-{figure_id}-{sample_id}`
- `sdr:descriptor/{SID}-{sample_id}/{idx}`

元の `sample_id` / `figure_id` は `sd:rawSampleId` / `sd:rawFigureId` として **残す** (上流 CSV との突き合わせのため)。

### Why
Phase 1 initial 実装で `sdr:sample/{sample_id}` (単独キー) を使ったところ、Dify 経由 SPARQL で **9,661 sample / 4,466 curve が collision** することが発覚 (PR #8 で fix)。starrydata の `sample_id` / `figure_id` は **paper 内 unique** だが **globally unique ではない**。

`(SID, sample_id)` で 0 collision、`(SID, figure_id, sample_id)` で curve も 0 collision を 全件検証で確認。

### Alternatives
- **A. UUID** (`sdr:sample/<uuid>`): collision 完全回避だが、上流 CSV との対応が壊れる (re-ingest 時に別 UUID が振られる)
- **B. hash** (`sdr:sample/<sha1(SID + sample_id)>`): re-ingest 冪等性は確保できるが、人間が IRI を見ても元の値を辿れない
- **C. 複合キー** (採用): 上流対応 + 冪等性 + 人間可読性 すべて満たす

### Trade-offs
- IRI 文字列が長くなる (15〜30 文字)。Oxigraph のストアサイズに微増だが、starrydata 全件 (~1M IRI) でも数十 MB の増加で済む
- 上流 CSV で `sample_id` だけ知っている状況で IRI を構築するには `SID` が必要。これは `sd:fromPaper` で逆引きできる

### Re-evaluation triggers
- starrydata の上流が `sample_id` を globally unique にした (運用上は変えにくいので可能性は低い)
- 別 dataset で「SID 相当の paper 識別子が無い」場合は、その dataset の独立な ID を活かせるか再評価

### Cross-refs
- [`ai-assisted-step0-workflow.md` §6 罠 1](ai-assisted-step0-workflow.md#6-ai-が間違いやすいポイント-phase-1-で実観測)
- [`../ontology/starrydata.ttl`](../ontology/starrydata.ttl) line 215+ (`sd:rawSampleId` / `sd:rawFigureId`)
- [`../../ingest/src/asterism/starrydata.py`](../../ingest/src/asterism/starrydata.py) line 313, 382

---

## 2. blank node 不使用 (bnode-free)

### Decision
`Periodical` / `Person` / `Descriptor` / `IngestionActivity` / `source` を含む **全 entity に stable な IRI** を割り当てる。rdflib の `BNode()` は呼ばない。

### Why
Oxigraph (SPARQL store) は **set semantics** で、同じ IRI を持つ triple は再 INSERT しても 1 件のまま。これにより:

- watcher が同じ CSV を再投入しても triple が **重複しない** (Phase 2 の核心)
- 部分更新を `INSERT DATA` で安全に流せる

一方 blank node は INSERT のたびに新しい `_:b1234` が振られ、Oxigraph では **別 entity 扱い**で重複が積み上がる。Phase 0.5 ベンチで 100 paper × 3 bnode = 300 triple の重複を観測 ([phase05-decisions.md §2.2](phase05-decisions.md#22-oxigraph))。

### Alternatives
- **A. blank node + skolemization**: 標準化されているが Oxigraph が自動 skolemize しないので結局自前で IRI 命名と同じ
- **B. SPARQL Update で DROP + INSERT**: 全件 drop は重い、部分 drop は対象 IRI が必要 (それなら最初から IRI で命名)
- **C. IRI 命名** (採用): 直接 set semantics に乗る

### Trade-offs
- Periodical / Person / Descriptor の **slug** を作る必要 → `slugify()` helper を実装
- "anonymous" な entity (例: 著者の所属機関で氏名しか無いケース) を扱いにくくなる → starrydata では発生せず

### Re-evaluation triggers
- Oxigraph の skolemization が auto enabled になった
- 別 dataset で「IRI 命名のための安定キーが本当に取れない entity」が出てきた

### Cross-refs
- [`phase05-decisions.md` §2.2](phase05-decisions.md#22-oxigraph) — 検証根拠
- [`option-b.md` §1](option-b.md#1-全体図) — re-ingest フロー

---

## 3. Curve x/y の表現 — 方針 C (JSON literal + 集約値)

### Decision
`sd:Curve` の x / y 配列は以下の **2 重表現**で保持:

- `sd:xValuesJSON` / `sd:yValuesJSON` (xsd:string) ... 元の JSON 配列をそのまま literal で
- `sd:xMin` / `sd:xMax` / `sd:yMin` / `sd:yMax` (xsd:double), `sd:pointCount` (xsd:integer) ... 集約値を別 triple で

### Why
starrydata の curves.csv は 233,104 行 (Phase 1 smoke-test は 2,457)、1 curve あたり 5〜数百点。全件で `sd:DataPoint` ノード化すると数千万 triple になり、Oxigraph のストアと検索性能の両方を圧迫する。

集約値で「Bi2Te3 系で Seebeck が最大の curve」のような **粗い範囲クエリ**には答えられる。「300-400 K 領域で y がピークの curve」のような **2 次元局所範囲クエリ**は集約値だけでは無理 — その時は MCP tool (`template_curve_fetch`) で curve 全体を取り出して client side で展開する戦略。

### Alternatives
- **A. `sd:DataPoint` 個別ノード化**: 検索性能 ◎、ストア肥大 ✗ (Phase 1 では不採用)
- **B. JSON literal のみ**: ストア最小、範囲クエリは MCP tool 経由のみ ✗ (粗い範囲検索が一切できない)
- **C. JSON literal + 集約値** (採用): ストアは 1.x 倍程度、粗い範囲クエリは answerable、詳細は MCP tool

### Trade-offs
- ★ **既知の限界**: "Seebeck で 300-400 K 領域に局所最大が出る curve" のような 2 次元の局所範囲クエリは集約値だけでは答えられない
- 集約値が 5 個 (xMin / xMax / yMin / yMax / pointCount) 余計に triple として出る → 1 curve あたり 5 triple 増、233k curve 全件で +1.1M triple (許容範囲)
- ストア肥大は xValuesJSON / yValuesJSON の string size が主因 (1 curve 数 KB)。Phase 1 smoke-test 49,449 triple では問題なし、全件で再ベンチ予定

### Re-evaluation triggers
- starrydata 全件投入後、Oxigraph のストアサイズが数 GB を超え、SPARQL レスポンスが p95 100ms を超える
- 利用者が 2 次元局所範囲クエリを多用するユースケースが見えた
- xValuesJSON / yValuesJSON を別 endpoint (S3 等) に逃がす設計が現実的になった

### Cross-refs
- 内部設計プラン §4 「x/y 配列の表現方針」 (方針 A / B / C 比較) — repo 外の `asterism_design_plan.md`
- [`../../data/togomcp/mie/starrydata.yaml`](../../data/togomcp/mie/starrydata.yaml) `anti_patterns` セクション

---

## 4. PROV-O IngestionActivity (per-CSV-file)

### Decision
`ingest_papers` / `ingest_samples` / `ingest_curves` の **各 run で 1 つ**の `sd:IngestionActivity` (`prov:Activity`) を発行し、その run で生成された全 entity に `prov:wasGeneratedBy <activity>` を付ける。

```
sdr:ingestion/run-20260528T064500Z
    a sd:IngestionActivity, prov:Activity ;
    prov:atTime "2026-05-28T06:45:00Z"^^xsd:dateTime ;
    prov:used sdr:source/starrydata_papers.csv ;
    prov:wasAssociatedWith <https://github.com/kumagallium/asterism> .
```

3 つの CSV を順に投入すると **3 つの IngestionActivity** が並ぶ。

### Why
- 設計プラン §4 で PROV-O 中心の方針が確定済
- "この curve はいつ・どの CSV から投入されたか" が SPARQL 1 クエリで取れる (MIE の query example 5)
- 同じ CSV を再投入した場合、新しい `IngestionActivity` IRI が振られ (run_id にタイムスタンプ)、**古い活動の triple も残る** → "ある時点で何が読み込まれていたか" の audit に使える

### Alternatives
- **A. CSV 単位ではなく "row 単位" の Activity**: 細かすぎて triple 数が爆発 (papers 56k + samples 144k + curves 233k = 数十万 Activity)
- **B. CSV 単位ではなく "1 つだけ" の Activity (asterism の永続的 agent)**: re-ingest を区別できない、来歴が事実上消える
- **C. per-CSV-file-per-run** (採用): 粒度として "CSV のロード単位" は人間が理解しやすく、SPARQL でフィルタしやすい

### Trade-offs
- 1 run あたり Activity 1 つ + source 1 つ = 2 IRI 増。これは無視できる規模
- Activity の `prov:endedAtTime` は finally で必ず書く (途中エラーでも) → 部分 ingest の Activity が残ることがある (Phase 2 で UI 表示時に注意)

### Re-evaluation triggers
- 利用者が "row レベル" の来歴を求めた (例: 1 paper を 2 段階に分けて投入したケース)
- WebPlotDigitizer 由来の `sd:DigitizationActivity` を導入したい (Phase 2 候補)

### Cross-refs
- [`../ontology/starrydata.ttl`](../ontology/starrydata.ttl) line 60+ (`sd:IngestionActivity`)
- [`../../ingest/src/asterism/starrydata.py`](../../ingest/src/asterism/starrydata.py) `_emit_ingestion_activity`
- [`../../data/togomcp/mie/starrydata.yaml`](../../data/togomcp/mie/starrydata.yaml) query example 5

---

## 5. MIE keywords / categories — synonym を多めに

### Decision
`data/togomcp/mie/starrydata.yaml` の `schema_info.keywords` に **30+ 個**の synonym を登録 (英 + 日 + 代表組成式):

```yaml
keywords:
  - starrydata
  - thermoelectric
  - Seebeck
  - Seebeck coefficient
  - thermopower
  - 熱電
  - 熱電材料
  - ゼーベック係数
  - 熱起電力
  - Bi2Te3
  - PbTe
  - SnSe
  - half-Heusler
  # ... 計 30+
categories:
  - materials
  - materials-science
  - physics
  - thermoelectric
  - battery
  - magnetic
  - measurement-curves
  - provenance
```

### Why
Phase 1 後の実 AI 検証 (Dify, Crucible 経由) で、**Claude が `find_databases("thermoelectric")` で starrydata を発見できない** → PubMed の abstract に逃げて hallucinate する事象を観測した。

togomcp 内部 ([`rdf_portal.py`](https://github.com/dbcls/togomcp/blob/main/togomcp/rdf_portal.py) line 340 / 377) は `schema_info.categories` と `keywords` を `find_databases` / `list_categories` の検索対象として読む。**ここに synonym が無いと AI に到達不能**。

特に **日本語 keywords** は学習データの偏りを補正する効果が大きい (ユーザの自然な質問は日本語混じり)。

### Alternatives
- **A. keywords を 5-10 個に絞る**: 学術的には適切だが AI 到達性が下がる
- **B. 全文検索 backend を入れる**: Phase 3 候補。Phase 1 では keywords で十分
- **C. 過剰登録** (採用): false-positive が出ないユースケースなら冗長な方がよい

### Trade-offs
- 別 dataset が同じ keyword を持つと find_databases が複数 hit する → 現状 starrydata 1 個しか無いので問題なし。Phase 3 で複数 dataset を扱うようになると、keywords の disambiguation 戦略が必要

### Re-evaluation triggers
- 複数 dataset を asterism に乗せて keywords が衝突しはじめた
- togomcp が全文検索 / vector search を export するようになった
- 別の発見経路 (例: SPARQL VOCAB / VoID description) が一般化した

### Cross-refs
- [`ai-assisted-step0-workflow.md` §6 罠 4](ai-assisted-step0-workflow.md#6-ai-が間違いやすいポイント-phase-1-で実観測)

---

## 6. utf-8-sig による CSV BOM 対処

### Decision
ingester は CSV を `open(..., encoding="utf-8-sig")` で開く。引数化はせず default に。

### Why
starrydata の `starrydata_samples.csv` / `starrydata_curves.csv` は **UTF-8 BOM 付き** で出荷されている。`utf-8` で開くと最初の列名が `"﻿SID"` になり、`DictReader` で `row["SID"]` を引くと **常に空文字** → 全行が SID 不在で skip される。

`utf-8-sig` は BOM があれば剥がし、無ければそのまま読むので **副作用なし**。

### Alternatives
- **A. `chardet` で自動判定**: 重い + 大 CSV では遅い + 必要ない
- **B. BOM チェックを別関数で呼ぶ**: 1 行増えるだけだが忘れやすい
- **C. utf-8-sig を default** (採用): 副作用なしの安全側 default

### Trade-offs
- "厳密に utf-8 で読みたい" ニーズがあれば config で上書き必要 → 現状そのニーズなし

### Re-evaluation triggers
- CSV 以外のエンコーディング (Shift_JIS 等) のソースを扱う必要が出た

### Cross-refs
- [`ai-assisted-step0-workflow.md` §6 罠 2](ai-assisted-step0-workflow.md#6-ai-が間違いやすいポイント-phase-1-で実観測)
- [`../../ingest/src/asterism/starrydata.py`](../../ingest/src/asterism/starrydata.py) `open(..., encoding="utf-8-sig")`

---

## 7. IRI host — GitHub Pages dereferenceable

### Decision
Phase 1 の IRI prefix を `https://kumagallium.github.io/asterism/starrydata/...` に確定。`#iri=...` を解決する HTML landing を `docs/starrydata/ontology/index.html` で立て、`sd:` / `sdr:` namespace が dereferenceable になるように。

### Why
- "RDF resource は IRI を URL として叩くと description が返る" は LOD のお作法 (Linked Data principles)
- GitHub Pages は無料 / 永続的 / 我々の GitHub アカウントで自己完結
- WebVOWL や Protégé が IRI を直接読めるようになる

### Alternatives
- **A. localhost prefix**: 開発時のみ実用、外部公開不可
- **B. w3id.org redirect**: 永続性最強、PR 必要 (Phase 2 以降)
- **C. GitHub Pages** (採用): Phase 1 で最小コスト、Phase 2 で w3id に migrate 可能

### Trade-offs
- ユーザ名 `kumagallium` が IRI に焼き付くので、organization に移管すると 301 redirect が必要 (GitHub Pages は柔軟に対応)
- starrydata は dataset name にも IRI にも入っており、別 dataset が増えると prefix tree が `asterism/{dataset}/...` に分岐する

### Re-evaluation triggers
- Phase 2 で `w3id.org/asterism/...` 申請が通った
- 別 organization への移管が決まった

### Cross-refs
- 内部設計プラン §4.0 「IRI 命名と所有」 — repo 外の `asterism_design_plan.md`
- [`../starrydata/ontology/index.html`](../starrydata/ontology/index.html) — dereferenceable landing

---

## 8. Descriptor 空エントリの drop

### Decision
samples.csv の `sample_info` JSON 列を parse するとき、`{category: "", comment: "", extracted: ""}` のように **全フィールド空**のエントリは Descriptor として **出力しない**。

### Why
starrydata の curator UI は Descriptor のテンプレ (例: `{MaterialFamily, Form, FabricationProcess, ThermalMeasurement, ...}` の 12 種) を空のまま保存することが多く、CSV 上で大量の "空 Descriptor" が並ぶ。これを愚直に triple 化すると Descriptor ノードが膨大に増え、`sd:hasDescriptor` の cardinality も歪む (実際には 1-3 個でいいところに 12 個全て出る)。

S/N 比を上げるため ingest 時に drop。

### Alternatives
- **A. 全エントリ triple 化**: 検索ノイズで使い物にならない
- **B. CSV 上の "テンプレ汚染" を上流で fix**: starrydata 側に依存、現実的でない
- **C. ingest 時 drop** (採用): asterism 側で完結、現実的

### Trade-offs
- "空 Descriptor が CSV 上にあった事実"そのものが triple として残らない → CSV と RDF の片方を見て他方を再現することが不可能 (片方向変換)。これは Phase 1 で許容
- 何が drop されたかを log すると debug しやすい → 現状 logger 未実装 (Phase 2 候補)

### Re-evaluation triggers
- 空 Descriptor の存在自体に意味がある利用ケース (curator の入力履歴 audit 等) が出てきた

### Cross-refs
- [`../../ingest/src/asterism/starrydata.py`](../../ingest/src/asterism/starrydata.py) `parse_sample_info` line 165-170

---

## 9. parse_issued — date_parts JSON best-effort

### Decision
papers.csv の `issued` 列 (`{"date_parts": [[YYYY, MM?, DD?]]}` の JSON) を `schema:datePublished` の `xsd:date` literal に変換する。月 / 日が無い場合は **1 月 1 日として補完**。parse 失敗時は **triple を出さない**。

### Why
- starrydata の `issued` は Crossref API 由来で formatted (JSON 配列ネスト)
- 月 / 日が無い (年だけの) ものが ~30% ある
- xsd:date は YYYY-MM-DD format なので、月 / 日のデフォルトを決めるしかない
- 1 月 1 日補完は典型的な慣例 (BibTeX も同じ挙動)
- parse 失敗時に literal を出すと SPARQL の date 比較が壊れる → triple ごと省く方が安全

### Alternatives
- **A. parse 失敗時に raw 文字列を出す**: xsd:date 比較が壊れる
- **B. 月だけ無いケースは "YYYY-XX" 文字列に**: 非標準
- **C. xsd:gYear に倒す**: 月日があるケースで型がブレる
- **D. 1 月 1 日補完 + parse 失敗 drop** (採用): SPARQL クエリ側が単純になる

### Trade-offs
- "実際は何月だったか" の情報が失われるケースがある (例: 年だけのデータと 1/1 のデータが SPARQL では区別できない)
- 月日が無いことを示すフラグを別 property で残す案もあるが Phase 1 では不要と判断

### Re-evaluation triggers
- 月レベルの精度が SPARQL で必要になった (例: 月別 publication trends)
- citation の format で year-only と month-precision を区別する必要が出た

### Cross-refs
- [`../../ingest/src/asterism/starrydata.py`](../../ingest/src/asterism/starrydata.py) `parse_issued` line 91-110

---

## 10. error log — jsonl per-run

### Decision
ingester は失敗行を `error_log_path` (任意指定) に **jsonl** で記録し、本体処理は止めない。各 line は `{"row": <int>, "sid": <str?>, "error": <repr>}`。

### Why
- starrydata 56k papers / 144k samples / 233k curves から「数行の syntax error で全停止」は実用に堪えない
- jsonl は append-friendly + 各行を独立に parse 可能 + grep / jq で集計しやすい
- error_log_path が None なら何も書かない default は最小副作用

### Alternatives
- **A. CSV format**: column が安定しない (error が JSON serialize できない型を含むことがある)
- **B. structured logging (loguru / structlog)**: 依存追加、現状 Phase 1 で不要
- **C. jsonl + repr(exc)** (採用): 軽量、debug 時に十分

### Trade-offs
- `repr(exc)` は traceback を含まない → 詳細な debug には `--errors` を指定して `traceback` を追記する fallback が必要 (Phase 2 候補)
- error rate が `IngestStats.rows_err` でしか見えない → CI で alert する仕組みは Phase 2 で

### Re-evaluation triggers
- error analysis が頻繁になった (Phase 2 で別 dataset を扱い始めた等)
- traceback を含む rich error が必要になった

### Cross-refs
- [`../../ingest/src/asterism/starrydata.py`](../../ingest/src/asterism/starrydata.py) `_ingest_generic` line 614-651

---

## 11. dcterms:created の意味論

### Decision
papers.csv / samples.csv / curves.csv の `created_at` 列は **starrydata curator の record timestamp** であり、xsd:dateTime としては **parse せず raw 文字列のまま** `dcterms:created` に渡す。「ingestion timestamp ではない」ことを `anti_patterns` に明記。

ingestion timestamp は `prov:wasGeneratedBy/prov:atTime` で別経路として保持。

### Why
- starrydata の `created_at` は free-form 文字列 ("2023-04-12 10:15:32+09" や "2023/04/12" 等が混在)
- xsd:dateTime に parse するには normalization 必要 + 失敗時に triple drop すると上流対応が壊れる
- "curator が記録した時刻" と "asterism が投入した時刻" は **別の意味**

### Alternatives
- **A. xsd:dateTime に強引に parse**: 失敗行が多くて triple drop が頻発する
- **B. `dcterms:created` を `prov:wasGeneratedBy/prov:atTime` で代替**: semantic は近いが上流の record_at を捨てることになる
- **C. raw 文字列で残す + PROV で ingestion 時刻を別に** (採用): 両方の情報が残る

### Trade-offs
- SPARQL で "2023 年以降に curator が登録した sample" を date 比較で取れない → CONTAINS / STR で代替できるが UX は劣る
- Phase 2 で normalize する余地は残っている

### Re-evaluation triggers
- starrydata 側で `created_at` を ISO 8601 に統一する upstream PR が通った

### Cross-refs
- [`../../data/togomcp/mie/starrydata.yaml`](../../data/togomcp/mie/starrydata.yaml) `anti_patterns` (line 354+)

---

## 12. ファイル分割 — papers / samples / curves で 3 ingester

### Decision
`ingest_papers` / `ingest_samples` / `ingest_curves` を **別 public function** とし、共通のハーネスは `_ingest_generic(emit_row=...)` で内部関数化。CLI も 3 つ独立 (`_main` / `_main_samples` / `_main_curves`)。

### Why
- 3 つの CSV は schema が完全に独立。共通化を過度にすると interface が広くなる
- watcher / upload_api は file name で dispatch しやすい
- 失敗時の re-run も per-file で済む

### Alternatives
- **A. 1 つの `ingest_all` 関数で 3 CSV を順に処理**: 一気に走らせやすいが部分 re-run できない
- **B. plugin architecture (各 CSV 用 plugin class)**: overengineering、Phase 1 では不要
- **C. 3 個別 function + 共通 internal harness** (採用): シンプル

### Trade-offs
- "全 CSV を 1 コマンドで" は user CLI で `for f in papers samples curves; do ... done` が必要
- Phase 2 の watcher で per-file dispatch を書く必要 → 自然な分担なので問題なし

### Re-evaluation triggers
- CSV の数が 10 個以上になり個別関数が肥大化した
- 横断的な制約 (例: papers が無いと samples を処理しない) が出てきた

### Cross-refs
- [`../../ingest/src/asterism/starrydata.py`](../../ingest/src/asterism/starrydata.py) line 467-565

---

## 13. ShEx で MIE の shape_expressions を書く

### Decision
MIE YAML の `shape_expressions` は **ShEx (Shape Expressions)** で書く。AI が schema を理解するための shape 表現として ShEx を採用。

### Why
- togomcp の他 MIE (chebi.yaml 等) が ShEx で書かれている → format 共通化
- ShEx は Turtle ベースで AI / 人間どちらも読みやすい
- cardinality (`?` / `*` / `+`) が一目で見える
- 既存 ontology の reuse を `@<PersonShape>` のような名前参照で示せる

### Alternatives
- **A. SHACL**: より正式だが冗長、AI への "shape の伝達" 目的には ShEx の方が軽量
- **B. JSON-Schema**: 非 RDF community との互換性は良いが RDF 内で完結しない
- **C. ShEx** (採用): togomcp ecosystem に揃える

### Trade-offs
- ShEx の validator (shex.js, ShExer 等) を CI に組み込む手数が必要 → Phase 1 では parse すらしておらず、Phase 2 で導入予定
- 人間が SHACL に慣れていると ShEx は別途学習要

### Re-evaluation triggers
- togomcp 側が SHACL に乗り換えた
- shex.js / Python ShEx validator が出した結果が SHACL より良かった

### Cross-refs
- [`../../data/togomcp/mie/starrydata.yaml`](../../data/togomcp/mie/starrydata.yaml) `shape_expressions` (line 92-182)

---

## 14. Phase 2 candidates (意図的に **入れていない**もの)

Phase 1 で **意図的に保留**した決定。Phase 2 / 3 で再評価する。

| 項目 | 保留理由 | Re-evaluation |
|---|---|---|
| **QUDT QuantityKind / Unit** | propertyY / unitYString が raw 文字列 — Phase 2 で `qudt:SeebeckCoefficient` 等の IRI に正規化 | Phase 2 着手時 |
| **EMMO / MatOnto alignment** | 上位 ontology との subClassOf — 採用する ontology を Phase 3 (multi-dataset) で決めると効率的 | Phase 3 着手時 |
| **sd:DigitizationActivity** | WebPlotDigitizer 由来情報 — 上流 starrydata に source field が増えたら | Phase 2 候補 |
| **sd:DataPoint ノード化** | x/y 配列を個別ノード化 (方針 A) — ストア性能が許せば | starrydata 全件で Oxigraph が辛くなった時、または DataPoint クエリ需要が出た時 |
| **逆方向プロパティ** (`sd:hasSample` 等) | SPARQL 推論で扱えるので triple は追加しない | RDFS reasoner を on にした方がレスポンスが速い場合 |
| **DigitizationActivity の PROV-O 経路** | 別 Activity として持つ | starrydata 側で digitization tool name が CSV に入った時 |

### Cross-refs
- [`../ontology/diagram.md`](../ontology/diagram.md) §「Phase 1 で意図的に入れていないもの」 (line 103+)

---

## 15. 関連

- [`ai-assisted-step0-workflow.md`](ai-assisted-step0-workflow.md) — プロセス全体
- [`ai-assisted-step0-prompts.md`](ai-assisted-step0-prompts.md) — Step 3 で AI に投げる prompt 雛形 (本ドキュメントの §1〜§13 は §3.5 "設計判断の根拠" の completed example)
- [`option-b.md`](option-b.md) — アーキテクチャ
- [`phase05-decisions.md`](phase05-decisions.md) — Phase 0.5 で Oxigraph + Python rdflib を採用した判断

---

## 16. メンテナンス

このドキュメントは Phase 1 の判断点を **凍結保存**するためのものなので、Phase 2 以降の判断は別ファイル (`design-rationale-phase2.md` 等) に書き、本ファイルは「Phase 1 時点ではこう考えていた」の証拠として残す。

Phase 1 の判断を **覆す** ことが Phase 2 で確定したら、本ファイルの該当セクションの末尾に:

```markdown
> **Phase 2 update (YYYY-MM-DD)**: この判断は ... に置き換えられた (理由: ..., 詳細は ...)
```

を追記する (削除はしない)。
