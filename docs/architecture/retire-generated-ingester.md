# 生成 ingester の撤去 — 実行しないコードは、書かせない

Status: accepted (2026-08-28)

前提 ADR: [`ingestion-execution-safety.md`](ingestion-execution-safety.md)（生成 ingester
を自動実行しない）、[`phase5-declarative-substrate.md`](phase5-declarative-substrate.md)
（宣言的 substrate = 北極星 option 2）。

## 問題

`ingestion-execution-safety.md` は 2026-06-01 に「AI が生成した ingester を自動実行
しない」を鉄則として定め、北極星に option 2（宣言的マッピング + 検証済みエンジン）を
置いた。Phase 5 でその option 2 は**実現した** ── 取り込みは `mapping.rml.ttl` を
Morph-KGC が解釈する経路のみで、`ingester.py` は**一度も実行されない**。

にもかかわらず `ingester.py` は生成され続けていた。設計文書のプロンプト（§8 Ingester
sketch）が毎回 LLM にそれを書かせ、materialize が抽出し、registry が保存し、UI が
「旧方式」チップ付きのカードとして出していた。

本番 registry の生成物（`xrd-42f904f7/ingester.py`）を読むと、**仮に実行しても壊れる**
内容だった:

| 症状 | 実際の記述 |
|---|---|
| 定義したヘルパを呼んでいない | `make_sample_iri()` が未使用 |
| 区切り文字が実データと違う | `csv.DictReader(f, delimiter=" ")` — 実際は空白の連続 |
| 存在しない列を読む | `row["No"]` — 生ファイルに `No` 列は無い（preamble の配布は Asterism 側の処理） |
| 他人の名前空間に IRI を作る | `URIRef(f"{PROV}ingest/...")` — w3.org の `prov#` 配下 |
| 意味が矛盾 | 同じ IRI を `prov:used` と `prov:generated` の両方に入れている |

つまり **LLM のトークンを使って、誰も使わない誤ったコードを生成・保存していた**。
それだけでなく、この誤りは検査を通じて設計にも影響していた ── T1（ID の一意性）は
ingester の ast を解析して鍵を復元し、T2（BOM）は ingester の文字列を grep し、
T3（blank node 無し）は ingester に `BNode(` が無いかを見ていた。**実行されない
スケッチの記述の揺れが、データの欠陥として報告されうる**状態だった。

## 決定

**`ingester.py` を成果物から撤去する。** 生成も保存も画面表示もしない。

検査 T1/T2/T3 は、実際に走る経路 ── §9 mapping spec（`mapping.yaml`）と MIE ── だけを
読むように付け替える。

| 検査 | 旧: 何を読んでいたか | 新: 何を読むか |
|---|---|---|
| T1 ID の一意性 | MIE の IRI テンプレート **＋ ingester の IRI ビルダ（ast 解析）** | MIE の IRI テンプレート **＋ §9 `maps[].subject.template`** |
| T2 BOM | ingester を `utf-8-sig` で grep（＋§9 `dialects:`） | §9 `dialects:` の encoding のみ |
| T3 blank node 無し | TBox の bnode ＋ ingester の `BNode(` grep | TBox の bnode のみ |

## なぜこれで従来と同等以上か

**T1 はむしろ正確になる。** ingester の ast 解析は「f-string を組み立てる Python の
形」から鍵を推測する不確実な復元だった（部分解決しかできない鍵は `notes` に落として
検査対象外にしていた）。§9 は `subject.template` に鍵をそのまま宣言している。さらに
**各 map は自分の `source:` を宣言している** ── 旧実装は鍵をどのファイルで検査するかを
エンティティ名とファイル名の類似（`paper` → `papers.csv`）で推測していたが、いまは
仕様が名指ししたファイルで検査できる。推測が 1 段減った。

**T2 は「実際に開く文字コード」だけを見るようになった。** 旧実装は 2 つの読み手
（実行されないスケッチと、実際に走る RML substrate）を別々に judge し、修正レシピも
§8 用と §9 用の 2 種類を持っていた。取り込みの読み手は 1 つしかないので、判定も
レシピも 1 つでよい。

**T3 は原理的に起きない事象を見るのをやめた。** 宣言経路では全 subject が §9 の
`template:`／`constant:` から鋳造される ── IRI であることが構成上保証されている
（subject 項が無い仕様はコンパイラが T9 で弾く）。blank node をまだ表現できる成果物は
TBox だけなので、そこだけを見る。

## 帰結

- **§8 用の決定論的修理を 1 つ失う。** `design_loop._stamp_utf8_sig`（`encoding="utf-8"`
  → `utf-8-sig` の 4 文字置換）を撤去した。代わりの §9 版は**作らない**: §9 の
  `dialects:` は `_overlay_detected_dialects` が検出値で毎ラウンド上書きする**機械所有**
  の領域で、LLM のラウンドでも修理器でも書き換える対象ではない。ここが T2 で落ちるのは
  「検出器が BOM を残す文字コードを選んだ」という、人間が見るべき稀な状態である。
- **設計文書は §1-7 + §9 になる。** §8 が抜けた番号は詰めない ── §9 は IR 契約の正式な
  見出しで、materialize・splice・修理レシピ・ADR が名前で参照している。番号を動かすと
  それら全部が嘘になる。欠番のほうが安い。
- **後方互換: 既存 registry のディスク上の `ingester.py` は消さない。** 読み込み側が
  参照しなくなるだけ。ただし履歴 diff は「今の成果物集合」に限定して比較する ──
  撤去前のスナップショットと現在を素朴に比べると、**利用者がしていない「ingester.py
  が全削除された」変更**が過去の全 diff の先頭に出てしまうため。

## 採用しなかった案

- **§8 を残して「実行しない」と注記し続ける。** 現状がそれ（UI の「旧方式」チップ）。
  誤った内容を生成し続けるコストと、検査が誤りに引きずられるリスクが残る。
- **§8 を機械合成（`doc_synth.synthesize_ingester_py`）だけにする。** LLM のトークンは
  節約できるが、誰も読まず誰も実行しないファイルを保存し続ける点は変わらない。
- **ディスク上の `ingester.py` を物理削除する。** 過去のスナップショットは「その時の
  設計はこうだった」という記録で、遡って書き換えるものではない。
