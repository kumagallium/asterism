"""③⑤ の当てはめ（``upper.json``）— 人が受けた「同じ項目／種類の一種」を、公開で線にする
（ADR upper-structure-shared-terms.md §2.5.3・契約 handoff §5.5）。

- ``GET``/``PUT /api/datasets/{id}/upper`` — 下書き（registry artifact ``upper.json``）。
  形は ``{"version": 1, "upper": [{subject, term, relation, applied_at, skipped?}]}``
  （``handles.json`` と同じ wrapper。読み手は bare list も受ける）。
- ``apply_upper`` — 公開（``promote`` と名前だけの公開）が呼ぶ。``applied_at`` が空の項目だけ
  ``assert_alignment`` で線にして ``applied_at`` を書き戻す。**1 回だけ消費**する — 人が「ことば」で
  線を取り消しても、再公開で復活しない（``applied_at`` が残っているため）。

``applied_at`` / ``skipped`` は**サーバが書く**値。``PUT`` で送られても信じず、同じ
``(subject, term, relation)`` の既存の ``applied_at`` を引き継ぐ（消費済みを未消費に戻せない）。
機械が決めるのは線にするかどうかの実行だけで、受けるかどうかは人が決める（提案は表示のみ）。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from asterism import substrate
from asterism.crosswalk_runtime import assert_alignment
from asterism.mapping_ir_read import BUILTIN_PREFIXES, MappingIRView, expand, read_mapping_ir
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from asterism_api import registry

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

__all__ = [
    "UPPER_RELATIONS",
    "apply_upper",
    "clean_upper",
    "dump_upper",
    "is_valid_subject",
    "load_upper",
    "merge_upper",
    "register_upper",
    "resolve_property_ref",
]

logger = logging.getLogger(__name__)

# 当てはめで引ける線: 項目は subPropertyOf / equivalentProperty、
# 種類は subClassOf / equivalentClass。
UPPER_RELATIONS = ("subClassOf", "equivalentClass", "subPropertyOf", "equivalentProperty")
_IRI_RE = re.compile(r'^https?://[^\s<>"{}|\\^`]+$', re.IGNORECASE)
# 項目の当てはめの subject: ``property:<map 名>/<列名>``。公開のとき mapping.yaml の列 → 述語で
# 述語 IRI に解決する（種類の subject は従来どおり IRI）。
_PROPERTY_REF_RE = re.compile(r"^property:([^/\s][^/]*)/(\S.*)$")


def is_valid_subject(subject: Any) -> bool:
    """subject が IRI か ``property:<map>/<列>`` か。"""
    return isinstance(subject, str) and bool(
        _IRI_RE.match(subject) or _PROPERTY_REF_RE.match(subject)
    )


def resolve_property_ref(view: MappingIRView, ref: str) -> str | None:
    """``property:<map>/<列>`` → 述語 IRI（prefix 展開済み）。解決できなければ ``None``。"""
    m = _PROPERTY_REF_RE.match(ref)
    if not m:
        return None
    map_name, column = m.group(1), m.group(2)
    prefixes = dict(BUILTIN_PREFIXES) | view.prefixes
    for tm in view.maps:
        if tm.name != map_name:
            continue
        for prop in tm.properties:
            if prop.predicate and (prop.column == column or column in prop.columns):
                iri = expand(prefixes, prop.predicate)
                return iri if _IRI_RE.match(iri) else None
    return None


def _key(item: dict) -> tuple[str, str, str]:
    return item["subject"], item["term"], item["relation"]


def clean_upper(raw: Any) -> list[dict]:
    """生の値 → 形の正しい項目だけの list（不正・重複は静かに落とす）。

    ``applied_at`` は文字列だけ、``skipped`` は文字列だけ残す。
    """
    if isinstance(raw, dict):
        raw = raw.get("upper")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        subject, term, relation = item.get("subject"), item.get("term"), item.get("relation")
        if not (
            isinstance(subject, str)
            and is_valid_subject(subject)
            and isinstance(term, str)
            and _IRI_RE.match(term)
            and relation in UPPER_RELATIONS
        ):
            continue
        row: dict[str, Any] = {
            "subject": subject,
            "term": term,
            "relation": relation,
            "applied_at": item.get("applied_at")
            if isinstance(item.get("applied_at"), str)
            else None,
        }
        if not row["applied_at"] and isinstance(item.get("skipped"), str) and item["skipped"]:
            row["skipped"] = item["skipped"]
        if _key(row) in seen:
            continue
        seen.add(_key(row))
        out.append(row)
    return out


def load_upper(artifacts: dict[str, str]) -> list[dict]:
    """``artifacts["upper.json"]`` → 項目の list（無い・壊れている・形違いは ``[]``）。"""
    text = (artifacts.get("upper.json") or "").strip()
    if not text:
        return []
    try:
        return clean_upper(json.loads(text))
    except ValueError:
        return []


def dump_upper(items: Sequence[dict]) -> str:
    return json.dumps({"version": 1, "upper": list(items)}, ensure_ascii=False, indent=2)


def merge_upper(existing: Sequence[dict], incoming: Sequence[dict]) -> list[dict]:
    """送られた一覧で置き換える（送れば置換）。ただし ``applied_at`` は既存から引き継ぐ。

    送られてきた ``applied_at`` / ``skipped`` は捨てる（サーバだけが書く）。同じ線が既存で
    消費済みなら ``applied_at`` を残す — 消費済みの線を PUT で「未消費」に戻せない。一覧から
    外した項目は消え、あとで入れ直せば新しい（未消費の）項目になる。
    """
    applied = {_key(e): e["applied_at"] for e in existing if e.get("applied_at")}
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in clean_upper(list(incoming)):
        k = _key(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(
            {
                "subject": item["subject"],
                "term": item["term"],
                "relation": item["relation"],
                "applied_at": applied.get(k),
            }
        )
    return out


async def _subject_in_ontology(client: OxigraphClient, dataset_id: str, subject: str) -> bool:
    """``subject`` がこのデータセットの ontology graph に居るか（種類・項目の IRI の解決）。"""
    graph = substrate.ontology_graph_iri(dataset_id)
    res = await client.sparql_select(f"ASK {{ GRAPH <{graph}> {{ <{subject}> ?p ?o }} }}")
    return bool(res.get("boolean")) if isinstance(res, dict) else False


async def apply_upper(
    client: OxigraphClient,
    registry_root: Path,
    dataset_id: str,
    *,
    at: str | None = None,
) -> dict[str, Any]:
    """公開で ``upper.json`` の未消費の項目を線にする（1 回だけ消費）。

    - ``applied_at`` が空の項目だけ処理する。subject がこのデータセットの ontology graph に
      無ければ ``skipped = "subject missing"``（未消費のまま — 次の公開でもう一度試す）。
    - ``assert_alignment`` が ``ValueError``（cycle・kind mismatch・unminted shared term …）を
      返したら ``skipped = <理由>`` を書き、未消費のまま残す。
    - 書けたら ``applied_at`` を記録して書き戻す。ストアの失敗は項目に何も書かず ``warnings``
      に載せる（次の公開でもう一度）。
    - 例外は投げない（呼ぶ側は公開を止めない）。

    戻り値 ``{"applied": n, "skipped": [{"subject", "term", "reason"}…], "warnings": [str…]}``。
    ``applied`` が 1 以上のとき、呼ぶ側は ``wired.json`` を数え直す。
    """
    report: dict[str, Any] = {"applied": 0, "skipped": [], "warnings": []}
    try:
        data = registry.load_dataset(registry_root, dataset_id)
        if data is None:
            return report
        pending = [i for i in load_upper(data.get("artifacts") or {}) if not i.get("applied_at")]
        if not pending:
            return report
        now = at or datetime.now(UTC).isoformat()
        view: MappingIRView | None = None
        if any(_PROPERTY_REF_RE.match(i["subject"]) for i in pending):
            try:
                view = read_mapping_ir((data.get("artifacts") or {}).get("mapping.yaml") or "")
            except ValueError:  # mapping.yaml が無い・壊れている → 項目は解決できない
                view = None
        results: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in pending:
            k = _key(item)
            try:
                subject = item["subject"]
                if _PROPERTY_REF_RE.match(subject):
                    resolved = resolve_property_ref(view, subject) if view else None
                    if resolved is None:
                        results[k] = {"skipped": "predicate missing"}
                        continue
                    subject = resolved
                if not await _subject_in_ontology(client, dataset_id, subject):
                    results[k] = {"skipped": "subject missing"}
                    continue
                await assert_alignment(client, subject, item["term"], item["relation"], at=now)
                results[k] = {"applied_at": now}
            except ValueError as exc:  # 閉じた規則で断られた（cycle など）
                results[k] = {"skipped": str(exc)}
            except Exception as exc:  # ストアの失敗 — 項目には何も書かず、次の公開で再試行
                logger.warning("upper: assert failed for %s", dataset_id, exc_info=True)
                report["warnings"].append(f"線を書けませんでした ({item['subject']}): {exc}")
        # 書き戻しは最新の一覧に重ねる（処理中に PUT された変更を消さない）。
        latest = load_upper(
            (registry.load_dataset(registry_root, dataset_id) or {}).get("artifacts") or {}
        )
        changed = False
        for row in latest:
            res = results.get(_key(row))
            if res is None or row.get("applied_at"):
                continue
            if "applied_at" in res:
                row["applied_at"] = res["applied_at"]
                row.pop("skipped", None)
                report["applied"] += 1
                changed = True
            elif row.get("skipped") != res["skipped"]:
                row["skipped"] = res["skipped"]
                changed = True
            if "skipped" in res:
                report["skipped"].append(
                    {"subject": row["subject"], "term": row["term"], "reason": res["skipped"]}
                )
        if changed:
            registry.write_artifact(registry_root, dataset_id, "upper.json", dump_upper(latest))
    except Exception as exc:  # 公開は止めない
        logger.warning("upper: apply failed for %s", dataset_id, exc_info=True)
        report["warnings"].append(f"当てはめの線を書けませんでした: {exc}")
    return report


class UpperItem(BaseModel):
    subject: str
    term: str
    relation: Literal["subClassOf", "equivalentClass", "subPropertyOf", "equivalentProperty"]
    # 受け取るが使わない — applied_at / skipped はサーバが書く値（merge_upper 参照）。
    applied_at: str | None = None
    skipped: str | None = None


class UpperBody(BaseModel):
    upper: list[UpperItem]


def register_upper(
    app: FastAPI,
    cfg: Settings,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """``GET``/``PUT /api/datasets/{dataset_id}/upper`` を ``app`` に登録する。"""

    @app.get("/api/datasets/{dataset_id}/upper")
    async def get_upper(dataset_id: str) -> dict:
        record = registry.load_dataset(cfg.registry_root, dataset_id)
        if record is None:
            raise HTTPException(404, "unknown dataset_id")
        return {"upper": load_upper(record["artifacts"])}

    @app.put("/api/datasets/{dataset_id}/upper", dependencies=list(write_auth))
    async def put_upper(dataset_id: str, body: UpperBody) -> dict:
        """当てはめの一覧を置き換える（送れば置換）。線を書くのは公開のときだけ。

        subject / term が http(s) の IRI でなければ 422。消費済み（``applied_at`` あり）の項目は
        同じ線を送っても消費済みのまま。
        """
        record = registry.load_dataset(cfg.registry_root, dataset_id)
        if record is None:
            raise HTTPException(404, "unknown dataset_id")
        for item in body.upper:
            if not (is_valid_subject(item.subject) and _IRI_RE.match(item.term)):
                raise HTTPException(
                    422,
                    "subject は http(s) の IRI か property:<map>/<列>、term は http(s) の IRI "
                    "にしてください",
                )
        merged = merge_upper(load_upper(record["artifacts"]), [i.model_dump() for i in body.upper])
        registry.write_artifact(cfg.registry_root, dataset_id, "upper.json", dump_upper(merged))
        return {"upper": merged}
