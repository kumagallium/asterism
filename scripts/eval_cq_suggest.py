#!/usr/bin/env python3
"""「問いを提案してもらう」の物差し（ui-redesign PR 10d・design.md §18.2）。任意実行（CI では回さない）。

金標準 ``docs/architecture/cq-gold/{starrydata,samples,papers}.yaml``（Bench4KE の ``ground-truth`` に準じた
``{source, gold_cqs[], expected_answers[]}``）の事例ごとに、``POST /api/consult/suggest-questions`` と
同じ道を**ライブラリ呼び出し**で実行する:

    asterism_step0.question_suggest.propose_questions（AI・閉じた JSON）
      → asterism.question_suggest.postprocess_questions（決定論の後処理・画面に出るもの）

そして ``gold_cqs`` との一致と「閉じた形に落ちた率」を数え、``results-<日付>.md`` に表で書く。

指標（意味の近さ＝埋め込みは使わない）:

* **完全一致** — ``normalize_label``（NFKC・小文字化・空白畳み）が同じ。
* **重なり** — 語の重なり Jaccard ≥ 0.5（ASCII の語は 1 語・かな漢字は 2 文字の並びを 1 語・記号は捨てる）。
* **答え方も一致** — 重なりで当たった問いの ``op`` と名前（kind / property / …）が ``expected_answers`` と同じ。
* **閉じた形に落ちた率** — 画面に出た問い ÷ AI が返した問い（後処理が落とした残り）。
* 再現率の分母は「閉じた形に落ちる想定」（``closed: true``）の gold だけ。op の合成で落ちない問いは入れない。

使い方（実 LLM・OpenAI 互換）::

    uv run --project api python scripts/eval_cq_suggest.py \\
        --base-url https://api.example.com/v1 --model <モデル名> --api-key <キー>
    # 環境変数でも渡せる: ASTERISM_EVAL_BASE_URL / ASTERISM_EVAL_MODEL / ASTERISM_EVAL_API_KEY（OPENAI_API_KEY）
    # 雛形だけ書く（LLM を呼ばない）: --template
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from asterism import themes
from asterism.question_suggest import postprocess_questions
from asterism.shared_vocab import SharedTerm, normalize_label
from asterism_step0.question_suggest import propose_questions

REPO = Path(__file__).resolve().parent.parent
GOLD_DIR = REPO / "docs" / "architecture" / "cq-gold"
GOLD_FILES = ("starrydata.yaml", "samples.yaml", "papers.yaml")
JACCARD_MIN = 0.5
#: 答え方の一致で見る欄（``expected_answers`` に書いてある欄だけ比べる）。
_SHAPE_FIELDS = ("kind", "property", "property2", "kind2", "target", "direction")

__all__ = [
    "JACCARD_MIN",
    "Case",
    "CaseResult",
    "aggregate",
    "evaluate_case",
    "jaccard",
    "load_cases",
    "main",
    "render_markdown",
    "tokens",
]


# ---------------------------------------------------------------------------
# 金標準
# ---------------------------------------------------------------------------


@dataclass
class Case:
    """金標準の事例 1 つ。"""

    file: str  # 金標準のファイル名（starrydata.yaml …）
    id: str
    source: dict[str, Any]
    gold_cqs: list[str]
    expected: list[dict[str, Any]]

    def closed_indexes(self) -> list[int]:
        return [i for i, e in enumerate(self.expected) if e.get("closed", True)]


def load_cases(gold_dir: Path = GOLD_DIR, files: tuple[str, ...] = GOLD_FILES) -> list[Case]:
    """金標準の YAML → 事例の一覧。``gold_cqs`` と ``expected_answers`` の長さが違えば ValueError。"""
    cases: list[Case] = []
    for name in files:
        path = gold_dir / name
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in doc.get("cases") or []:
            gold = [str(q) for q in raw.get("gold_cqs") or []]
            expected = list(raw.get("expected_answers") or [])
            if len(gold) != len(expected):
                raise ValueError(f"{name}:{raw.get('id')}: gold_cqs と expected_answers の数が違います")
            cases.append(
                Case(
                    file=name,
                    id=str(raw["id"]),
                    source=dict(raw.get("source") or {}),
                    gold_cqs=gold,
                    expected=expected,
                )
            )
    return cases


# ---------------------------------------------------------------------------
# 一致の物差し（決定論）
# ---------------------------------------------------------------------------

def tokens(text: str) -> set[str]:
    """語の重なり用の語の集合。NFKC・小文字化のあと、記号と空白を境に切り、ASCII の英数字の並びは
    1 語、かな・漢字などの並びは 2 文字ずつ（1 文字だけの並びはその 1 文字）を 1 語とする。"""
    s = unicodedata.normalize("NFKC", text).casefold()
    out: set[str] = set()
    word: list[str] = []  # ASCII の英数字の並び
    run: list[str] = []  # かな・漢字などの並び

    def flush() -> None:
        if word:
            out.add("".join(word))
        if len(run) == 1:
            out.add(run[0])
        else:
            out.update("".join(run[i : i + 2]) for i in range(len(run) - 1))
        word.clear()
        run.clear()

    for ch in s:
        if ch.isascii() and ch.isalnum():
            if run:
                flush()
            word.append(ch)
        elif not ch.isascii() and unicodedata.category(ch)[0] in ("L", "N", "M"):
            if word:
                flush()
            run.append(ch)
        else:  # 空白・記号・句読点（全角の「？」「、」も）
            flush()
    flush()
    return out


def jaccard(a: str, b: str) -> float:
    """2 つの文の語の重なり（Jaccard）。どちらかが語を持たなければ 0。"""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _best(gold: str, candidates: list[str]) -> tuple[float, int]:
    """``gold`` に最も重なる候補の（重なり, 添字）。候補が無ければ (0.0, -1)。"""
    best, at = 0.0, -1
    for i, c in enumerate(candidates):
        s = jaccard(gold, c)
        if s > best:
            best, at = s, i
    return best, at


def _same_shape(expected: dict[str, Any], row: dict[str, Any]) -> bool:
    """出た問いの答え方が ``expected_answers`` と同じか（op と、書いてある名前の欄）。"""
    if str(row.get("op") or "") != str(expected.get("op") or ""):
        return False
    for f in _SHAPE_FIELDS:
        want = expected.get(f)
        if want and normalize_label(str(want)) != normalize_label(str(row.get(f) or "")):
            return False
    return True


# ---------------------------------------------------------------------------
# 事例を 1 回流す
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: Case
    runs: int = 0
    errors: list[str] = field(default_factory=list)
    proposed: int = 0  # AI が返した問い（後処理の前）
    shown: int = 0  # 画面に出る問い（後処理のあと）
    gold_closed: int = 0  # 再現率の分母（閉じた形に落ちる想定の gold × 試行）
    exact: int = 0
    overlap: int = 0  # 重なり ≥ 0.5（完全一致を含む）
    shape: int = 0  # 重なりで当たり、答え方も同じ
    overlap_proposed: int = 0  # 後処理の前の AI の問いに対する重なり
    first_shown: list[dict[str, Any]] = field(default_factory=list)  # 1 回目に出た問い（点検用）
    first_matches: list[str] = field(default_factory=list)  # 同じ添字の印（完全／重なり／答え方も）


def _terms_of(src: dict[str, Any]) -> list[SharedTerm]:
    out = []
    for i, t in enumerate(src.get("terms") or []):
        label = str(t["label"])
        out.append(
            SharedTerm(
                iri=f"urn:cq-gold:{i}",
                slug=f"t{i}",
                kind=str(t.get("kind") or "property"),
                label=label,
                label_en=None,
                comment=None,
                created_at="",
            )
        )
    return out


def run_once(client: Any, case: Case, *, lang: str) -> tuple[list[dict], list[dict]]:
    """API の道と同じに 1 回流す → ``(AI が返した問い, 画面に出る問い)``。"""
    src = case.source
    screen = str(src.get("screen") or "dataset")
    terms = _terms_of(src)
    if screen == "dataset":
        cols = list(src.get("columns") or [])
        columns = [{"name": c["name"], "kind_hint": c.get("kind_hint") or ""} for c in cols]
        kind_names = [(str(k), []) for k in src.get("kinds") or []]
        prop_names = [(str(c.get("label") or c["name"]), [str(c["name"])]) for c in cols]
        standards = [s["label"] for s in themes.default_standards([c["name"] for c in columns])]
        text = None
        kinds = [k for k, _ in kind_names]
    else:  # paper（term は金標準に無い）
        columns, kind_names, prop_names, kinds = None, [], [], None
        standards = [s["label"] for s in themes.default_standards([])]
        text = str(src.get("text") or "")
    ai = propose_questions(
        client,
        screen=screen,
        columns=columns,
        dataset_label=str(src.get("dataset_label") or ""),
        existing_questions=[],
        terms=[{"label": t.label, "kind": t.kind} for t in terms],
        standards=standards,
        kinds=kinds,
        text=text,
        lang=lang,
    )
    shown = postprocess_questions(
        ai,
        kinds=kind_names,
        properties=prop_names,
        terms=terms,
        existing_questions=[],
        lang=lang,
    )
    return list(ai.get("questions") or []), shown


def evaluate_case(client: Any, case: Case, *, lang: str = "ja", runs: int = 1) -> CaseResult:
    """事例を ``runs`` 回流して数える。LLM の失敗・形の崩れは ``errors`` に書き、数えずに続ける。"""
    res = CaseResult(case=case)
    for n in range(runs):
        try:
            proposed, shown = run_once(client, case, lang=lang)
        except Exception as exc:  # noqa: BLE001 — AI の形の崩れ・接続の失敗（評価は続ける）
            res.errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
            continue
        res.runs += 1
        res.proposed += len(proposed)
        res.shown += len(shown)
        shown_q = [str(r["question"]) for r in shown]
        proposed_q = [str(q.get("question") or "") for q in proposed]
        marks = [""] * len(shown)
        for i in case.closed_indexes():
            gold = case.gold_cqs[i]
            res.gold_closed += 1
            score, at = _best(gold, shown_q)
            if any(normalize_label(gold) == normalize_label(q) for q in shown_q):
                res.exact += 1
            if score >= JACCARD_MIN:
                res.overlap += 1
                same = _same_shape(case.expected[i], shown[at])
                if same:
                    res.shape += 1
                if n == 0:
                    marks[at] = marks[at] or ("答え方も一致" if same else "重なり")
            if _best(gold, proposed_q)[0] >= JACCARD_MIN:
                res.overlap_proposed += 1
        if n == 0:
            res.first_shown = shown
            res.first_matches = marks
    return res


# ---------------------------------------------------------------------------
# 集計と書き出し
# ---------------------------------------------------------------------------

_SUM_FIELDS = ("proposed", "shown", "gold_closed", "exact", "overlap", "shape", "overlap_proposed")


def aggregate(results: list[CaseResult]) -> dict[str, int]:
    out = {f: sum(getattr(r, f) for r in results) for f in _SUM_FIELDS}
    out["cases"] = len(results)
    out["errors"] = sum(len(r.errors) for r in results)
    out["gold_all"] = sum(len(r.case.gold_cqs) for r in results)
    out["gold_closed_per_run"] = sum(len(r.case.closed_indexes()) for r in results)
    return out


def _rate(hit: int, total: int) -> str:
    return f"{hit}/{total} ({hit / total:.0%})" if total else "—"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def render_markdown(
    results: list[CaseResult],
    *,
    date: str,
    model: str,
    endpoint: str,
    lang: str,
    runs: int,
    measured: bool,
) -> str:
    """結果の Markdown。``measured=False`` は「未計測」の雛形（LLM を呼んでいない）。"""
    total = aggregate(results)
    na = "未計測"

    def v(text: str) -> str:
        return text if measured else na

    lines = [
        f"# 問いの提案の評価結果（{date}）",
        "",
        (
            "金標準 `docs/architecture/cq-gold/` に対する `scripts/eval_cq_suggest.py` の結果。"
            "指標の定義はスクリプトの冒頭と README を見る。"
        ),
        "",
        _row(["項目", "値"]),
        _row(["---", "---"]),
        _row(["日付", date]),
        _row(["モデル", model if measured else na]),
        _row(["接続先", endpoint if measured else na]),
        _row(["言語", lang]),
        _row(["試行（各事例を流した回数）", str(runs) if measured else na]),
        _row(["事例数", str(total["cases"])]),
        _row(
            [
                "gold の問い",
                f"{total['gold_all']} 本（閉じた形に落ちる想定 {total['gold_closed_per_run']} 本）",
            ]
        ),
        "",
        "## 全体",
        "",
        _row(["指標", "値"]),
        _row(["---", "---"]),
        _row(["完全一致の再現率（画面に出た問い）", v(_rate(total["exact"], total["gold_closed"]))]),
        _row(["重なり ≥ 0.5 の再現率（画面に出た問い）", v(_rate(total["overlap"], total["gold_closed"]))]),
        _row(["うち答え方も一致", v(_rate(total["shape"], total["gold_closed"]))]),
        _row(
            [
                "重なり ≥ 0.5 の再現率（後処理の前＝AI が返した問い）",
                v(_rate(total["overlap_proposed"], total["gold_closed"])),
            ]
        ),
        _row(["AI が返した問い／画面に出た問い", v(f"{total['proposed']} 本／{total['shown']} 本")]),
        _row(["閉じた形に落ちた率（出た ÷ 返した）", v(_rate(total["shown"], total["proposed"]))]),
        _row(["失敗した試行（接続・形の崩れ）", v(str(total["errors"]))]),
        "",
        "## 金標準ごと",
        "",
        _row(["金標準", "事例", "gold（想定）", "重なり ≥ 0.5", "答え方も一致", "閉じた形に落ちた率"]),
        _row(["---", "---:", "---:", "---:", "---:", "---:"]),
    ]
    for name in dict.fromkeys(r.case.file for r in results):
        rs = [r for r in results if r.case.file == name]
        a = aggregate(rs)
        lines.append(
            _row(
                [
                    f"`{name}`",
                    str(a["cases"]),
                    f"{a['gold_all']}（{a['gold_closed_per_run']}）",
                    v(_rate(a["overlap"], a["gold_closed"])),
                    v(_rate(a["shape"], a["gold_closed"])),
                    v(_rate(a["shown"], a["proposed"])),
                ]
            )
        )
    lines += [
        "",
        "## 事例ごと",
        "",
        _row(["事例", "gold（想定）", "返した", "出た", "完全一致", "重なり ≥ 0.5", "答え方も一致"]),
        _row(["---", "---:", "---:", "---:", "---:", "---:", "---:"]),
    ]
    for r in results:
        lines.append(
            _row(
                [
                    f"`{r.case.id}`",
                    f"{len(r.case.gold_cqs)}（{len(r.case.closed_indexes())}）",
                    v(str(r.proposed)),
                    v(str(r.shown)),
                    v(_rate(r.exact, r.gold_closed)),
                    v(_rate(r.overlap, r.gold_closed)),
                    v(_rate(r.shape, r.gold_closed)),
                ]
            )
        )
    if measured:
        lines += ["", "## 出た問い（1 回目・点検用）", ""]
        for r in results:
            lines.append(f"### `{r.case.id}`")
            lines.append("")
            lines.append("gold: " + " ／ ".join(r.case.gold_cqs))
            lines.append("")
            if r.errors:
                lines += [f"- 失敗: {e}" for e in r.errors]
            if not r.first_shown and not r.errors:
                lines.append("- （出た問いなし）")
            for row, mark in zip(r.first_shown, r.first_matches, strict=False):
                tail = f"（{mark}）" if mark else ""
                lines.append(f"- {row['question']} — {row.get('shape', '')}{tail}")
            lines.append("")
    else:
        lines += [
            "",
            "## 状態",
            "",
            (
                "**未計測**。実 LLM を回した日に `scripts/eval_cq_suggest.py` で上書きする"
                "（`--template` は LLM を呼ばず、この雛形だけを書く）。"
            ),
        ]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="問いの提案の物差し（金標準との一致と、閉じた形に落ちた率）。任意実行・CI では回さない。"
    )
    p.add_argument("--base-url", default=os.environ.get("ASTERISM_EVAL_BASE_URL"), help="OpenAI 互換の URL")
    p.add_argument("--model", default=os.environ.get("ASTERISM_EVAL_MODEL"), help="モデル名")
    p.add_argument(
        "--api-key",
        default=os.environ.get("ASTERISM_EVAL_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        help="API キー（結果のファイルには書かない）",
    )
    p.add_argument("--provider", default="openai", help="openai（互換）／anthropic（既定 openai）")
    p.add_argument("--lang", default="ja", choices=("ja", "en"), help="問いを書く言語（既定 ja）")
    p.add_argument("--runs", type=int, default=1, help="各事例を流す回数（既定 1）")
    p.add_argument("--max-tokens", type=int, default=None, help="1 回の出力の上限（弱いモデル向け）")
    p.add_argument("--no-stream", action="store_true", help="ストリームを使わない（スタブや古い互換サーバ向け）")
    p.add_argument("--gold-dir", type=Path, default=GOLD_DIR, help="金標準のフォルダ")
    p.add_argument("--only", default=None, help="この文字列を含む事例 ID だけ流す")
    p.add_argument("--date", default=None, help="結果に書く日付（既定は今日）")
    p.add_argument("--out", type=Path, default=None, help="書き出し先（既定 <gold-dir>/results-<日付>.md）")
    p.add_argument("--template", action="store_true", help="LLM を呼ばず「未計測」の雛形だけ書く")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    date = args.date or _dt.date.today().isoformat()
    cases = load_cases(args.gold_dir)
    if args.only:
        cases = [c for c in cases if args.only in c.id]
    if not cases:
        print("事例がありません", file=sys.stderr)
        return 2
    out = args.out or (args.gold_dir / f"results-{date}.md")

    if args.template:
        results = [CaseResult(case=c) for c in cases]
        md = render_markdown(
            results, date=date, model="", endpoint="", lang=args.lang, runs=0, measured=False
        )
        out.write_text(md, encoding="utf-8")
        print(f"雛形を書きました: {out}")
        return 0

    if not (args.base_url or args.api_key) or not args.model:
        print(
            "--base-url（または --api-key）と --model が要ります（環境変数 ASTERISM_EVAL_* でも可）。"
            " LLM を呼ばずに雛形だけ書くなら --template。",
            file=sys.stderr,
        )
        return 2
    if args.no_stream:
        os.environ["ASTERISM_LLM_STREAM"] = "0"
    from asterism_step0.llm import make_llm

    client = make_llm(
        args.provider,
        model=args.model,
        api_base=args.base_url,
        api_key=args.api_key,
        max_tokens=args.max_tokens,
    )
    results = []
    for c in cases:
        r = evaluate_case(client, c, lang=args.lang, runs=max(1, args.runs))
        results.append(r)
        print(
            f"{c.id}: 返した {r.proposed}・出た {r.shown}・重なり {r.overlap}/{r.gold_closed}"
            + (f"・失敗 {len(r.errors)}" if r.errors else "")
        )
    host = urlparse(args.base_url).netloc if args.base_url else args.provider
    md = render_markdown(
        results,
        date=date,
        model=args.model,
        endpoint=host or args.provider,
        lang=args.lang,
        runs=max(1, args.runs),
        measured=True,
    )
    out.write_text(md, encoding="utf-8")
    print(f"結果を書きました: {out}")
    return 0 if aggregate(results)["errors"] < len(cases) * max(1, args.runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
