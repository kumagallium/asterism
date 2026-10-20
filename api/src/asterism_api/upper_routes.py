"""③⑤ の当てはめ（``upper.json``）— 人が受けた「同じ項目／種類の一種」を、公開で線にする
（ADR upper-structure-shared-terms.md §2.5.3・契約 handoff §5.5）。

- ``GET``/``PUT /api/datasets/{id}/upper`` — 下書き（registry artifact ``upper.json``）。
  形は ``{"version": 1, "upper": [{subject, term, relation, applied_at, skipped?}]}``
  （``handles.json`` と同じ wrapper。読み手は bare list も受ける）。
- ``apply_upper`` — 公開（``promote`` と名前だけの公開）が呼ぶ。``applied_at`` が空の項目だけ
  ``assert_alignment`` で線にして ``applied_at`` を書き戻す。**1 回だけ消費**する —
  人が「知りたいこと」で線を取り消しても、再公開で復活しない（``applied_at`` が残っているため）。

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
from asterism.crosswalk_runtime import BATCH_MAX, REASON_MAX, assert_alignment
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

    def carriers(maps) -> list[str]:
        out: list[str] = []
        for tm in maps:
            for prop in tm.properties:
                if prop.predicate and (prop.column == column or column in prop.columns):
                    iri = expand(prefixes, prop.predicate)
                    if _IRI_RE.match(iri) and iri not in out:
                        out.append(iri)
        return out

    named = [tm for tm in view.maps if tm.name == map_name]
    if not named:
        # 名指した map 自体が無い（作り直しで消えた）。推測で補わない。
        return None
    found = carriers(named)
    if found:
        return found[0]
    # 名指した map にその列が無い（画面は列の持ち主を推測で決める。同じファイルの別の
    # map、例えば 1 件ごとの行の種類が持っていることがある — 実機 2026-10-09）。同じ
    # ファイルの map の中で、その列を運ぶ述語が**ちょうど 1 つ**に決まるときだけ使う。
    # 決まらなければ書かない（違う述語に線を引くより、書けなかったと言う方がよい）。
    sources = {tm.source for tm in named if tm.source}
    pool = [tm for tm in view.maps if tm.source in sources]
    found = carriers(pool)
    return found[0] if len(found) == 1 else None


def _key(item: dict) -> tuple[str, str, str]:
    return item["subject"], item["term"], item["relation"]


#: 来歴（会話で受けた線の「誰が・どのまとめで・なぜ」）。公開で ``assert_alignment`` に渡す。
#: 人が表で直に受けた線には付かない（``assert_alignment`` の既定 = ``human``）。
_PROVENANCE = ("drafted_by", "batch", "reason")
_UPPER_DRAFTED_BY = ("ai", "human")


def _clean_provenance(item: dict) -> dict[str, str]:
    """``drafted_by`` / ``batch`` / ``reason`` のうち、形の正しいものだけ（不正は静かに落とす）。
    ``batch`` は 1 行・``BATCH_MAX`` 字まで・制御文字なし、``reason`` は折って
    ``REASON_MAX`` 字まで。"""
    out: dict[str, str] = {}
    by = item.get("drafted_by")
    if by in _UPPER_DRAFTED_BY:
        out["drafted_by"] = by
    batch = item.get("batch")
    if isinstance(batch, str):
        b = batch.strip()
        if b and len(b) <= BATCH_MAX and not any(ord(c) < 32 for c in b):
            out["batch"] = b
    reason = item.get("reason")
    if isinstance(reason, str):
        r = " ".join(reason.split())
        if r:
            out["reason"] = r[:REASON_MAX]
    return out


def clean_upper(raw: Any) -> list[dict]:
    """生の値 → 形の正しい項目だけの list（不正・重複は静かに落とす）。

    ``applied_at`` は文字列だけ、``skipped`` は文字列だけ残す。来歴（``drafted_by`` / ``batch`` /
    ``reason``）は形の正しいものだけ残す。
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
        row.update(_clean_provenance(item))
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
    消費済みなら ``applied_at`` を残す — 消費済みの線を PUT で「未消費」に戻せない。同じ線が
    既存で未消費かつ見送り済み（``skipped``）なら、その理由も残す（別の線を足しただけで、
    見送りの理由が消えない）。一覧から外した項目は消え、あとで入れ直せば新しい（未消費の）
    項目になる。

    ``remove: true`` の項目は「この線を外す」の印: 同じ（subject, term, relation）の**未消費**の
    既存項目を消す（会話で受けた当てはめを外す）。**消費済み**（``applied_at`` あり）は消さない
    （もう線になっている — 外すのは線の側の操作）。印だけを送った（印でない項目が 1 つも無い）
    ときは置換でなく「その線だけ消す」— ほかの既存項目はそのまま残す。
    """
    removes = {_key(i) for i in clean_upper([x for x in incoming if x.get("remove") is True])}
    incoming = [x for x in incoming if x.get("remove") is not True]
    if removes and not incoming:
        incoming = list(existing)  # 印だけ → 既存を土台に、その線だけ消す
    applied = {_key(e): e["applied_at"] for e in existing if e.get("applied_at")}
    skipped = {
        _key(e): e["skipped"]
        for e in existing
        if not e.get("applied_at") and isinstance(e.get("skipped"), str) and e["skipped"]
    }
    # 来歴は、送られたものを優先し、無ければ同じ線の既存のものを引き継ぐ（表の PUT が、
    # 会話で受けた線の来歴を落とさない）。
    kept = {_key(e): _clean_provenance(e) for e in existing}
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in clean_upper(list(incoming)):
        k = _key(item)
        if k in seen:
            continue
        seen.add(k)
        if k in removes and k not in applied:
            continue  # 外す（未消費だけ）
        row = {
            "subject": item["subject"],
            "term": item["term"],
            "relation": item["relation"],
            "applied_at": applied.get(k),
        }
        if k not in applied and k in skipped:
            row["skipped"] = skipped[k]
        row.update(_clean_provenance(item) or kept.get(k, {}))
        out.append(row)
    # 消費済みの線を外す印は何もしない — 置換で送り忘れていても、消費済みの線は残す
    for e in clean_upper(list(existing)):
        k = _key(e)
        if k in removes and k in applied and k not in seen:
            seen.add(k)
            out.append({**e, "applied_at": applied[k]})
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
                await assert_alignment(
                    client,
                    subject,
                    item["term"],
                    item["relation"],
                    at=now,
                    drafted_by=item.get("drafted_by") or "human",
                    batch=item.get("batch"),
                    reason=item.get("reason"),
                )
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
    # 会話（作業場の「そう」）で受けた線の来歴。公開で ``xw:`` の来歴になる。
    drafted_by: Literal["ai", "human"] | None = None
    batch: str | None = None
    reason: str | None = None
    # true = この線を外す（未消費の同じ線を消す・消費済みは消さない）。
    # 印だけを送れば、その線だけ消える。
    remove: bool | None = None


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
