# 検証レポート: 非構造 PDF/Word を文書オントロジー層に取り込めるか（変換の実現性）

2026-06-12 / 関連: [`document-ontology-layer.md`](../architecture/document-ontology-layer.md)・
spike `experiments/pdf-docling-spike/`

## Question

文書オントロジー層は JATS（構造化ソース）を前提にしているが、日常の文書は **PDF/Word**（非構造）が
大半。**変換を一段挟めば**、PDF/Word も「節→段落→文まで解決可能・引用可能・来歴つき」に取り込めるか。
哲学（決定論的 ingest・生成コード非実行）を壊さずに。

## Method

- 題材＝**PMC5951533**（10.3390/ma11040649・CC-BY）。**JATS と PDF の両方が手元にある稀な"正解ペア"**
  （JATS=`datasets/papers/`・PDF=MatPROV `10+3390__ma11040649.pdf`）。JATS 由来グラフを正解とする。
- 変換器を3段階で比較: ①素の PDF→text（pymupdf4llm）②レイアウト認識（**Docling** 2.101.0・IBM）
  ③（参考）GROBID→TEI。出力構造を **既存の決定論的後段パス**（`trim_collapse`+`sentence_spans`）に通し、
  doco/nif グラフ化して引用ツールで引く。
- 変換は **`lit:DocumentConversionActivity`**（変換器+版+日付）として PROV に記録＝オフラインの
  「日付つき主張」。runtime ingest 経路には ML を入れない（信頼境界不変）。

## Result

**忠実度（この PDF・JATS 正解と照合）:**

| 指標 | JATS（正解） | Docling | 素変換(pymupdf4llm) |
|---|---|---|---|
| 節見出し | 10 top＋5 小節 | **14**（全小節 2.1–2.5＋全 numbered 節） | 2（崩れ） |
| 図 | 13 | 17 picture＋23 caption | 0 |
| 表 | 10 | **10**（一致） | — |
| 重要文(PPMS/argon/TTO) | — | **全復元** | 行折返しで分断 |

**end-to-end（spike）**: PDF→Docling→doco/nif グラフ＝**1941 triples（14 節・329 文）**。§4 の測定条件文
（PPMS/TTO）を解決可能 IRI＋変換来歴つきで回収。**PDF 由来の文 == JATS 由来の文（バイト一致）** ＝
独立な2ソースが**同一の引用できる事実**に到達。

- 素変換の "弱点" は **テキスト消失ではなく構造（見出し/図）未認識と行折返し**だった（PPMS 文は素変換でも
  全語が読み順で存在・改行で割れていただけ→既存後段パスの `trim_collapse` が結合し1文に復元）。
- ⇒ **「ちゃんとした（レイアウト認識）変換器」さえ使えば、PDF から JATS 並みの本文構造が出る**を実証。

## Conclusion

**PDF/Word は変換を挟めば実用になる。** 唯一の追加リンク＝変換を **PROV の日付つき主張**
（`DocumentConversionActivity`・変換器/版/信頼度）にすれば、コア哲学（決定論・生成コード非実行・引用）を
保ったまま、既存の doco/nif パイプラインと引用ツールにそのまま乗る。変換器が TEI を出せば（GROBID）
`ql:XPath` に native、Docling のような構造オブジェクトなら JATS 用後段パスと同型の小アダプタで同じグラフに
落ちる（エンジン無改修）。**信頼度ラベル**: JATS=高／Word(pandoc)=高／born-digital PDF(Docling)=中・版固定で
再現可／スキャン PDF(OCR)=低。これは変換を隠す RAG/LLM 抽出に対する差別化（「どう構造化したか」を必ず開示）。

## Limitations

- Docling は ML（layout/OCR モデル）＝**決定論はモデル版＋推論設定を固定した時のみ**（born-digital は OCR
  オフで層モデルのみ＝固定可）。だから `DocumentConversionActivity` にモデル版/ハッシュを刻むのが要。
- spike は Docling の markdown を素朴にパース＝図/表/キャプションを段落に畳んでいる（段落 76・文 329 と
  JATS の 32/196 より多め＝ノイズ）。本番は picture/caption/table を doco の正しいクラスへ。
- 1論文・born-digital PDF のみ。スキャン PDF（OCR）・複数文書・Word(pandoc)・GROBID→TEI 比較は後続。

## Reproduce

```bash
# spike（torch/Docling 不要・コミット済 Docling 出力から実行）
PYTHONPATH=ingest/src ingest/.venv/bin/python experiments/pdf-docling-spike/run_spike.py

# 変換そのものを再現する場合（要 Docling・born-digital PDF は OCR オフ推奨）
python -m venv /tmp/dv && /tmp/dv/bin/pip install docling
/tmp/dv/bin/python -c "from docling.document_converter import DocumentConverter as C; \
  open('out.md','w').write(C().convert('<paper>.pdf').document.export_to_markdown())"
```
