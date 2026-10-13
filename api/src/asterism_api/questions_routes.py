"""⑥「自分の問い」— ``questions.json`` の下書きと、公開時のツール化
（ADR upper-structure-shared-terms.md §2.3「⑥ の問いの規律」・契約 handoff §5.3）。

- ``GET``/``PUT /api/datasets/{id}/questions`` — 下書き（registry artifact ``questions.json``）。
  形は ``{"version": 1, "questions": [{id, title, kind_iri?, property_iri?, op, lint_error?}]}``
  （``handles.json`` と同じ wrapper。読み手は bare list も受ける）。
- ``build_question_sparql`` / ``question_tool`` — 閉じた選択（``op`` = 件数 ``count`` /
  範囲 ``range`` /
  上位の値 ``top``・種類・項目）から決定論のテンプレートに流し込む。**LLM なし・自由記述の
  SPARQL なし**。IRI は検査してから埋め込む（文字列連結の注入なし）。
- ``apply_questions`` — 公開（``promote`` と名前だけの公開）が呼ぶ。問いを ``q_<hash8>`` の
  宣言ツールにして ``query_tools.yaml`` へ**名前で upsert／削除**する（予約 3 本の置換規則を持つ
  ``write_registry_query_tools`` とは別の関数）。lint を通らないものは ``lint_error`` を下書きに
  書いてツール化しない。

画面では staged の draft graph に対して同じテンプレートを走らせ（``graph=`` を渡す）、公開後は
canonical の FROM-merge で走る（``graph=None`` のテンプレート）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from asterism.query_tools import (
    SYNTHESIZED_TOOL_NAMES,
    lint_query_tool,
    parse_query_tools,
    upsert_registry_query_tools_by_name,
)
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from asterism_api import registry

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from asterism_api.main import Settings

__all__ = [
    "QUESTION_OPS",
    "QUESTION_ORIGIN",
    "RESERVED_TOOL_PREFIX",
    "apply_questions",
    "build_question_sparql",
    "clean_questions",
    "dump_questions",
    "is_reserved_tool_name",
    "load_questions",
    "question_tool",
    "question_tool_name",
    "register_questions",
    "selection_error",
]

logger = logging.getLogger(__name__)

QUESTION_OPS = ("count", "range", "top")
RESERVED_TOOL_PREFIX = "q_"
# ⑥の問いが生成したツールの印（query_tools.yaml の ``origin``）。公開時の削除はこの印のある
# ものだけ — 人が以前に保存した ``q_`` 名のツールは消さない。
QUESTION_ORIGIN = "question"
_XSD = "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
# SPARQL の ``<...>`` に安全に埋め込める絶対 IRI（http(s) のみ・区切り文字なし）。
_IRI_RE = re.compile(r'^https?://[^\s<>"{}|\\^`]+$', re.IGNORECASE)
_ID_MAX = 128


def is_reserved_tool_name(name: str) -> bool:
    """人の保存経路（``POST /api/datasets/{id}/tools``）が使えない名前か。

    自動の 3 本（``SYNTHESIZED_TOOL_NAMES``）と、⑥ の問いが公開で使う ``q_`` 接頭辞。
    """
    return name in SYNTHESIZED_TOOL_NAMES or name.startswith(RESERVED_TOOL_PREFIX)


def question_tool_name(question_id: str) -> str:
    """``q_<sha1(id)[:8]>`` — 下書きの id から決まる、公開ツールの名前。"""
    return RESERVED_TOOL_PREFIX + hashlib.sha1(question_id.encode("utf-8")).hexdigest()[:8]


def selection_error(op: Any, kind_iri: Any, property_iri: Any) -> str | None:
    """閉じた選択の検査。問題があれば理由、無ければ ``None``。

    ``count`` は種類が要る。``range`` / ``top`` は項目が要る（種類は絞り込みで任意）。
    IRI は埋め込む前に形を検査する。
    """
    if op not in QUESTION_OPS:
        return f"op は {list(QUESTION_OPS)} のどれかです"
    for label, value in (("kind_iri", kind_iri), ("property_iri", property_iri)):
        if value is not None and not (isinstance(value, str) and _IRI_RE.match(value)):
            return f"{label} は http(s) の IRI にしてください"
    if op == "count" and not kind_iri:
        return "件数の問いには種類 (kind_iri) が要ります"
    if op in ("range", "top") and not property_iri:
        return "範囲・上位の値の問いには項目 (property_iri) が要ります"
    return None


# ---------------------------------------------------------------------------
# 決定論のテンプレート（閉じた選択 → SPARQL）
# ---------------------------------------------------------------------------


def _in_graph(graph: str | None, body: str) -> str:
    return f"GRAPH <{graph}> {{ {body} }}" if graph else body


def _numeric_pattern(
    subject: str, kind_iri: str | None, property_iri: str, suffix: str = ""
) -> str:
    """``subject`` の ``property_iri`` の値を数として読む（キャストできないものは落ちる）。

    数は ``xsd:double(str(?v))`` で見分ける（``isNumeric`` ではない）— 実データは数を文字列
    リテラルで持つことが多い。``/trial-queries`` と同じ規則。``suffix`` は変数名
    （``?v`` / ``?num``）に付ける。範囲の問いは副問い合わせを 3 つ持つので、変数を共有させない
    （共有すると rdflib の副問い合わせが集計を落とす）。
    """
    kind = f"{subject} a <{kind_iri}> . " if kind_iri else ""
    return (
        f"{kind}{subject} <{property_iri}> ?v{suffix} FILTER(isLiteral(?v{suffix})) "
        f"BIND(xsd:double(str(?v{suffix})) AS ?num{suffix}) FILTER(BOUND(?num{suffix}))"
    )


def build_question_sparql(question: dict, *, graph: str | None = None) -> str:
    """問い 1 つの SPARQL（決定論）。``graph`` を渡すと staged の 1 graph に限る（⑥の画面）。
    ``None`` は宣言ツール用 — canonical の FROM-merge の上を読む。

    不正な選択（:func:`selection_error`）は ``ValueError``。
    """
    op = question.get("op")
    kind_iri = question.get("kind_iri") or None
    property_iri = question.get("property_iri") or None
    problem = selection_error(op, kind_iri, property_iri)
    if problem:
        raise ValueError(problem)
    if graph is not None and not _IRI_RE.match(graph):
        raise ValueError("graph は http(s) の IRI にしてください")
    if op == "count":
        return (
            "SELECT ?class (COUNT(DISTINCT ?s) AS ?n) WHERE { "
            f"VALUES ?class {{ <{kind_iri}> }} "
            f"{_in_graph(graph, '?s a ?class')} "
            "} GROUP BY ?class ORDER BY DESC(?n) ?class"
        )
    assert property_iri is not None
    if op == "range":
        # 範囲は集計で、1 行がその根拠ではない。だが最小・最大を持つ記録は IRI で引用できる
        # （自動の value_range と同じ形。同値は主語の IRI 昇順で決める）。
        def pat(subject: str, suffix: str = "") -> str:
            return _in_graph(graph, _numeric_pattern(subject, kind_iri, property_iri, suffix))

        return (
            f"{_XSD}SELECT ?n ?min ?max ?minSubject ?maxSubject WHERE {{ "
            "{ SELECT (COUNT(?num) AS ?n) (MIN(?num) AS ?min) (MAX(?num) AS ?max) "
            f"WHERE {{ {pat('?s')} }} }} "
            f"OPTIONAL {{ SELECT ?minSubject WHERE {{ {pat('?minSubject', 'Lo')} }} "
            "ORDER BY ASC(?numLo) ASC(?minSubject) LIMIT 1 } "
            f"OPTIONAL {{ SELECT ?maxSubject WHERE {{ {pat('?maxSubject', 'Hi')} }} "
            "ORDER BY DESC(?numHi) ASC(?maxSubject) LIMIT 1 } "
            "}"
        )
    return (  # top
        f"{_XSD}SELECT ?s ?v WHERE {{ "
        f"{_in_graph(graph, _numeric_pattern('?s', kind_iri, property_iri))} "
        "} ORDER BY DESC(?num) ?s LIMIT 1"
    )


def question_tool(question: dict, *, unit: str | None = None) -> dict[str, Any]:
    """問いを ``query_tools.yaml`` の宣言ツール（raw dict）にする。名前は ``q_<hash8>``。

    題は人が書いたものをそのまま使う。結果の形・``output_kind`` は自動の 3 本
    （``counts_by_kind`` / ``value_range`` / ``top_value``）と同じ。
    """
    op = question["op"]
    title = str(question.get("title") or "").strip()
    tool: dict[str, Any] = {
        "name": question_tool_name(str(question["id"])),
        "origin": QUESTION_ORIGIN,
        "title": title,
        "description": (
            f"利用者の問い「{title}」に、画面で選んだ種類・項目から決まる集計で答えます。"
        ),
        "parameters": [],
        "query": build_question_sparql(question),
    }
    if op == "count":
        tool["output_kind"] = "breakdown"
        tool["result"] = {
            "item": {
                "class_iri": {"var": "class", "number": False, "role": "category"},
                "count": {"var": "n", "number": True, "role": "count"},
            }
        }
    elif op == "range":
        tool["output_kind"] = "facts"
        tool["result"] = {
            "item": {
                "count": {"var": "n", "number": True},
                "min": {"var": "min", "number": True},
                "max": {"var": "max", "number": True},
                "min_subject_iri": "minSubject",
                "max_subject_iri": "maxSubject",
            }
        }
    else:
        value_spec: dict[str, Any] = {"var": "v", "number": True, "role": "value"}
        if unit:
            value_spec["unit"] = unit
        tool["output_kind"] = "quantity"
        tool["result"] = {
            "item": {
                "subject_iri": {"var": "s", "number": False, "role": "subject"},
                "value": value_spec,
            }
        }
    return tool


# ---------------------------------------------------------------------------
# questions.json の読み書き
# ---------------------------------------------------------------------------


def clean_questions(raw: Any) -> list[dict]:
    """生の値 → 検査を通る問いだけの list（不正・重複 id は静かに落とす）。

    読み手（壊れていても ``[]``）と materialize の受け口が使う。厳密に 422 を返したい
    ``PUT`` は自前で検査する。
    """
    if isinstance(raw, dict):
        raw = raw.get("questions")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        qid = item.get("id")
        title = item.get("title")
        if not (isinstance(qid, str) and qid.strip() and len(qid) <= _ID_MAX) or qid in seen:
            continue
        if not (isinstance(title, str) and title.strip()):
            continue
        kind_iri = item.get("kind_iri") or None
        property_iri = item.get("property_iri") or None
        if selection_error(item.get("op"), kind_iri, property_iri):
            continue
        row: dict[str, Any] = {"id": qid, "title": title.strip(), "op": item["op"]}
        if kind_iri:
            row["kind_iri"] = kind_iri
        if property_iri:
            row["property_iri"] = property_iri
        lint_error = item.get("lint_error")
        if isinstance(lint_error, str) and lint_error:
            row["lint_error"] = lint_error
        seen.add(qid)
        out.append(row)
    return out


def load_questions(artifacts: dict[str, str]) -> list[dict]:
    """``artifacts["questions.json"]`` → 問いの list（無い・壊れている・形違いは ``[]``）。"""
    text = (artifacts.get("questions.json") or "").strip()
    if not text:
        return []
    try:
        return clean_questions(json.loads(text))
    except ValueError:
        return []


def dump_questions(questions: Sequence[dict]) -> str:
    return json.dumps({"version": 1, "questions": list(questions)}, ensure_ascii=False, indent=2)


def _lint_errors(question: dict, unit_of: Callable[[str], str | None] | None = None) -> list[str]:
    """問いのツールが parse と lint を通るか。通らなければ理由。"""
    unit = unit_of(question["property_iri"]) if unit_of and question.get("property_iri") else None
    try:
        tool = question_tool(question, unit=unit)
        parsed = parse_query_tools({"tools": [tool]})
    except Exception as exc:  # ValueError / QueryToolError — 理由がそのまま使える
        return [str(exc)]
    return list(lint_query_tool(parsed[0]).errors)


def apply_questions(
    registry_root: Path,
    dataset_id: str,
    *,
    unit_of: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """公開で ``questions.json`` を ``query_tools.yaml`` の ``q_`` ツールにする。

    - 問いごとに lint。落ちたものは ``lint_error`` を下書きに書き、ツール化しない（古い
      ``q_`` ツールが残っていれば、下の「無いものは消す」で一緒に消える）。通ったものは
      ``lint_error`` を消す。
    - ``upsert_registry_query_tools_by_name(…, remove_missing_prefix="q_",
      remove_missing_origin="question")`` で**名前による upsert と削除**。削除は ``origin:
      question`` の印があるものだけ — 予約 3 本・人の保存したツール（印なし）は触らない。
      既存 yaml が読めなければ書かず、``warnings`` に載せる。
    - 何も書くものが無く ``query_tools.yaml`` も無いときは、空のファイルを作らない。

    例外は投げない（呼ぶ側は公開を止めない）。戻り値 ``{"written": [name…], "lint_errors":
    [{"id", "errors"}…], "warnings": [str…]}``。
    """
    report: dict[str, Any] = {"written": [], "lint_errors": [], "warnings": []}
    try:
        data = registry.load_dataset(registry_root, dataset_id)
        if data is None:
            return report
        questions = load_questions(data.get("artifacts") or {})
        tools: list[dict[str, Any]] = []
        changed = False
        for q in questions:
            errors = _lint_errors(q, unit_of)
            if errors:
                message = "; ".join(errors)
                if q.get("lint_error") != message:
                    q["lint_error"] = message
                    changed = True
                report["lint_errors"].append({"id": q["id"], "errors": errors})
                continue
            if q.pop("lint_error", None) is not None:
                changed = True
            unit = unit_of(q["property_iri"]) if unit_of and q.get("property_iri") else None
            tools.append(question_tool(q, unit=unit))
        if changed:
            registry.write_artifact(
                registry_root, dataset_id, "questions.json", dump_questions(questions)
            )
        path = registry.query_tools_path(registry_root, dataset_id)
        if not tools and not (path is not None and path.is_file()):
            return report
        rejected = upsert_registry_query_tools_by_name(
            registry_root,
            dataset_id,
            tools,
            remove_missing_prefix=RESERVED_TOOL_PREFIX,
            remove_missing_origin=QUESTION_ORIGIN,
        )
        if rejected is None:
            report["warnings"].append(
                "query_tools.yaml が読めないため、問いをツールにできませんでした"
            )
            return report
        report["written"] = [t["name"] for t in tools if t["name"] not in rejected]
        if rejected:
            report["warnings"].append(f"問いが検査を通りません: {', '.join(rejected)}")
    except Exception as exc:  # 公開は止めない
        logger.warning("questions: apply failed for %s", dataset_id, exc_info=True)
        report["warnings"].append(f"問いのツール化に失敗しました: {exc}")
    return report


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


class QuestionItem(BaseModel):
    id: str
    title: str
    op: Literal["count", "range", "top"]
    kind_iri: str | None = None
    property_iri: str | None = None
    # 受け取るが使わない — lint_error は検査の結果としてサーバが書く（クライアントの値は信じない）。
    lint_error: str | None = None


class QuestionsBody(BaseModel):
    questions: list[QuestionItem]


def register_questions(
    app: FastAPI,
    cfg: Settings,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """``GET``/``PUT /api/datasets/{dataset_id}/questions`` を ``app`` に登録する。

    書き込みの認証は呼ぶ側（``build_app``）が渡す ``write_auth``（``register_vocab`` と同じ）。
    """

    @app.get("/api/datasets/{dataset_id}/questions")
    async def get_questions(dataset_id: str) -> dict:
        record = registry.load_dataset(cfg.registry_root, dataset_id)
        if record is None:
            raise HTTPException(404, "unknown dataset_id")
        return {"questions": load_questions(record["artifacts"])}

    @app.put("/api/datasets/{dataset_id}/questions", dependencies=list(write_auth))
    async def put_questions(dataset_id: str, body: QuestionsBody) -> dict:
        """問いの下書きを置き換える（送れば置換）。公開までは ``query_tools.yaml`` に何も書かない。

        id が重複・空、題が空、選択（種類・項目）が op に合わない、は 422。新しい id の問いが
        lint を通らないときも 422（保存しない）。すでにある id の問いが通らないときは
        ``lint_error`` を付けて残す（再設計で IRI が変わったものを下書きから失わない）。
        """
        record = registry.load_dataset(cfg.registry_root, dataset_id)
        if record is None:
            raise HTTPException(404, "unknown dataset_id")
        known = {q["id"] for q in load_questions(record["artifacts"])}
        rows: list[dict] = []
        seen: set[str] = set()
        for item in body.questions:
            qid = item.id.strip()
            if not qid or len(qid) > _ID_MAX:
                raise HTTPException(422, "問いの id は 1〜128 文字にしてください")
            if qid in seen:
                raise HTTPException(422, f"問いの id が重複しています: {qid}")
            seen.add(qid)
            if not item.title.strip():
                raise HTTPException(422, "問いの題が空です")
            problem = selection_error(item.op, item.kind_iri or None, item.property_iri or None)
            if problem:
                raise HTTPException(422, problem)
            row: dict[str, Any] = {"id": qid, "title": item.title.strip(), "op": item.op}
            if item.kind_iri:
                row["kind_iri"] = item.kind_iri
            if item.property_iri:
                row["property_iri"] = item.property_iri
            errors = _lint_errors(row)
            if errors:
                if qid not in known:
                    raise HTTPException(422, f"問いが検査を通りません: {'; '.join(errors)}")
                row["lint_error"] = "; ".join(errors)
            rows.append(row)
        registry.write_artifact(
            cfg.registry_root, dataset_id, "questions.json", dump_questions(rows)
        )
        return {"questions": rows}
