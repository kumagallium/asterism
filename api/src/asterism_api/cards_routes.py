"""``register_cards(app, cfg)`` — the api half of object-cards-ui.md §3 (C1-tools).

Every route here is wired the way §0.1 requires: this module owns its own
``@app.get``/``@app.post`` decorators, never touches ``main.py``, and reads
``app.state.client`` / ``cfg.registry_root`` at request time exactly like the
routes ``main.py`` already defines. The **integration** step — calling
``register_cards(app, cfg)`` inside ``build_app`` — is left to the
integrator, per contract.

Deviation from the contract's literal wording (see this PR's ``notes``):
§0.1 says to "import ``main.py`` の ``require_write_auth``". That function is
a closure defined *inside* ``build_app`` (not a module-level name), so it
cannot literally be imported — and even if it could, importing anything
*from* ``main.py`` here would create a circular import once ``main.py``
imports ``register_cards`` from this module. Instead, :func:`_require_write_auth`
below reproduces the exact same fail-closed check (same messages, same
constant-time comparison) against the ``cfg`` this module already receives.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, TypeVar

import httpx
from asterism import class_schema as class_schema_mod
from asterism import query_tools as query_tools_mod
from asterism import subject_tools, substrate
from asterism import subjects as subjects_mod
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel

from asterism_api import appdata, describe

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_SUBJECTS_NAMESPACE = "subjects"

_MAX_SEARCH_LIMIT = 50


class CardsRunBody(BaseModel):
    """Body for ``POST /api/cards/run`` (§3.4): which subject, which tool."""

    subject: dict[str, Any]
    tool: str
    params: dict[str, Any] = {}


class SetsResolveBody(BaseModel):
    """Body for ``POST /api/sets/resolve`` (§3.4): a caller-supplied SetSpec."""

    spec: dict[str, Any]


def _snapshot_of(graph_iri: str) -> str | None:
    tail = graph_iri.rsplit("/", 1)[-1]
    return tail if tail.startswith("v") and tail[1:].isdigit() else None


async def _dataset_ranking_for_subject(
    client: Any, registry_root: Any, iri: str
) -> list[dict[str, Any]]:
    """``iri`` の三つ組を持つデータセットを、三つ組が多い順（同数は ``dataset_id``
    辞書順）に並べる（§resolve: 1 件を持つデータセットが複数あるときの裁定 —
    :func:`asterism.subject_tools.subject_sources` と同じ ``GRAPH`` 集計を再利用
    し、別のクエリを新設しない）。"""
    counts = await subject_tools._graph_counts_for_subject(client, iri)
    labels = subject_tools.dataset_labels(registry_root)
    per_dataset: dict[str, tuple[str | None, int]] = {}
    for g, cnt in counts:
        dataset_id = substrate.dataset_id_of_canonical_graph(g)
        if dataset_id is None:
            continue
        snapshot = _snapshot_of(g)
        prior_snapshot, prior_count = per_dataset.get(dataset_id, (snapshot, 0))
        per_dataset[dataset_id] = (prior_snapshot or snapshot, prior_count + cnt)
    ranked = sorted(per_dataset.items(), key=lambda kv: (-kv[1][1], kv[0]))
    return [
        {
            "dataset_id": dataset_id,
            "snapshot": snapshot,
            "label": labels.get(dataset_id, dataset_id),
            "count": count,
        }
        for dataset_id, (snapshot, count) in ranked
    ]


def _rows(raw: dict[str, Any]) -> list[dict[str, dict[str, Any]]]:
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    return list(results.get("bindings", []) if isinstance(results, dict) else [])


def _cell(row: dict[str, dict[str, Any]], var: str) -> str | None:
    node = row.get(var)
    return node.get("value") if node else None


async def _entity_label(client: Any, iri: str) -> str:
    """``iri``'s own display label — canonical + ontology scope, the shared
    priority rule (§2: :func:`asterism.subjects.pick_label`), never the
    ``describe.fetch_description``'s ``label`` field alone (that field is
    picked by predicate-IRI iteration order, not priority — the実機 bug that
    produced 「1」/「119」 as a label for a resource whose only literal was a
    ``schema:name``)."""
    canon = await substrate.canonical_graphs(client)
    onto = await substrate.ontology_graphs(client)
    graphs = sorted(set(canon) | set(onto))
    labels = await subject_tools._label_lookup(client, graphs, {iri})
    return labels.get(iri) or subject_tools._local_name(iri)


async def _class_schema_or_none(
    client: Any, registry_root: Any, class_iri: str
) -> dict[str, Any] | None:
    """``class_schema(...)`` そのもの、または (並列担当の) class_schema モジュール
    が使えない／失敗したときの ``None``。もともと :func:`_class_properties` の
    中身だった best-effort ロードを、``properties`` 以外（``dataset_id`` 等）も
    要る呼び出し元（契約メモ contract_pr_f2.md §3.3 の ``sets/resolve`` への
    ``dataset_label`` 追加）のために切り出したもの。"""
    schema_fn = subject_tools._load_class_schema()
    if schema_fn is None:
        return None
    try:
        schema = await schema_fn(client, registry_root, class_iri)
    except Exception:  # best-effort: class_schema is owned by a parallel PR
        logger.debug("cards_routes: class_schema lookup failed", exc_info=True)
        return None
    return schema if isinstance(schema, dict) else None


async def _class_properties(
    client: Any, registry_root: Any, class_iri: str
) -> list[dict[str, Any]] | None:
    """``class_schema(...)['properties']``, or ``None`` when the (parallel-
    authored) class_schema module is unavailable or the lookup fails."""
    schema = await _class_schema_or_none(client, registry_root, class_iri)
    if schema is None:
        return None
    properties = schema.get("properties")
    return list(properties) if isinstance(properties, list) else None


def _require_write_auth(cfg: Settings):
    """A ``Depends`` factory reproducing ``main.py``'s ``require_write_auth``
    (same fail-closed 503-when-unset / 401-when-wrong-token behaviour, same
    messages) — see the module docstring for why this cannot be a literal
    import of that closure."""

    def _dep(
        authorization: str | None = Header(default=None),
        x_asterism_token: str | None = Header(default=None),
    ) -> None:
        token = cfg.api_token
        if not token:
            logger.warning(
                "write route refused: ASTERISM_API_TOKEN is unset "
                "(fail-closed against anonymous writes)"
            )
            raise HTTPException(
                503,
                "利用許可コード (管理者が設定する API token) が未設定のため、この操作はできません",
            )
        presented: str | None = None
        if authorization and authorization.startswith("Bearer "):
            presented = authorization[len("Bearer ") :].strip()
        elif x_asterism_token:
            presented = x_asterism_token.strip()
        if not (presented and hmac.compare_digest(presented, token)):
            raise HTTPException(401, "利用許可コードが違います")

    return _dep


def _store_query_syntax_error_to_400(exc: Exception) -> HTTPException | None:
    """Map a SPARQL-query-construction failure that leaked past every
    ``asterism.subjects``/``subject_tools`` validation gate to a 400 instead
    of an unhandled 500 (checker finding: this is the belt on top of the
    braces — a malformed subject/property IRI must never reach here, but if
    one somehow does, the store's rejection of the resulting query must not
    surface as an opaque server error).

    Two shapes observed in practice, both from a real store: ``pyoxigraph``'s
    in-process ``Store.query`` (used directly by the ingest-layer test
    fixtures) raises the builtin ``SyntaxError`` for a malformed query
    string; the HTTP-backed ``OxigraphClient.sparql_select`` (production)
    raises ``httpx.HTTPStatusError`` when the Oxigraph server responds
    ``400 Bad Request`` to the same malformed query. Anything else (a
    ``HTTPStatusError`` for a non-400 status, i.e. the store itself is
    unhealthy) is NOT a client error — return ``None`` so it re-raises and
    surfaces as the usual 500."""
    if isinstance(exc, SyntaxError):
        return HTTPException(400, f"invalid query: {exc}")
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 400:
        return HTTPException(400, f"invalid query: {exc.response.text[:200]}")
    return None


async def _run_read(coro: Awaitable[_T]) -> _T:
    """Run a read-only route body, mapping the same store-rejected-the-query
    failure :func:`_store_query_syntax_error_to_400` maps for ``cards_run`` —
    checker finding: that mapping was applied to ``cards_run`` only, so every
    other read route that builds its own SPARQL (``subjects_resolve``,
    ``subjects_search``, ``subjects_default_cards``, ``sets_default_cards``,
    ``sets_resolve``, and ``class_schema_routes.classes_schema``) could leak a
    ``SyntaxError``/``httpx.HTTPStatusError`` as an opaque 500 instead of a
    400. Every other exception passes through unchanged."""
    try:
        return await coro
    except (SyntaxError, httpx.HTTPStatusError) as exc:
        mapped = _store_query_syntax_error_to_400(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _reject_if_content_length_exceeds(request: Request, limit: int) -> None:
    """Mirrors ``main.py``'s appdata pre-check: refuse early on an oversized
    declared ``Content-Length`` instead of buffering it first."""
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > limit:
        raise HTTPException(413, f"body is {declared} bytes, over the {limit} limit")


def register_cards(app: FastAPI, cfg: Settings) -> None:
    """Register every object-cards-ui §3/§5 route on ``app``.

    Called once by the integrator, inside ``build_app``, right before
    ``return app`` (§0.1) — never imported/called anywhere else.
    """

    write_auth = [Depends(_require_write_auth(cfg))]

    # ------------------------------------------------------------------
    # §3.4 — POST /api/cards/run (unauthenticated, read-only)
    # ------------------------------------------------------------------

    @app.post("/api/cards/run")
    async def cards_run(body: CardsRunBody) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        try:
            subject = subjects_mod.validate_subject_key(body.subject)
        except (subjects_mod.SubjectKeyError, subjects_mod.SetSpecError) as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            return await subject_tools.run_subject_tool(
                client, cfg.registry_root, subject, body.tool, body.params
            )
        except subject_tools.UnknownSubjectToolError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (
            subject_tools.SubjectKindMismatchError,
            subject_tools.SubjectToolError,
            subjects_mod.SetSpecError,
            query_tools_mod.QueryToolError,
        ) as exc:
            raise HTTPException(400, str(exc)) from exc
        except (SyntaxError, httpx.HTTPStatusError) as exc:
            mapped = _store_query_syntax_error_to_400(exc)
            if mapped is None:
                raise
            raise mapped from exc

    # ------------------------------------------------------------------
    # §3.4 — GET /api/subjects/resolve
    # ------------------------------------------------------------------

    @app.get("/api/subjects/resolve")
    async def subjects_resolve(iri: str = Query(...)) -> dict[str, Any]:
        return await _run_read(_subjects_resolve_impl(iri))

    async def _subjects_resolve_impl(iri: str) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        try:
            subjects_mod.validate_subject_key({"kind": "individual", "iri": iri})
        except (subjects_mod.SubjectKeyError, subjects_mod.SetSpecError) as exc:
            raise HTTPException(400, str(exc)) from exc
        description = await describe.fetch_description(client, iri)
        if description is None:
            return {
                "iri": iri,
                "found": False,
                "label": None,
                "class_iri": None,
                "class_label": None,
                "dataset_id": None,
                "snapshot": None,
                "dataset_label": None,
                "dataset_labels": [],
                "dataset_ids": [],
            }
        types = list(description.get("types") or [])
        class_iri = await subject_tools.pick_class_iri(client, types)
        class_label = (
            await class_schema_mod.class_label(client, cfg.registry_root, class_iri)
            if class_iri
            else None
        )
        # §resolve: この 1 件を持つデータセットが複数あるとき、三つ組が最も
        # 多いデータセットを主とする（同数は dataset_id 辞書順）— 実機所見:
        # 「置く」で棚とつながった IRI は 2 つの版グラフに載り、以前はどちらか
        # 片方（三つ組の少ない方になりうる）しか見せていなかった。
        ranking = await _dataset_ranking_for_subject(client, cfg.registry_root, iri)
        top = ranking[0] if ranking else None
        dataset_id = top["dataset_id"] if top else None
        snapshot = top["snapshot"] if top else None
        dataset_label = top["label"] if top else None
        # §2: describe.fetch_description の ``label`` はここでは使わない
        # （predicate の IRI 辞書順で拾うだけで優先順位が無く、実機で
        # 「1」/「119」のような取り違いを起こしていた）— 共通の優先順位
        # 関数で改めて引く。
        label = await _entity_label(client, iri)
        return {
            "iri": iri,
            "found": True,
            "label": label,
            "class_iri": class_iri,
            "class_label": class_label,
            "dataset_id": dataset_id,
            "snapshot": snapshot,
            "dataset_label": dataset_label,
            "dataset_labels": [r["label"] for r in ranking],
            "dataset_ids": [r["dataset_id"] for r in ranking],
        }

    # ------------------------------------------------------------------
    # §3.4 — GET /api/subjects/search
    # ------------------------------------------------------------------

    @app.get("/api/subjects/search")
    async def subjects_search(
        q: str = Query(default=""),
        limit: int = Query(default=20, ge=1, le=_MAX_SEARCH_LIMIT),
        dataset_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return await _run_read(_subjects_search_impl(q, limit, dataset_id))

    async def _subjects_search_impl(q: str, limit: int, dataset_id: str | None) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        # §3.2: dataset_id は任意 — 指定時はそのデータセットの版グラフに限定
        # する（不正な形は 400。既存の subjects/resolve の subject.iri と同じ
        # 「呼び出し境界で 1 度だけ検証する」流儀）。
        scoped_dataset_id: str | None = None
        if dataset_id is not None:
            scoped_dataset_id = subjects_mod.valid_dataset_id(dataset_id)
            if scoped_dataset_id is None:
                raise HTTPException(400, f"invalid dataset_id: {dataset_id!r}")
        needle = q.strip().lower()
        if not needle:
            return {"items": []}
        graphs = await substrate.canonical_graphs(client)
        if scoped_dataset_id is not None:
            graphs = [
                g for g in graphs if substrate.dataset_id_of_canonical_graph(g) == scoped_dataset_id
            ]
        if not graphs:
            return {"items": []}
        named = substrate.canonical_from_clauses(graphs, named=True)
        escaped = query_tools_mod._escape_literal(needle)
        # §2: 述語の優先順位は共通の LABEL_PREDICATES（rdfs:label が最優先）—
        # 以前はこの検索専用の別リストを持っていて、他の read path と食い違って
        # いた。
        pairs = " ".join(f"(<{p}> {i})" for i, p in enumerate(subjects_mod.LABEL_PREDICATES))
        # Over-fetch (bounded) so a subject matched via several labels/graphs
        # still yields exactly `limit` DISTINCT subjects, picked deterministically.
        raw_limit = min(limit * 4, 400)
        query = (
            f"SELECT ?s ?label ?__rank (LANG(?label) AS ?__lang) ?g\n{named}"
            f"WHERE {{ GRAPH ?g {{ VALUES (?__lp ?__rank) {{ {pairs} }} "
            f'?s ?__lp ?label FILTER(CONTAINS(LCASE(STR(?label)), "{escaped}")) }} }} '
            f"ORDER BY ?s ?__rank ?label ?g LIMIT {raw_limit}"
        )
        rows = _rows(await client.sparql_select(query))
        candidates: dict[str, list[tuple[str | None, int | None, str | None]]] = {}
        graph_of: dict[str, str] = {}
        order: list[str] = []
        for row in rows:
            s = _cell(row, "s")
            if not s:
                continue
            if s not in candidates:
                if len(order) >= limit:
                    continue
                order.append(s)
                candidates[s] = []
                graph_of[s] = _cell(row, "g") or ""
            rank_raw = _cell(row, "__rank")
            rank = int(rank_raw) if rank_raw is not None else None
            candidates[s].append((_cell(row, "label"), rank, _cell(row, "__lang")))
        items: list[dict[str, Any]] = []
        for s in order:
            label = subjects_mod.pick_label(candidates[s]) or subject_tools._local_name(s)
            g = graph_of.get(s)
            dataset_id = substrate.dataset_id_of_canonical_graph(g) if g else None
            types = await subject_tools.subject_types(client, s)
            class_iri = await subject_tools.pick_class_iri(client, types)
            class_label = (
                await class_schema_mod.class_label(client, cfg.registry_root, class_iri)
                if class_iri
                else None
            )
            items.append(
                {
                    "iri": s,
                    "label": label,
                    "class_iri": class_iri,
                    "class_label": class_label,
                    "dataset_id": dataset_id,
                }
            )
        return {"items": items}

    # ------------------------------------------------------------------
    # §3.4 — GET /api/subjects/default-cards / GET /api/sets/default-cards
    # ------------------------------------------------------------------

    @app.get("/api/subjects/default-cards")
    async def subjects_default_cards(iri: str = Query(...)) -> list[dict[str, Any]]:
        return await _run_read(_subjects_default_cards_impl(iri))

    async def _subjects_default_cards_impl(iri: str) -> list[dict[str, Any]]:
        client: OxigraphClient = app.state.client
        try:
            subjects_mod.validate_subject_key({"kind": "individual", "iri": iri})
        except (subjects_mod.SubjectKeyError, subjects_mod.SetSpecError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return await subject_tools.default_cards_for_subject(client, cfg.registry_root, iri)

    @app.get("/api/sets/default-cards")
    async def sets_default_cards(spec: str = Query(...)) -> list[dict[str, Any]]:
        return await _run_read(_sets_default_cards_impl(spec))

    async def _sets_default_cards_impl(spec: str) -> list[dict[str, Any]]:
        client: OxigraphClient = app.state.client
        try:
            raw_spec = json.loads(spec)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"spec must be JSON: {exc}") from exc
        try:
            normalized = subjects_mod.normalize_set_spec(raw_spec)
        except subjects_mod.SetSpecError as exc:
            raise HTTPException(400, str(exc)) from exc
        properties = await _class_properties(client, cfg.registry_root, normalized["class"])
        return subject_tools.default_cards_for_set(normalized, properties)

    # ------------------------------------------------------------------
    # §3.4 — POST /api/sets/resolve
    # ------------------------------------------------------------------

    @app.post("/api/sets/resolve")
    async def sets_resolve(body: SetsResolveBody) -> dict[str, Any]:
        return await _run_read(_sets_resolve_impl(body))

    async def _sets_resolve_impl(body: SetsResolveBody) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        try:
            spec = subjects_mod.normalize_set_spec(body.spec)
        except subjects_mod.SetSpecError as exc:
            raise HTTPException(400, str(exc)) from exc
        set_id = subjects_mod.set_id_of(spec)
        class_label = await class_schema_mod.class_label(client, cfg.registry_root, spec["class"])
        # §3.3: dataset_label は §3.1 と同じ解決 — class_schema がこの class を
        # 所有すると判定したデータセット（無ければ null）。properties も同じ
        # schema から取るので、schema_fn の呼び出しは 1 回で済ませる。
        schema = await _class_schema_or_none(client, cfg.registry_root, spec["class"])
        properties = schema.get("properties") if schema else None
        properties = list(properties) if isinstance(properties, list) else None
        dataset_id = schema.get("dataset_id") if schema else None
        dataset_label = (
            subjects_mod.resolve_dataset_label(cfg.registry_root, dataset_id)
            if isinstance(dataset_id, str) and dataset_id
            else None
        )
        prop_meta = {
            p["iri"]: p for p in (properties or []) if isinstance(p, dict) and p.get("iri")
        }
        clauses = []
        for clause in spec["where"]:
            meta = prop_meta.get(clause["property"], {})
            clauses.append(
                {
                    "property_label": meta.get("label")
                    or subject_tools._local_name(clause["property"]),
                    "op": clause["op"],
                    "value": clause["value"],
                    "unit": meta.get("unit"),
                }
            )
        return {
            "set_id": set_id,
            "spec": spec,
            "title": {"class_label": class_label, "clauses": clauses},
            "dataset_label": dataset_label,
        }

    # ------------------------------------------------------------------
    # §5 — appdata subjects (single-user only; PUT/DELETE are write-gated)
    # ------------------------------------------------------------------

    def _appdata_root_or_404():
        if not cfg.single_user or cfg.appdata_root is None:
            raise HTTPException(404, "appdata is only available in single-user mode")
        return cfg.appdata_root

    @app.get("/api/appdata/subjects")
    async def appdata_list_subjects() -> dict[str, Any]:
        root = _appdata_root_or_404()
        return {"subjects": appdata.read_threads(root, namespace=_SUBJECTS_NAMESPACE)}

    @app.put("/api/appdata/subjects/{subject_id}", dependencies=write_auth)
    async def appdata_put_subject(subject_id: str, request: Request) -> dict[str, Any]:
        root = _appdata_root_or_404()
        _reject_if_content_length_exceeds(request, appdata.MAX_THREAD_BYTES)
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "subject body must be a JSON object")
        try:
            appdata.write_thread(root, subject_id, payload, namespace=_SUBJECTS_NAMESPACE)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        except (appdata.ThreadTooLarge, appdata.TooManyThreads) as exc:
            raise HTTPException(413, str(exc)) from exc
        return {"saved": True}

    @app.delete("/api/appdata/subjects/{subject_id}", dependencies=write_auth)
    async def appdata_delete_subject(subject_id: str) -> dict[str, Any]:
        root = _appdata_root_or_404()
        try:
            deleted = appdata.delete_thread(root, subject_id, namespace=_SUBJECTS_NAMESPACE)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"deleted": deleted}
