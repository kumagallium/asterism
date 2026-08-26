# 検証レポート: 検出できていた文字コードが、設計に記録される途中で消えていた

2026-08-26 / 関連: [`docs/architecture/source-dialect.md`](../architecture/source-dialect.md)（契約の正）

## Question

実機（v0.22.0 デスクトップ版）で、日本語ヘッダーを持つ XRD 装置ファイルが「④ 項目の意味」で
`ファイルの文字を読み取れませんでした` に落ちた。

- 文字コードの検出は本当に失敗していたのか
- 失敗していないとしたら、正しい答えはどこで失われたのか
- 人間に「CSV UTF-8 で保存し直してください」と頼むのは、機械が答えを出せない問いなのか

## Method

実機に残っていた失敗ジョブ（`sources/jobs.jsonl`、同一エラー 3 回）と、その入力ファイル
（`registry/_staging/…/xrd-664287b2.txt`、67,457 bytes）をそのまま使い、
**配布版アプリに同梱されている step0 / ingest** で各段を単独実行して切り分けた。

対象ファイルの形状（実バイト）: 1 行目 `Al3V_bulk`（ASCII の前置き行）、2 行目だけが cp932
（`2θ (deg)` / `強度 (cps)` = `0x83 0xc6` / `0x8b 0xad 0x93 0x78`）、
3 行目以降 3,001 行はすべて ASCII の数値、タブ区切り、CRLF。

## Result

### 1. 検出は全段で正しかった

| 段 | 実行したもの | 結果 |
|---|---|---|
| 文字コード＋区切りの検出 | `detect_dialect` | `encoding='cp932', delimiter='\t', skip_rows=1` ✅ |
| ①②③（読み取りの確認） | `inspect_source_set` | 同上。列名も `2θ (deg)` / `強度 (cps)` と正しく取得 ✅ |
| 設計への pin（決定論） | `apply_source_dialects` | `encoding: cp932` を書き込む ✅ |
| ④ で実際に使われた pin | 失敗ジョブのエラー本文 | **`utf-8-sig`** ❌ |

失敗の実体は ingest 段の RML 検証:

```
source file 'xrd-664287b2.txt' cannot be decoded with its pinned
dialect encoding 'utf-8-sig': 'utf-8' codec can't decode byte 0x83 in position 12.
The file changed since design time — re-run design/inspect to re-pin the dialect.
```

ファイルは設計時から一切変わっていない。エラー文の診断そのものが誤っていた。

### 2. 正しい答えが消える経路は 2 つあった（いずれも再現済み）

**(a) 人間の部分的な訂正が、触っていない項目まで既定値に戻す**

ウィザードの「読み取りの確認」から送られる override は
`{source: SourceDialect}`、つまり**ソース単位の全項目**として解釈されていた。
`SourceDialect` は「未指定」を表現できないため、書かれていない項目はクラス既定値になる。

```
入力  {'delimiter': '\t', 'skip_rows': 1}          ← 文字コードは書かれていない
出力  SourceDialect(encoding='utf-8-sig', ...)      ← 検出済みの cp932 が消える
issues: []                                          ← 警告も出ない
```

さらに設計ループ側が `effective = {**detected, **overrides}` とソース単位で置換するため、
検出結果は復元されない。

**(b) LLM が §9 に書いた `dialects:` が、検出結果に勝つ**

`apply_detected_dialects` は「IR に明示された値が検出に勝つ」設計だった。これは人間の
訂正を守るための規則だが、**LLM が書いた値と人間が訂正した値を区別していない**。
自動修正ラウンドが `encoding: cp932` を `utf-8-sig` に「直す」と、決定論側はそれを尊重する。

回帰テストで両経路とも、修正前のコードで `cp932` → `utf-8-sig` に化けることを確認した
（`api/tests/test_source_dialect.py::test_partial_override_keeps_the_detected_encoding`、
`::test_llm_authored_encoding_loses_to_detection`）。

**実機で起きたのは (b) である**（使用ログ `registry/_usage/events-2026-08.jsonl` のタイムライン、
すべて UTC）:

```
01:12:57 〜 01:14:45   propose.autocorrect  × 5   ← 自動修正が §9 を 5 回書き直した
01:15:14 / 01:15:39    refine               × 2   ← 「AI に直してもらう」
01:15:40               ingest → error  pinned dialect encoding 'utf-8-sig'
```

`_overlay_detected_dialects` は各ラウンド後に走るが、旧実装では「明示値が検出に勝つ」ため、
ラウンドが書いた `utf-8-sig` を上書きできなかった。取り込み直前の 5 + 2 ラウンドが
`dialects:` に触れる機会そのものだった。

### 3. ingest 段は、答えを出せる問いを人間に投げていた

`.txt`（legacy suffix）でアノテーションが無いソースには `DEFAULT_DIALECT`（= `utf-8-sig`）が
無言で割り当てられ、strict read に失敗した時点で取り込み全体が停止していた。
どの文字コードで開けるかは、候補を順に試せば機械が決められる。

## 修正（3 層）

| 層 | 変更 |
|---|---|
| ① 受け取り | override は**書かれた項目だけ**を運ぶ（`{source: {field: value}}`）。設計ループは項目単位でマージする（`merge_dialect_overrides`） |
| ② 権限 | 設計ループの pin を authoritative にし、LLM が `dialects:` に書いた値を上書きする。`--source-dir` からの再 pin は従来どおり明示値を尊重する（FIX2 を維持） |
| ③ 取り込み | pin で読めなければ `encoding_that_decodes` が検出器と同じ候補（`utf-8-sig` / `cp932`）を全ファイルに対して試し、読めた方で正規化し直す。ログに substitution を残す。どれでも読めなければ従来どおり 422（文言は「試した候補」を述べる形に変更） |

②の候補に `latin-1` は**入れない**。任意のバイト列を読めてしまうため、推測に使うと
「誰も見ていない場所で文字化けしたまま取り込む」経路になる。人間がプレビューを見て
`latin-1` を選んだ場合の pin は従来どおり尊重される。

## Result（修正後、同じ実ファイルで再実行）

```
LOG source 'xrd-664287b2.txt' does not decode as its pinned 'utf-8-sig';
    reading it as 'cp932' instead (the design's pin is stale — re-designing re-pins it)
正規化されたか : True
ヘッダー行     : 2θ (deg),強度 (cps)
先頭データ行   : 20.000000,3600.000000
行数           : 3002
```

設計側も同じ実ファイルで確認した。

| 入力 | 結果 |
|---|---|
| 検出 | `cp932, '\t', skip_rows=1` |
| override（`skip_rows` だけ明示） | `{'skip_rows': 1}` |
| 実際に使う読み方 | `cp932, '\t', skip_rows=1` — 文字コードが残る |
| LLM が `utf-8-sig` と書いた §9 | 最終 pin は `encoding: cp932` — 決定論が上書き |

## Conclusion

- 検出器は正しかった。壊れていたのは**正しい答えを設計に運ぶ経路**で、原因は
  「未指定」を表現できないデータ形と、「誰が書いた値か」を区別しない優先規則の 2 つ。
- ファイルの読み方は evidence であって設計判断ではない。LLM ラウンドが書き換えてよい
  対象から外した（列の帰属・数値型と同じ扱い）。
- 「CSV UTF-8 で保存し直してください」は、機械が試せば答えの出る問いだった。ingest 側で
  試すようにしたので、この案内が出るのは本当にどの候補でも読めないファイルだけになる。

## Limitations

- ingest 側の修復は**文字コードのみ**。区切り・見出し前の行数・前置き行の扱いが誤って
  pin されている場合は救済しない（decode 失敗はそれらについて何の証拠でもないため）。
- 候補は `utf-8-sig` / `cp932` の 2 つ。UTF-16 や EUC-JP のファイルは、検出器が pin して
  いれば読めるが、pin が失われた場合の救済対象外。
- 実機の事故は (b) と特定したが、LLM がどのラウンドで `dialects:` をどう書いたかまでは
  復元していない（失敗した設計は ingest の rollback で消えており、`proposal.md` が残って
  いない）。特定は使用ログのタイムラインと、旧実装が LLM の明示値を尊重する仕様である
  ことの組み合わせによる。
- (a) は実機で発火した証拠はない（UI は検出値から全項目を組み立てて送るコードになっている）。
  API 境界の仕様として実在する穴なので、同じ事故の別入口として塞いだ。

## Reproduce

```bash
# ① 受け取り: 書かれた項目だけが残る / 検出値が消えない
cd api && pytest tests/test_source_dialect.py -k "partial_override or llm_authored"

# ③ 取り込み: pin が実バイトと合わなくても読み直す / どれでも読めなければ 422
cd ingest && pytest tests/test_substrate.py -k "stale_pinned or no_encoding_can_read"
```

実ファイルでの確認は、cp932 のヘッダー行を持つタブ区切り `.txt` を用意し、
`ast:sourceEncoding` を書かない RML を `normalize_dialect_sources` に渡す
（実機の失敗状態と同じ形）。
