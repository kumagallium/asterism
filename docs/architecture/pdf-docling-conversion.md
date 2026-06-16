# PDF（Docling）変換を文書オントロジー層に本番接続する ADR

status: **着手**（実現性は実証済み = `docs/reports/pdf-conversion-feasibility.md` /
spike `experiments/pdf-docling-spike/`。本 ADR で本番化の構造を決める）。
決定母体: [`document-ontology-layer.md`](document-ontology-layer.md)（文書層本体・JATS→doco/nif）/
[`non-csv-sources.md`](non-csv-sources.md)（取り込みはソース形式非依存）/
[`ingestion-execution-safety.md`](ingestion-execution-safety.md)（生成コード非実行・信頼境界）/
[`scalable-declarative-ingestion.md`](scalable-declarative-ingestion.md)（非同期 ingest ジョブ）。

## 0. 一行サマリ

日常文書の大半は **PDF**（非構造）。**Docling（IBM・レイアウト認識 ML）で PDF→構造**を一段挟めば、
PDF も既存の `論文→節→段落→文` の解決可能・引用可能・来歴つきグラフにそのまま乗る。ただし Docling は
**重い ML 依存**で、コア哲学（**生成コードを実行しない／runtime に ML を入れない**）を壊さないために、
ML 実行は **別サイドカーサービスに隔離**し、決定論変換（Docling 構造 → JATS）は **vet 済み in-repo 関数**に
置く。変換は **`lit:DocumentConversionActivity`**（変換器＋版＋日付）として開示する（RAG が変換を隠すのと逆）。

## 1. 動機と非ゴール

- **動機**: Word（pandoc）/JATS は UI から取り込めるが、研究者・実務者の手元文書は **born-digital PDF** が
  最多。「PDF を UI に直ドロップ → 節/段落/文に構造化 → 引用」を一周させる。
- **非ゴール**: スキャン PDF（OCR 必須・信頼度低）はこの PR の対象外（後続トグル）。OCR は最重量の ML で
  決定論が崩れやすいため、まず **born-digital（OCR オフ）** に絞る。
- **非ゴール**: PDF からの意味抽出（数値・関係の自動抽出）。本層は **位置と引用と来歴**だけを与える
  （文書層本体の非ゴールと同じ線）。

## 2. 信頼モデル（不変条件をどう守るか）

CLAUDE.md の不変条件は2つ:「生成コードを実行しない」「自リポジトリ単体で完結」。Docling は ML 推論なので、
そのままでは「runtime に重い非決定論ステップを持ち込む」ことになる。これを次の分離で解く。

| 層 | 何をするか | どこに置くか | 信頼性 |
|---|---|---|---|
| **PDF → DoclingDocument(dict)** | ML 推論（レイアウト/表モデル）。**唯一の非決定論ステップ** | **サイドカー**（torch 同梱・api イメージ外） | 中（モデル版を固定すれば再現可） |
| **DoclingDocument(dict) → JATS** | 純関数・stdlib・決定論アダプタ（`docling_dict_to_jats`） | **`asterism.documents`（vet 済み ingest pkg）** | 高（fixture でユニットテスト・torch 不要） |
| **JATS → doco/nif グラフ** | 既存の vet 済み structurer（`structure_jats`・defusedxml） | `asterism.documents`（無改修） | 高 |

要点:
- **サイドカーは ML 実行だけ**。生の `DoclingDocument` を `export_to_dict()` で返すのみ（asterism 非依存・極薄）。
- **決定論アダプタは信頼できる runtime（api/ingest）側で走る**。サイドカーが返す生 dict を、in-repo の
  vet 済み関数が JATS 化する。＝「ML は隔離、決定論変換は in-repo で検証」。生成コード非実行を維持。
- アダプタはセクション/図の `id` を**決定論的に合成**（PDF に id は無い）。これで `structure_jats` は無改修。
- 変換器版・モデル設定（OCR オフ）は `lit:DocumentConversionActivity` に刻む＝**開示された、版固定の主張**。
  信頼度ラベル: JATS=高／Word(pandoc)=高／born-digital PDF(Docling)=**中**／スキャン PDF(OCR)=低。

## 3. 置き場所の判断 = サイドカー（理想優先）

候補は ①サイドカー ②オフライン事前変換 ③バックグラウンドワーカー（feasibility 引き継ぎ）。
ユーザー選択 = **①サイドカー**（2026-06-15）。理由 = 製品ゴール「友人が UI だけで使える＝PDF を直ドロップ」に
最も近く、重 ML を1コンテナに隔離できる（哲学とも一致）。スペックが要るのはサイドカー1個のみで、
非同期化により「速いか遅いか」に効くだけで「動くか」には効かない。

非同期 = 既存の **JobManager + SSE** を再利用する。新しい async 配線・新エンドポイント契約を増やさない:
- **`.pdf` ソースは生のまま永続化**し、**構造化時（= 既存の非同期 ingest ジョブ）に変換**する。
  ＝ create→ingest→promote の主経路は、create を一瞬で返し（生 PDF 永続のみ）、遅い Docling 変換は
  既に SSE 進捗を持つ ingest ジョブの中で走る。UI 改修は accept に `.pdf` を足すだけ。
- docx は pandoc が一瞬なので従来通り **persist 時変換**（速度差による正当な非対称）。
- スナップショット再取り込み（A7）は生 PDF から Docling を再実行＝モデル版固定で再現可能。

## 4. サイドカー API（最小）

```
GET  /health                       -> {"status":"ok","converter":"docling/<ver> (...)"}
POST /convert  (PDF bytes; ocr=off固定) -> {"docling_doc": {...export_to_dict...},
                                          "converter": "docling/<ver> (...model/設定...)"}
```

- 同時実行 = 1（ピーク RAM を固定）。入力サイズ上限。タイムアウト。OCR オフ固定（born-digital）。
- api 側 `ASTERISM_DOCLING_URL` 未設定/不達は **明示エラー**（pandoc 不在と同型の graceful degrade）。
  ＝サイドカー無しでも Word/JATS/CSV/JSON は無影響。
- イメージは torch(CPU) + docling + モデル焼き込み。GPU 不要。推奨 = RAM 4–8GB / 2–4 vCPU。

## 5. 取り込み経路への接続

- `_DOCUMENT_SOURCE_SUFFIXES` に `.pdf` を追加。
- 永続: `.pdf` は生で source に保存（docx のように persist 時変換しない）。
- ingest ジョブ（`is_document` 分岐）: `.pdf` ソースを集め、ジョブ内で `_convert_pdf_via_sidecar`
  （httpx → サイドカー → 生 dict → `docling_dict_to_jats` → JATS）→ `structure_jats`。SSE で
  `phase="converting"` を出す。`.conversion` サイドカーに変換器版を記録（開示・A7 再現用）。
- append（`POST /api/datasets/{id}/documents`）: `.pdf` も受理。一文書なので同期変換（off-thread）。
  完全非同期化は後続（ROADMAP）。
- `structure_jats`・`document_to_nt_file`・recall tools（search_text/quote_with_citation/fetch_passage）は
  **無改修**。PDF 由来でも JATS 由来でも同一の引用できる事実に到達する（feasibility で実証済み）。

## 6. 検証ゲート

- アダプタ単体（torch 不要・committed Docling dict fixture）: dict→JATS→`structure_jats`→文が引ける。
- end-to-end（使い捨て Oxigraph + 実サイドカー）: PMC5951533 の PDF を一周し、**PDF 由来文 == JATS 由来文**
  （feasibility と同じ照合）。共有 :7878 は不可侵。
- レポート = `docs/reports/pdf-docling-production.md`（Q/Method/Result/Conclusion/Limitations/Reproduce）。

## 7. 限界・後続

- born-digital のみ（スキャン PDF=OCR は後続トグル・信頼度低として別 confidence）。
- 表は doco の `Table`/`table-wrap` へ（初回は図/キャプション/節/段落/リストを優先、表は段階対応）。
- append の完全非同期化（JobManager 化）は後続。
- モデル版ハッシュの厳密刻印（現状は docling 版 + 設定を記録）。
