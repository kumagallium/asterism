# AI-assisted Step 0 — dogfood feedback

[`ai-assisted-step0-workflow.md`](ai-assisted-step0-workflow.md) §9 が依頼しているフィードバックの蓄積先。実際に `propose_schema` (Phase 3 #4) を回して観測した「機械化できた / 人間が必須 / 直すべき」点を記録する。

---

## Round 1 — starrydata subset (2026-05-29)

### 設定

| 項目 | 値 |
|---|---|
| dataset | starrydata 先頭 40 papers の subset (papers 40 / samples 194 / curves 693 行, FK 整合) |
| tool | `csv2rdf-propose` (Opus 4.7, adaptive thinking, effort=xhigh, prompt cache) |
| domain hint | "熱電・電池・磁性の測定曲線。PROV-O 必須、bnode 不使用。Seebeck=thermopower=熱起電力=ゼーベック係数。" |
| 入力 | inspection Markdown ~13KB + domain hint |
| 出力 | 8 セクション構成の proposal Markdown (但し §7 途中で truncate — Finding 2) |

### 品質評価 (機械化できた部分)

proposal は **総じて高品質**。特に良かった点:

- **§5.3 Curve IRI で複合キーを正しく選択** — inspection が `(sample_id)` 単独も `(figure_id)` 単独も curves.csv で collide すると示したのを見抜き、`sdr:curve/{sample_id}-{figure_id}` (693/693 unique) を選んだ。さらに「同 sample が同 figure に複数 curve を持つようになったら `cycle_index` を足せ」という re-evaluation trigger まで付けた
- **JSON 列戦略が的確** — author → Person ノード展開、issued → xsd:date 圧縮、x/y → JSON literal + 集約 (方針 C、bnode を避ける理由を T3 と紐付け)、sample_info の **dirty keys** (`" remanence magnetion"` の先頭空白・重複) を見て「raw + selective lift」に倒した
- **8 罠を self-check** — ★ T1 / ★ T3 マーカーを IRI 設計と紐付け、sample_rdf_entries は実 CSV 値 (`sdr:sample/6027`, `Cu2.025Cd0.975SnSe4`) を使用 (T6 準拠)
- **Decision / Why / Alternatives / Trade-offs / Re-evaluation** が全設計判断に付く (T7 準拠)
- rdf-config model.yaml が `bundle exec rdf-config --shex` にかけられる正しい構文

→ Phase 1 で人間 + AI が数時間かけた co-design の **大半が 1 回の LLM 呼び出しで再現**できた。

### ★ Finding 1 (方法論、人間が必須): subset は uniqueness collision を隠す

proposal は **§5.2 で Sample IRI = `sdr:sample/{sample_id}` (sample_id 単独)** を選んだ。理由は inspection で subset の samples.csv では `(sample_id)` が 194/194 unique だったから。

しかし **全件 (104,846 行) では `sample_id` 単独に 13,225 collisions** あり、`(SID, sample_id)` でないと unique にならない (= Phase 1 [design-rationale §1](design-rationale.md#1-iri-命名--複合キー-composite-iri) がまさに踏んだ罠)。

**重要なのは、proposal 自身が §5.2 の Trade-offs で正しく警告していたこと**:

> assumes `sample_id` is globally unique in Starrydata's authoritative DB. **Re-evaluate by re-running the inspection on full exports.**

つまり LLM は T1 を意識して「これは subset 前提、全件で再検証せよ」と明示した。これは [workflow §7](ai-assisted-step0-workflow.md#7-人間の必須介入ポイント-never-skip) 「IRI uniqueness の最終確認は全件で」の原則が **実証された**形。

**教訓・運用ルール**:
- **IRI uniqueness を決める列は、subset でなく全件で inspect すること**。propose は subset で品質を見てよいが、IRI key の最終決定前に `csv2rdf-inspect <full csv> --fk ...` で collision 0 を確認する
- subset で propose → 全件で `csv2rdf-validate` (T1) すれば自動で catch できる。今回 `(sample_id)` を全件でチェックして 13,225 collisions を確認 → validate ワークフローが機能することも実証
- propose の domain hint に「sample_id は paper を跨ぐと重複する」と書けば、LLM は subset を見ても複合キーを選べる (= Phase 1 の知見を hint で前倒し注入できる)

### ★ Finding 2 (コードバグ、修正済): max_tokens=16000 非ストリーミングで truncate

proposal が **§7 MIE の sparql_query_examples 途中 (q5) で切れた**。`AnthropicLLMClient` が `messages.create()` 非ストリーミング + `max_tokens=16000` だったため。8 セクション (Mermaid + 144 行の rdf-config model.yaml + MIE + ingester) は 16k tokens に収まらず、§8 ingester に到達しなかった (materialize の "No ingester block" の原因)。

**修正** (この PR): `AnthropicLLMClient.complete()` を `client.messages.stream()` + `get_final_message()` に変更、`max_tokens` を 32000 に。claude-api skill のガイダンス通り、大きい出力はストリーミングで SDK の ~10 分タイムアウトを回避する。

### Round-trip 検証

- `csv2rdf-materialize` で proposal Markdown → diagram.md / model.yaml / mie.yaml に分割成功 (ingester だけ truncate で欠落 — Finding 2)
- `csv2rdf-inspect` を全件 samples.csv にかけて Finding 1 を定量確認

---

## Round 2 — streaming 修正 + hint 注入後 (2026-05-29)

Round 1 の Finding 1/2 への対策を入れて再 propose:
- Finding 2 修正: `AnthropicLLMClient` を streaming 化 + max_tokens 32000
- Finding 1 対策: domain hint に「sample_id / figure_id は paper を跨いで重複するので IRI は {SID} を含む複合キーにすること」を一行注入

### 結果: 両対策が効いた

- **Finding 2 解消**: proposal が §8 ingester まで完走 (664 行)。truncation なし。ingester は `utf-8-sig` 4 箇所 + 複合 IRI builder (`sample/{sid}-{sample_id}`, `curve/{sid}-{sample_id}-{figure_id}`) を持つ
- **Finding 1 解消**: §2 IRI scheme で **Sample = `sdr:sample/{SID}-{sample_id}` (複合)**、Curve = 3-way 複合を選択。§5.1 に「`(sample_id)` は subset では unique だが **domain rule では paper を跨いで collide する** ので複合が安全な最小キー」と明記 → hint 注入で subset の誤信号を正しく上書きできた
- materialize で 4 artifacts 全部出力 (ingester も)

### ★ Finding 3 (validate の false positive、修正済): anti_patterns の負例 IRI を T1 が誤検知

materialize した v2 MIE を全件 CSV で validate したら **T1 ✗ fail (`sdr:sample/{sample_id}` → 13,225 collisions)**。だが調べると、その `sdr:sample/{sample_id}` は MIE の **`anti_patterns` の中**の文だった:

> Do NOT mint sample IRIs as `sdr:sample/{sample_id}`. In the full database sample_id is paper-scoped and collides across SIDs...

つまり **proposal v2 は完全に正しい** (ingester・sample_rdf_entries・§2 全て複合キー、anti_patterns で単独キーを明示的に禁止)。validate の `_extract_composite_keys` が `anti_patterns` の「使うな」例から IRI template を抽出して collision 判定した **false positive**。

**修正** (この PR): `_check_t1_uniqueness` は MIE を YAML parse して `anti_patterns` / `common_errors` セクションを除外してから IRI template を scan する。負例が T1 を誤って fail させなくなった (regression guard: shape_expressions に本当に単独キーを書いたら依然 fail する test 付き)。

→ 修正後 v2 の validate: T1 ⚠ warn ("no composite templates in MIE") / T2-T5 pass / T6-T7 warn / T8 skip → **exit 0** (誤 fail が消えた)。

### 残った heuristic 限界 (既知、未修正)

- **T6 warn**: v2 の sample_rdf_entries は `- subject: sdr:sample/1-6027` 形式 (triple list) で、T6 が期待する `rdf: |` ブロック形式と違うため "no sdr IRIs" と warn。MIE entry の format 揺れに T6 を強くする余地あり
- **T7 warn**: 設計根拠は proposal §5 に厚くあるが、materialized MIE の architectural_notes には literal "Why/Alt/Trade-offs" が無く warn。YAML-structured rationale parsing で改善可

---

## Round 3 — propose → refine → materialize → validate full loop (2026-05-29)

実 LLM で **refine の round-trip** を回した (入力 = Round 2 の v2 proposal)。実運用で来そうな review コメント 2 つを `--comments-file` で渡した:

1. 「Paper の SID は全件 (56k) で 28 collisions ある。Paper IRI を `(SID, DOI)` 複合キーにし、ingester / sample_rdf_entries / 設計根拠を同期更新。真 duplicate (同 SID 同 DOI) はエラーログへ」
2. 「compositionDetails は ~6% しか埋まっていない。cardinality 0..1 を明示し anti_patterns に追記」

### 結果: refine は 4+ artifacts を正しく同期更新した ✓

> 以下の値はすべて出力ファイルを Read tool で直接確認した実測値。

`csv2rdf-refine` 出力 = **46,546 bytes / 850 行、所要 5 分 52 秒、truncation なし** (末尾 `g.serialize(...)`)。system prompt 通り §1 Comment resolution log + §2 Updated schema 構成 (comment 用に §5.9 / §5.10 を追加)。

Comment 1 の resolution log が秀逸:
- Interpretation で「(SID, DOI) で別 paper を区別、同 SID 同 DOI のみ真 duplicate」と正しく解釈
- Affected artifacts に TBox (IRI scheme + §5 rationale) / MIE (sample_rdf_entries, anti_patterns, architectural_notes) / ingester を列挙、**Mermaid は IRI 構造を持たないので unchanged** と正しく除外
- ingester: `paper_iri(sid, doi)` + `_PAPER_INDEX: dict[int,str]` (SID→DOI) を ingest_papers で構築し samples/curves が解決、`seen: set[(SID,DOI)]` で真 dup を `log_error` → `continue`
- sample_rdf_entries の Paper IRI を `sdr:paper/1-10.1021-ar400290f` (実 DOI) に更新
- **Side effects**: 旧主張 `"(SID) unique 40/40"` を T7 通り supersede (削除せず caveat 化)、Sample/Curve IRI が bare SID のままなので残存衝突リスクを自己申告
- **Open questions** 3 件 (genuine): ① rename を Sample/Curve IRI にも cascade すべきか (保守的に保留) ② SID=6 の DOI が inspection に無く placeholder ③ 空 DOI の preprint の fallback IRI

Comment 2 は additive (cardinality 0..1 + composition_details sparse anti_pattern + §5.10) と正しくスコープ。

### round-trip 検証 (すべて出力ファイルを Read tool で確認)

- `csv2rdf-materialize` → 4 artifacts 出力 (exit 0)。ingester (`starrydata.py`, 10.8KB): `def paper_iri(sid: int, doi: str)` を **1 回** (重複なし)、`utf-8-sig` 3 箇所、`seen`/dedup あり
- MIE に `composition_details sparse coverage` anti_pattern あり
- **全件 3 CSV (papers 56k + samples 105k + curves 233k) で `csv2rdf-validate` → exit 0** (T2-T5 pass / T1・T6・T7 warn / T8 skip)。blocking failure なし、新バグなし

### 総括

`inspect → propose → refine → materialize → validate` の全 step が実 LLM で連結動作。**refine が「4 artifacts を矛盾なく同期更新する」という人間には面倒な作業を正しくこなした**ことが最大の収穫。Phase 1 で人間が手で 4 ファイルを書き換えていた部分が自動化できた。

### T1 の限界が再確認された (将来の改善余地)

refined ingester は正しく複合 `paper_iri(sid, doi)` を使うが、validate T1 は v2 と同じく warn ("no composite IRI templates in MIE")。**T1 は MIE の `{}` template しか見ず ingester の IRI builder を読まない**ため。`def paper_iri(sid, doi): SDR[f"paper/{sid}-{_slug(doi)}"]` を parse して実キー構造で uniqueness を検証すれば、「propose が誤キーを選んでも全件 validate が必ず catch する」完全な安全網になる。

> **プロセス上の自己反省**: この Round 3 の初回報告 (チャット) は **background refine の完了を待たずに書き、具体値が誤っていた** (サイズ・時間・変数名)。上記は実出力を Read tool で再検証した確定値。教訓: **LLM ツール結果は background 完了通知を待ち、出力ファイルを直接 Read してから記録する**。

---

## T1 ingester-builder 検証強化 (実装、2026-05-30)

Round 3 末尾で「将来の改善余地」とした **T1 を ingester の IRI builder から検証する強化** を実装した (`feat/phase3-validate-t1-ingester`)。これで「propose / refine が誤キーを選んでも全件 validate が必ず catch する」完全な安全網が入った。

### 何をしたか

新モジュール [`step0/src/csv2rdf_step0/t1_ingester.py`](../../step0/src/csv2rdf_step0/t1_ingester.py) が ingester を `ast` で parse し、**RDF entity ごとに IRI を構成する実際の CSV 列**を復元する。2 つの ingester スタイル両方に対応:

1. **builder-function 形式** (LLM / proposal 出力) — `def sample_iri(sid, sample_id): return SDR[f"sample/{sid}-{sample_id}"]`。placeholder は関数引数なので **call site** (`sample_iri(row["SID"], row["sample_id"])`) から列を引き当て、無ければ **ヘッダ名の大文字小文字無視マッチ** (`sid`→`SID`) にフォールバック。`_slug(doi)` / `.strip()` / `str(...)` などの変換ラッパは透過。
2. **inline 形式** (Phase 1 手書き `starrydata.py`) — `paper_sid = row.get("SID")` → `sample_key = f"{paper_sid}-{sample_id}"` → `sdr[f"sample/{sample_key}"]`。placeholder はローカル変数なので **代入を何 hop でも辿って** `row["COL"]` / `row.get("COL")` まで戻す。

`validate.py` の `_check_t1_uniqueness` を、MIE template だけでなく **ingester 由来のキーも uniqueness 検証**するよう拡張。これで Round 3 の「ingester が正しい複合キーでも T1 は warn 止まり」が **warn → pass / fail** に変わる。

### 設計上の罠を 2 つ自力で踏んで直した

- **保守的な未解決扱い**: 二次リソース `descriptor/{sample_key}/{i}` の loop index `i` や `ingestion/{run_id}` の `run_id` は列に解決できない。これらを推測せず `unresolved` に記録し、**`fully_resolved` なキーだけ uniqueness にかける**。→ 二次リソースが偽陽性 fail を出さない。
- **entity による CSV 選択** (実 CLI dogfood で発見した偽陽性): `sdr:paper/{SID}` の単独キーを、`SID` 列を持つ最初の CSV (= `samples.csv`、そこでは SID が設計上重複する) で検証すると **偽 fail** する。entity 名でマッチする CSV (`paper`→`papers.csv`) を優先選択するようにして解消。実 `starrydata.py` + クリーン CSV で `sdr:paper (SID)` が `papers.csv` に振られ pass することを確認した。

### 検証

- step0 全 test pass (96+ → **111**)。新規 unit test = 抽出器 9 件 (`test_t1_ingester.py`) + T1 統合 6 件 (`test_validate.py`)
- 実 `ingest/src/csv2rdf/starrydata.py` を CLI `csv2rdf-validate --ingester` にかけ、`paper`/`sample`/`curve` の複合キーを正しく復元・検証することを end-to-end 確認
- **副産物**: 実 starrydata.py の `sdr:paper/{SID}` が単独キーであることを T1 が自動で可視化した。残タスク「papers.csv の SID 28 collisions」(全件) はこの安全網が今後 catch する

→ 「次の Round で試すこと」**項目 5 完了**。

---

## 次の Round で試すこと

1. ~~streaming 修正後に再 propose~~ ✅ Round 2 完了
2. ~~domain hint に collision 知見を注入~~ ✅ Round 2 完了
3. **全件 inspect → propose** — uniqueness を全件で見せれば hint 無しでも複合キーを選ぶか (コスト増とのトレードオフ)
4. ~~propose → refine round-trip~~ ✅ Round 3 完了
5. ~~T1 を ingester の IRI builder から検証する強化~~ ✅ 実装完了 (2026-05-30、上記「T1 ingester-builder 検証強化」)
6. ~~validate を CI に統合~~ ✅ 完了 (2026-05-30、PR #31)。`step0/tests/fixtures/starrydata_min/` の極小 bundle を commit し、pytest + CI step で 8 罠を end-to-end 検証 (API キー不要)
7. **rdf-config 連携自動化** — materialize → `--shex` → MIE merge
8. **別 dataset** (NIMS Supercon 等) で全 loop

---

## 蓄積した運用ルール (propose を使う人向け)

- subset で品質を見るのは OK。だが **IRI key の uniqueness は全件 inspect で確定**する
- domain hint に **ドメイン固有の落とし穴** (id の重複、synonym、単位) を書くほど proposal が良くなる
- proposal は **必ず Trade-offs / Re-evaluation を読む** — LLM が自分で「ここは仮定」と書いた箇所が人間の確認ポイント
- materialize 後は **rdf-config で shape_expressions 生成 → MIE merge → validate** の順で締める
