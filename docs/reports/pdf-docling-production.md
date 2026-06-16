# 検証レポート: PDF（Docling サイドカー）を文書層に本番接続した経路の正しさ

2026-06-15 / 関連: ADR [`pdf-docling-conversion.md`](../architecture/pdf-docling-conversion.md)・
先行 feasibility [`pdf-conversion-feasibility.md`](pdf-conversion-feasibility.md)。

## Question

born-digital PDF を、コア哲学（決定論・生成コード非実行・runtime に ML を入れない）を壊さずに、
**UI からアップロード → 節/段落/文に構造化 → 解決可能 IRI で引用**まで本番経路で通せるか。
変換器（Docling=重い ML）を**別サイドカーに隔離**し、決定論変換（Docling→JATS）を**vet 済み in-repo
関数**に置いた設計が、実 PDF・実 Docling・実 SPARQL で「引用できる事実」に到達するか。

## Method

- 題材 = **PMC5951533**（10.3390/ma11040649・CC-BY・born-digital PDF 3.5 MB）。同論文の native JATS が
  `datasets/papers/` にあり、**JATS 由来文を正解**とする稀な"正解ペア"（feasibility と同一題材）。
- 構成 = ADR の3層分離:
  1. **サイドカー**（`infra/docling-sidecar/app.py`・docling 2.102.1・OCR オフ・同時実行1）が
     `POST /convert` で PDF→`DoclingDocument.export_to_dict()`（生 dict）を返す。ML はここだけ。
  2. **`asterism.documents.docling_dict_to_jats`**（純関数・stdlib・torch 不要）が生 dict→JATS。
  3. 既存 **`structure_jats`**（無改修）が JATS→doco/nif グラフ。
- 検証3段:
  - **アダプタ単体**（torch 不要）: committed 実 Docling dict fixture（`ingest/tests/fixtures/
    docling_ma11040649.json`）→ JATS → `structure_jats` → PPMS 文回収（`test_docling_adapter.py`）。
  - **api 経路**（サイドカー mock）: `.pdf` を生で永続→ 非同期 ingest ジョブが変換（`converting`
    フェーズ）→ 構造化→ ステージンググラフへストリーム、変換来歴 `.conversion` 記録（`test_ingest.py`）。
  - **end-to-end（実サイドカー・実 Docling）**: 実 PDF bytes → `convert_pdf_to_jats`（HTTP）→
    実 Docling → アダプタ → `structure_jats` → PPMS 文を JATS 正解と照合。

## Result

**end-to-end（実サイドカー・CPU・GPU なし）:**

| 指標 | 値 |
|---|---|
| 変換時間（PDF 3.5 MB・CPU・OCR オフ） | **16.4 s**（feasibility のスペック見積「数十秒」と一致） |
| converter id（来歴に刻印） | `docling/2.102.1 (docling-core/2.82.0; docling-ibm-models/3.13.3; ocr=off)` |
| JATS 復元 | 14 節見出し・12 図（キャプション付き） |
| doco/nif グラフ | 3187 triples（**15 Section** / 92 Paragraph / **345 Sentence** / 12 Figure） |
| PPMS 測定条件文 | **解決可能 IRI で1件回収**（`…/sec/sec-11/para/1/sent/0`） |
| PDF 由来文 == JATS 正解 | **✓ バイト一致** |
| `lit:DocumentConversionActivity` | **グラフに開示**（変換器+版・parse が `prov:wasInformedBy`） |

回収した文（PDF のみから・JATS 不使用）:
> "The transport properties at low temperatures were determined by a physical properties
> measurement system (PPMS, Quantum design, San Jose, CA, USA) using the thermal transport
> option (TTO) for the thermoelectric properties."

= native JATS 経路が引用する文と**同一**。独立な2ソースが**同一の引用できる事実**に到達。

**自動テスト:** ingest 324 passed / api 104→**+PDF 6 件**（生 PDF 永続・サイドカー未設定 422・実 mock
変換＋来歴開示・source_kind 判定）。アダプタ 7 件（well-formed JATS・文回収・決定論・page furniture
除外・graceful degrade・サイズ上限）。ruff 全通過。

## Conclusion

**ADR の3層分離は実データで成立する。** 重い ML（Docling）を1サイドカーに隔離し、決定論変換を vet 済み
in-repo 関数（fixture でテスト・torch 不要）に置くことで、コア哲学を保ったまま PDF が既存の doco/nif
パイプライン＋引用ツールにそのまま乗る。`.pdf` は生で永続し**既存の非同期 ingest ジョブ内**で変換する
ので、新しい async 配線も create エンドポイント契約変更も不要（UI は accept に `.pdf` を足すだけ）。
スペックが要るのはサイドカー1個のみ・GPU 不要・遅くても進捗バーが伸びるだけ（落ちない）。

## Limitations

- **born-digital のみ**（OCR オフ）。スキャン PDF は後続トグル・信頼度低として別 confidence。
- **表**は doco `Table` 化せず、キャプションを段落として保持（検索・引用は可能・ADR §7 で後続）。
- **決定論はモデル版固定が前提**: 別 Docling 実行で全文 JATS は数バイト揺れうる（同一題材2実行で
  44082 vs 44069 bytes）。**引用する文（PPMS）はバイト一致**で安定。だから変換を**日付つきの
  `DocumentConversionActivity` 主張**として開示する（=高信頼を僭称しない）。
- append（`POST /api/datasets/{id}/documents`）の PDF は同期変換（1文書）。完全非同期化は後続。

## Reproduce

```bash
# 1) アダプタ単体（torch 不要・CI と同じ）
cd ingest && uv run pytest tests/test_docling_adapter.py -q

# 2) api 経路（サイドカー mock・torch 不要）
cd api && uv run pytest tests/test_ingest.py -q

# 3) end-to-end（要 docling・born-digital PDF・OCR オフ）
python -m venv /tmp/dv && /tmp/dv/bin/pip install docling fastapi 'uvicorn[standard]' httpx
( cd infra/docling-sidecar && /tmp/dv/bin/uvicorn app:app --port 8205 ) &
PYTHONPATH=ingest/src ingest/.venv/bin/python - <<'PY'
from asterism import documents
import rdflib
b = open("<paper>.pdf","rb").read()
jats, conv = documents.convert_pdf_to_jats(b, sidecar_url="http://127.0.0.1:8205")
g = documents.structure_jats(jats, paper_iri="https://ex/doc/x", conversion={"converter":conv,"sourceFormat":"pdf"})
NIF="http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#"
print([str(o) for _,_,o in g.triples((None, rdflib.URIRef(NIF+"anchorOf"), None))
       if "physical properties measurement system" in str(o)])
PY
```
