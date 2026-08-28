# docs/reports — verification reports

Durable records of **product-meaningful verifications**: a milestone where we
checked a real product claim against evidence and want the result to survive as
more than a commit message or a ROADMAP line.

A report belongs here when the verification **materially de-risks or validates a
product claim** — e.g. "the closed Tier 0 function set is *enough* for arbitrary
datasets", "promotion stays memory-bounded at 12M triples", "the exposure switch
actually closes every raw-SPARQL surface". Routine green CI does **not** need a
report; a milestone that changes what we can confidently say about the product
does.

## How this differs from ADR / ROADMAP

| Where | Holds | Question it answers |
|---|---|---|
| `docs/architecture/` (ADR) | **decisions** | *What did we decide, and why?* |
| `docs/ROADMAP.md` | **execution state** | *What is done / next?* |
| `docs/reports/` (here) | **evidence** | *What did we verify, how, and what was the result?* |

## What a report contains

1. **Question** — the product claim under test, in one sentence.
2. **Method** — the corpus / harness / metric / gate, with links to the code and
   any ADR that defines the methodology.
3. **Result** — the numbers, with the run that produced them reproducible.
4. **Conclusion** — does the claim hold? what remains?
5. **Limitations** — honest scope of what the evidence does and does not show.
6. **Reproduce** — the exact commands.

Keep it short and evidence-first. Date every report; if a later run changes the
verdict, add a dated addendum rather than silently editing the numbers.

## Reports

| date | report | verdict |
|---|---|---|
| 2026-06-11 | [Tier 0 function library — sufficiency](tier0-coverage-sufficiency.md) | ✅ sufficient (gate PASS) |
| 2026-07-06 | [Mapping IR — deterministic RML compilation equivalence](mapping-ir-compiler-equivalence.md) | ✅ equivalent + syntax classes eliminated · ✅ live Qwen3.6/gpt-oss both converge (0 syntax issues in 5 runs) |
| 2026-07-07 | [↳ Phase 2a addendum — guided JSON surgical repair](mapping-ir-compiler-equivalence.md#addendum-2026-07-07--phase-2a-guided-json-surgical-repair-live-probe) | ✅ one guided call repairs all invention families; Sakura vLLM accepts json_schema natively |
| 2026-08-16 | [「確かめる」の人間クリックは機械が回せていなかったラウンド](traps-in-autocorrect-loop.md) | ✅ 実弱モデル出力 7/7 がトラップ不合格＝ループのオラクル漏れを確認 → T1-T10 を合流 |
| 2026-08-18 | [↳ addendum — v0.17.0 でまだ 5 クリック：列名 `No`→`False`・surgical 即停止・文法の穴・attach 前の盲目](traps-in-autocorrect-loop.md#addendum-2026-08-18--v0170-でまだ-5-クリック3-段目round-0-の崩壊4-つの構造欠陥) | ✅ 実データ: 解読不能 2 件 → 実列付き 17 件、transform 3 件は LLM 0 コールで消える。vLLM のスキーマ受理のみライブ未確認 |
| 2026-08-18 | [↳ addendum — ループが「値が 1 つも出ない設計」を合格にしていた](traps-in-autocorrect-loop.md) | ✅ 検査を「仕様の形」から「結果＝値が取り出せるか」へ転換。実データのループ出口が 0→1 issue で捕まる・型 1→7 本が LLM 0 コールで復活 |
| 2026-08-27 | [公開後に「データの数えかた」を直しても、配った引用は切れない](id-move-after-publish.md) | ✅ 実機一周: 旧 version graph 削除後も旧 ID が 2 件の行き先を返す・v1→v3 の 2 世代追跡・列欠落は公開前に警告。LLM 0 コール |
