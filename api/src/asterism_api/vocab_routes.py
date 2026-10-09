"""``register_vocab(app, cfg, write_auth=…)`` — 共有の言葉（上位構造）の api
（ADR upper-structure-shared-terms.md §2.1〜§2.5・契約 handoff §5/§6）。

- ``GET /api/vocab/shared`` — 鋳造済みの語の一覧（下に掛かるもの・標準との線・問い・答える数）
- ``POST /api/vocab/shared`` — 語を 1 つ作る（問いを添えて 1 操作。問い無しは 422・同 slug は 409）
- ``DELETE /api/vocab/shared/{slug}`` — 語を外す（線が残っていれば 409・問いは連動して消える）
- ``POST /api/vocab/shared/{slug}/cq`` — 既定テンプレの問いを 1 本足す。``{from_dataset,
  question_id}`` を送ると、他データセットの問い（``questions.json``）の種類／項目を上位の共有語に
  置き換えて足す（「ことばへ写す」・元は残す・上位がその語でなければ 409）
- ``GET /api/vocab/upper`` — 全体グラフ向けの上位の対応表（線だけから作る）
- ``GET /api/vocab/fit`` — 列を当てはめられる語の候補（完全一致のみ・表示専用）

``shared_vocab``（ingest）は決定論・LLM なし。ここは受け取りと HTTP への写像だけを持つ。
書き込みの認証は呼ぶ側（``build_app``）が渡す ``write_auth``（既存の ``/api/crosswalk/align``
と同じ依存）。読み取りは認証不要。``wired.json`` の数え直しは ``mint_term`` / ``remove_term``
が自分でやる（二重に呼ばない）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from asterism import grounding, shared_vocab, substrate
from asterism.query_tools import read_registry_raw_tools, upsert_registry_query_tools_by_name
from asterism.shared_vocab import CQSpec, SharedTerm
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from asterism_api import registry
from asterism_api.questions_routes import load_questions

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

__all__ = ["register_vocab"]

logger = logging.getLogger(__name__)

# ground_terms の「完全一致」の点（exact = 100）。これ未満は標準の語として出さない。
_EXACT_SCORE = 100

_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


class CQBody(BaseModel):
    """問い 1 つ。``op`` は閉じた選択（種類=``count``・項目=``values``）。"""

    title: str
    op: str


class DeclinedStandardBody(BaseModel):
    """「標準の語はあったが使わなかった」の記録（来歴。語の意味は変えない）。"""

    iri: str
    reason: str = ""


class SharedTermBody(BaseModel):
    slug: str
    kind: str
    label_ja: str
    label_en: str | None = None
    comment: str | None = None
    declined_standard: DeclinedStandardBody | None = None
    cqs: list[CQBody] = Field(default_factory=list)


class AddCqBody(BaseModel):
    # 既定テンプレの問いを足すときは title / op が要る（無ければ 422）。「写す」
    # （``from_dataset`` + ``question_id``）のときは題を省くと元の問いの題を使い、op は語の
    # 種類で決まる（種類＝count・項目＝values）ので送らなくてよい。
    title: str | None = None
    op: str | None = None
    from_dataset: str | None = None
    question_id: str | None = None


def _term_from_row(row: dict) -> SharedTerm:
    names = SharedTerm.__dataclass_fields__
    return SharedTerm(**{k: row[k] for k in names if k in row})


def register_vocab(
    app: FastAPI,
    cfg: Settings,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """Register the ``/api/vocab/*`` routes on ``app`` (called once from ``build_app``)."""

    def _client() -> OxigraphClient:
        return app.state.client

    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @app.get("/api/vocab/shared")
    async def vocab_shared_list() -> JSONResponse:
        try:
            terms = await shared_vocab.list_terms(_client(), cfg.registry_root)
        except Exception as exc:
            logger.warning("vocab list failed", exc_info=True)
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc
        return JSONResponse({"terms": terms})

    @app.post("/api/vocab/shared", dependencies=list(write_auth))
    async def vocab_shared_mint(body: SharedTermBody) -> JSONResponse:
        """語と最初の問いを**1 操作で**作る。問いが空なら 422（語だけ作って「何も答えない
        語」にしない）、同じ slug は 409、slug / kind の形が不正なら 422。"""
        try:
            term = await shared_vocab.mint_term(
                _client(),
                cfg.registry_root,
                slug=body.slug,
                kind=body.kind,
                label=body.label_ja,
                label_en=body.label_en,
                comment=body.comment,
                declined_standard=body.declined_standard.iri if body.declined_standard else None,
                declined_reason=body.declined_standard.reason if body.declined_standard else None,
                cqs=[CQSpec(title=c.title, op=c.op) for c in body.cqs],
                at=_now(),
            )
        except shared_vocab.TermExists as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:  # TermError も含む（問い無し・op 不一致・slug/kind 不正）
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:  # surface a store error
            logger.warning("vocab mint failed", exc_info=True)
            raise HTTPException(502, f"shared vocab write failed: {exc}") from exc
        return JSONResponse({"term": term}, status_code=201)

    @app.delete("/api/vocab/shared/{slug}", dependencies=list(write_auth))
    async def vocab_shared_remove(slug: str) -> Response:
        """語を外す。線（alignment）が 1 本でも残っていれば 409（先に線を外す）。"""
        try:
            await shared_vocab.remove_term(_client(), cfg.registry_root, slug)
        except shared_vocab.TermNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except shared_vocab.TermHasLines as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:  # slug の形が不正 / yaml が読めない
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.warning("vocab remove failed", exc_info=True)
            raise HTTPException(502, f"shared vocab write failed: {exc}") from exc
        return Response(status_code=204)

    def _find_question(dataset_id: str, question_id: str) -> dict:
        record = registry.load_dataset(cfg.registry_root, dataset_id)
        if record is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        found = next(
            (q for q in load_questions(record["artifacts"]) if q["id"] == question_id), None
        )
        if found is None:
            raise HTTPException(404, f"question {question_id!r} not found in {dataset_id!r}")
        return found

    async def _check_upper_is(question: dict, term_row: dict) -> None:
        """問いの種類／項目の上位が、この語であること（でなければ 409 と案内）。

        問いの IRI は種類（``kind_iri``）と項目（``property_iri``）。語の種類（class / property）に
        合う側を ``upper_map`` で上位に畳み、その上位が ``term_row`` の語と同じなら通す。
        """
        is_class = term_row["kind"] == "class"
        subject = question.get("kind_iri") if is_class else question.get("property_iri")
        where = "種類" if is_class else "項目"
        if not subject:
            raise HTTPException(
                409,
                f"この問いは{where}を選んでいないため、{where}の語「{term_row['slug']}」へは"
                "写せません。",
            )
        try:
            m = await shared_vocab.upper_map(_client())
        except Exception as exc:
            raise HTTPException(502, f"upper map failed: {exc}") from exc
        mapped = (m["classes"] if is_class else m["properties"]).get(str(subject), str(subject))
        if mapped != term_row["iri"]:
            raise HTTPException(
                409,
                f"この問いの{where}の上位が「{term_row['slug']}」ではありません"
                + (
                    "（上位に共有の語がありません）。先に「ことば」で語を作り、線を引いてください。"
                    if mapped == subject
                    else f"（上位は {mapped} です）。写す先の語を選び直してください。"
                ),
            )

    @app.post("/api/vocab/shared/{slug}/cq", dependencies=list(write_auth))
    async def vocab_shared_add_cq(slug: str, body: AddCqBody) -> JSONResponse:
        """既定テンプレの問いを 1 本足す（名前は ``cq_<slug>_<op>``・同じ op が既にあれば
        ``_2`` …）。

        ``from_dataset`` + ``question_id`` を送ると「ことばへ写す」: そのデータセットの
        ``questions.json`` の問いの種類（``kind_iri``）／項目（``property_iri``）を ``upper_map`` で
        上位の共有語に置き換え、その語が ``slug`` の語なら ``slug`` への問いとして足す（元の問いは
        データセットのツールとして残る＝コピー）。上位が ``slug`` の語でなければ 409 で案内する
        （語が無ければ「ことば」で鋳造、線が無ければ線を引く）。
        """
        copy_from = bool(body.from_dataset or body.question_id)
        if copy_from and not (body.from_dataset and body.question_id):
            raise HTTPException(422, "from_dataset と question_id は一緒に送ってください")
        if not copy_from and not (body.title and body.op):
            raise HTTPException(422, "title と op が要ります")
        try:
            iri = shared_vocab.term_iri(slug)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            rows = await shared_vocab.list_terms(_client(), cfg.registry_root)
        except Exception as exc:
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc
        row = next((r for r in rows if r["iri"] == iri), None)
        if row is None:
            raise HTTPException(404, f"{slug!r} ということばはありません")
        title = (body.title or "").strip()
        op = body.op or ""
        if copy_from:
            question = _find_question(str(body.from_dataset), str(body.question_id))
            await _check_upper_is(question, row)
            title = title or str(question["title"])
            op = "count" if row["kind"] == "class" else "values"
        term = _term_from_row(row)
        source = (
            {"dataset": str(body.from_dataset), "question_id": str(body.question_id)}
            if copy_from
            else None
        )
        if source is not None:
            # 同じ問いを同じ語へ写すのは 1 回だけ — 2 度目は新しく作らず既存を返す。
            for existing in (
                read_registry_raw_tools(cfg.registry_root, shared_vocab.REGISTRY_ID) or []
            ):
                if existing.get("source") == source and iri in (existing.get("for_terms") or []):
                    return JSONResponse(
                        {
                            "term": slug,
                            "cq": {
                                "tool_name": existing.get("name"),
                                "title": existing.get("title"),
                            },
                            "from": {
                                "dataset_id": body.from_dataset,
                                "question_id": body.question_id,
                            },
                            "existing": True,
                        },
                        status_code=200,
                    )
        taken = {c["tool_name"] for c in row.get("cqs", [])}
        spec = CQSpec(title=title, op=op)
        try:
            tool = None
            for n in range(1, len(taken) + 2):
                tool = shared_vocab.default_cq_tools(term, [spec] * n)[-1]
                if tool["name"] not in taken:
                    break
            assert tool is not None
        except shared_vocab.TermError as exc:
            raise HTTPException(422, str(exc)) from exc
        if source is not None:
            tool["source"] = source
        rejected = upsert_registry_query_tools_by_name(
            cfg.registry_root, shared_vocab.REGISTRY_ID, [tool]
        )
        if rejected is None:  # 読めない query_tools.yaml は上書きしない
            raise HTTPException(409, "query_tools.yaml が読めないため、問いを足せません")
        if rejected:
            raise HTTPException(422, f"問いが検査を通りません: {', '.join(rejected)}")
        out: dict[str, Any] = {
            "term": slug,
            "cq": {"tool_name": tool["name"], "title": tool["title"]},
        }
        if copy_from:
            out["from"] = {"dataset_id": body.from_dataset, "question_id": body.question_id}
        return JSONResponse(out, status_code=201)

    @app.get("/api/vocab/upper")
    async def vocab_upper() -> JSONResponse:
        """各 IRI を、線だけから決めた最上位の共有の語へ畳む対応表（全体グラフが読む）。"""
        try:
            m = await shared_vocab.upper_map(_client())
        except Exception as exc:
            logger.warning("upper_map failed", exc_info=True)
            raise HTTPException(502, f"upper map failed: {exc}") from exc
        return JSONResponse({"classes": m["classes"], "properties": m["properties"], "at": _now()})

    async def _dataset_terms(client: OxigraphClient) -> list[dict]:
        """他データセットの項目（ontology graph の class / property）と label。"""
        base = substrate.ONTOLOGY_GRAPH_BASE
        res = await client.sparql_select(
            "SELECT DISTINCT ?s ?t ?label ?g WHERE { GRAPH ?g { ?s a ?t ; "
            f"<{_RDFS}label> ?label }} VALUES ?t {{ <{_RDFS}Class> <{_RDF}Property> }} "
            f'FILTER(isIRI(?s) && STRSTARTS(STR(?g), "{base}")) }} ORDER BY ?g ?s'
        )
        out: list[dict] = []
        for b in res.get("results", {}).get("bindings", []):
            out.append(
                {
                    "iri": b["s"]["value"],
                    "label": b["label"]["value"],
                    "dataset_id": b["g"]["value"][len(base) :],
                    "kind": "class" if b["t"]["value"] == f"{_RDFS}Class" else "property",
                }
            )
        return out

    @app.get("/api/vocab/fit")
    async def vocab_fit(
        label: str = Query(default="", description="利用者が付けた表示名"),
        column: str = Query(default="", description="列名（英語のことが多い）"),
    ) -> JSONResponse:
        """この列を当てはめられる既存の語の候補。**完全一致だけ**（正規化後）で、曖昧な
        一致は出さない。表示専用 — 受けるかどうかは人が決める（受けて初めて線になる）。
        標準の語は列名で ``ground_terms`` を引いた完全一致（score == 100）だけ。"""
        if not label.strip() and not column.strip():
            raise HTTPException(400, "label or column is required")
        client = _client()
        try:
            rows = await shared_vocab.list_terms(client, cfg.registry_root)
            dataset_terms = await _dataset_terms(client)
        except Exception as exc:
            logger.warning("fit read failed", exc_info=True)
            raise HTTPException(502, f"fit read failed: {exc}") from exc
        standard_hits: list[dict] = []
        if column.strip():
            standard_hits = [
                {
                    "iri": c.iri,
                    "label": c.label or c.name,
                    "kind": c.kind,
                    "matched_by": "column",
                }
                for c in grounding.ground_terms(column, limit=8)
                if c.score == _EXACT_SCORE
            ]
        return JSONResponse(
            {
                "candidates": shared_vocab.fit_candidates(
                    label,
                    column,
                    shared_terms=[_term_from_row(r) for r in rows],
                    dataset_terms=dataset_terms,
                    standard_hits=standard_hits,
                )
            }
        )
