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
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

import httpx
from asterism import class_schema as class_schema_mod
from asterism import crosswalk_runtime, subject_tools, substrate
from asterism import network_view as network_view_mod
from asterism import query_tools as query_tools_mod
from asterism import subjects as subjects_mod
from asterism.crosswalk import XW as _XW_NS
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel

from asterism_api import appdata, crosswalk_names, describe

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_SUBJECTS_NAMESPACE = "subjects"

#: 契約メモ contract_pr_f9.md §2.2: 「上限 200」——
#: :data:`asterism.subjects.MAX_LIMIT` と同じ値を、意味も同じ「呼び出し境界の
#: 上限」として再利用する（別の数を持たない）。
_MAX_SEARCH_LIMIT = subjects_mod.MAX_LIMIT
# 空 q（種類の一覧）で読むラベル行の上限。1 主語あたりラベル数件なので、
# 数千件の種類まで名前順の先頭 limit 件を正しく選べる。それより大きい種類は
# 先頭の行だけで近似する（検索欄で絞れば正確）。
_EMPTY_QUERY_MAX_ROWS = 5000


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
    し、別のクエリを新設しない）。

    契約メモ contract_pr_f16.md §1.4: ハブの graph（:func:`asterism.substrate.is_hub_graph`）
    は「主語がどのデータセットに属するか」の答えに数えない — ハブ graph の
    リンク三つ組（メンバー→ハブ）まで数えると、ハブのメンバー自身の
    dataset_id/label が本来のデータセットでなくハブの graph の id（実データ
    と食い違う; §0 背景）に化けてしまう。"""
    counts = await subject_tools._graph_counts_for_subject(client, iri)
    labels = subject_tools.dataset_labels(registry_root)
    per_dataset: dict[str, tuple[str | None, int]] = {}
    for g, cnt in counts:
        if substrate.is_hub_graph(g):
            continue
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


async def _property_name(client: Any, iri: str) -> str:
    """述語の表示名 — ストア（公開済み＋オントロジー）の ``rdfs:label`` 等を共通の優先順位で、
    無ければ読みくだし（K4: 生のローカル名を出さない）。経由つきの線の条件の題（O67）用。"""
    graphs = sorted(await substrate.readable_graph_iris(client))
    labels = await subject_tools._label_lookup(
        client, graphs, {iri}, fallback=subject_tools._fallback_label
    )
    return labels.get(iri) or subject_tools._fallback_label(iri)


#: 契約メモ contract_pr_f16.md §1.4: ハブのページの「同じものとして束ねた
#: もの」に載せるメンバーの上限。
_HUB_MEMBERS_LIMIT = 50


def _ref_or_none(iri: str) -> str | None:
    """SPARQL の IRIREF として安全なら ``<iri>``、そうでなければ ``None``
    （契約メモ §3.2「IRI は ``_ref``」— ここでは ingest 由来・呼び出し元
    検証済みの IRI しか渡さないが、埋め込む前にもう一段確かめる）。"""
    checked = subjects_mod.safe_iri(iri)
    return f"<{checked}>" if checked else None


async def _hub_of_or_none(client: Any, iri: str) -> dict[str, Any] | None:
    """``asterism.subject_tools.hub_of_subject`` の薄いラッパー。契約メモ
    contract_pr_f16.md §0「並列中の仮置き」: ingest 側にまだこの関数が無い
    間は常に ``None``（=ハブ関連なし）として扱う。"""
    hub_of_subject = getattr(subject_tools, "hub_of_subject", None)
    if hub_of_subject is None:
        return None
    result = await hub_of_subject(client, iri)
    return result if isinstance(result, dict) else None


#: :func:`asterism_api.crosswalk_names.perspective_display_name` の
#: ``(predicate_label_of, field_label_of)`` を返す作り手の型
#: （``asterism_api.main._crosswalk_label_resolvers`` と同じ形）。
LabelResolvers = Callable[
    [Any],
    tuple[
        Callable[[str, str], "str | None"],
        Callable[[str, str, "str | None"], "str | None"],
    ],
]


def _no_op_label_resolvers(
    registry_root: Any,
) -> tuple[
    Callable[[str, str], str | None],
    Callable[[str, str, str | None], str | None],
]:
    """``label_resolvers`` 省略時の既定（単体テストなど main.py を経由しない
    呼び出し向け）。項目の表示名が 1 件も引けない場合と同じ挙動 — R2 は
    概念のキーの人向け直しに落ちる。呼び出し元が :func:`asterism_api.main.
    _crosswalk_label_resolvers` を渡すのが通常。"""
    del registry_root

    def _none2(_a: str, _b: str) -> str | None:
        return None

    def _none3(_a: str, _b: str, _c: str | None) -> str | None:
        return None

    return _none2, _none3


def _hub_perspective_name(
    registry_root: Any, perspective_id: str, label_resolvers: LabelResolvers
) -> str:
    """つながりの表示名（契約メモ contract_b_hub_names.md の R1）: 人が付けた
    名前を最優先し、無ければ参加している項目の表示名から組み立てる
    （:mod:`asterism_api.crosswalk_names`）。ハブの registry id と graph の id
    が食い違う（§0 背景）ため、
    :func:`asterism.crosswalk_runtime.crosswalk_registry_id` で registry id に
    変換してから meta / config を読む。"""
    meta = crosswalk_names.load_perspective_meta(registry_root, perspective_id)
    config = crosswalk_runtime.load_config(registry_root, perspective_id)
    predicate_label_of, field_label_of = label_resolvers(registry_root)
    return crosswalk_names.perspective_display_name(
        meta, config, perspective_id, field_label_of, predicate_label_of
    )


async def _class_label_or_hub(
    client: Any,
    registry_root: Any,
    class_iri: str,
    resolve_labels: LabelResolvers,
    hub_index: dict[str, crosswalk_runtime.RuntimeCrosswalkConfig],
) -> str | None:
    """種類の表示名 — 契約メモ contract_b2_hub_names.md B2-1: ``class_iri`` が
    ``hub_index`` に載っているハブの concept の種類なら R3
    （:func:`asterism_api.crosswalk_names.hub_class_display_name`）、そうでな
    ければ従来通り ``class_schema.class_label``。``hub_index`` は呼び出し元
    が 1 リクエストにつき 1 度だけ :func:`asterism_api.crosswalk_names.
    hub_class_index` で作って渡す（このモジュールでは読み直さない）。"""
    config = hub_index.get(class_iri)
    if config is not None:
        predicate_label_of, field_label_of = resolve_labels(registry_root)
        return crosswalk_names.hub_class_display_name(
            class_iri, config, field_label_of, predicate_label_of
        )
    return await class_schema_mod.class_label(client, registry_root, class_iri)


async def _hub_member_iris(client: Any, hub_graph: str, hub_iri: str) -> list[str]:
    """``hub_iri`` を指す実体（``hub_graph`` 内）の IRI を辞書順・重複無しで
    全件返す（契約メモ §1.4 の ``hub.members``／``hub_of.member_count``・
    ``dataset_labels`` の共通の材料）。"""
    hub_graph_ref = _ref_or_none(hub_graph)
    hub_iri_ref = _ref_or_none(hub_iri)
    if hub_graph_ref is None or hub_iri_ref is None:
        return []
    # per-link の来歴（xw:CrosswalkLink）もハブを指すが、メンバーではない
    # （subject_tools._hub_members と同じ除外）。
    query = (
        f"SELECT DISTINCT ?m WHERE {{ GRAPH {hub_graph_ref} {{ ?m ?p {hub_iri_ref} "
        f'FILTER NOT EXISTS {{ ?m a ?mt FILTER(STRSTARTS(STR(?mt), "{_XW_NS}")) }} }} }} '
        "ORDER BY ?m"
    )
    rows = _rows(await client.sparql_select(query))
    return [m for m in (_cell(r, "m") for r in rows) if m]


async def _hub_members(
    client: Any,
    registry_root: Any,
    hub_graph: str,
    hub_iri: str,
    limit: int,
    resolve_labels: LabelResolvers,
    hub_index: dict[str, crosswalk_runtime.RuntimeCrosswalkConfig],
) -> list[dict[str, Any]]:
    """ハブのページの「同じものとして束ねたもの」の行（契約メモ §1.4の
    ``hub.members``）。IRI 辞書順の先頭 ``limit`` 件だけ、ラベル・データ
    セット・種類を添えて返す。種類の名前は契約メモ contract_b2_hub_names.md
    B2-1（``hub_index`` は呼び出し元が 1 リクエストにつき 1 度だけ作って渡
    す — レジストリを読み直さない）。"""
    members: list[dict[str, Any]] = []
    for m in (await _hub_member_iris(client, hub_graph, hub_iri))[:limit]:
        label = await _entity_label(client, m)
        ranking = await _dataset_ranking_for_subject(client, registry_root, m)
        top = ranking[0] if ranking else None
        types = await subject_tools.subject_types(client, m)
        class_iri = await subject_tools.pick_class_iri(client, types)
        class_label = (
            await _class_label_or_hub(client, registry_root, class_iri, resolve_labels, hub_index)
            if class_iri
            else None
        )
        members.append(
            {
                "iri": m,
                "label": label,
                "dataset_id": top["dataset_id"] if top else None,
                "dataset_label": top["label"] if top else None,
                "class_label": class_label,
            }
        )
    return members


async def _hub_of_summary(
    client: Any, registry_root: Any, hub_graph: str, hub_iri: str
) -> tuple[int, list[str]]:
    """``hub_of`` の ``member_count``（ハブを指す実体の数）と
    ``dataset_labels``（それらのデータセット名の重複なし一覧・登場順）。
    契約メモ §1.4。"""
    member_iris = await _hub_member_iris(client, hub_graph, hub_iri)
    dataset_labels: list[str] = []
    seen: set[str] = set()
    for m in member_iris:
        ranking = await _dataset_ranking_for_subject(client, registry_root, m)
        if not ranking:
            continue
        label = ranking[0]["label"]
        if label not in seen:
            seen.add(label)
            dataset_labels.append(label)
    return len(member_iris), dataset_labels


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


def register_cards(
    app: FastAPI, cfg: Settings, *, label_resolvers: LabelResolvers | None = None
) -> None:
    """Register every object-cards-ui §3/§5 route on ``app``.

    Called once by the integrator, inside ``build_app``, right before
    ``return app`` (§0.1) — never imported/called anywhere else.

    ``label_resolvers``: 契約メモ contract_b_hub_names.md の R1 が使う項目の
    表示名の引き手（``asterism_api.main._crosswalk_label_resolvers``）。
    ``main.py`` からしか渡せない（このモジュールが ``main`` を import すると
    循環になる — モジュール docstring 参照）ので、ここでは受け取るだけ。
    省略時は :func:`_no_op_label_resolvers`（R2 が常にキーの人向け直しに
    落ちる）。
    """
    resolve_labels = label_resolvers or _no_op_label_resolvers

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
                "is_hub": False,
                "hub": None,
                "hub_of": None,
            }
        types = list(description.get("types") or [])
        class_iri = await subject_tools.pick_class_iri(client, types)
        # 契約メモ contract_b2_hub_names.md B2-1: この主語自身がハブかどうかに
        # 関わらず、種類の IRI がどれかのハブの concept のものなら R3。
        hub_class_index = crosswalk_names.hub_class_index(cfg.registry_root)
        class_label = (
            await _class_label_or_hub(
                client, cfg.registry_root, class_iri, resolve_labels, hub_class_index
            )
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

        # 契約メモ contract_pr_f16.md §1.4: 主語がハブ本体か、ハブを指す実体
        # （またはその親）かを添える。ingest 側の hub_of_subject がまだ無い
        # 並列期間は「どちらでもない」（§0 並列中の仮置き）。
        is_hub = False
        hub: dict[str, Any] | None = None
        hub_of: dict[str, Any] | None = None
        hub_info = await _hub_of_or_none(client, iri)
        if hub_info is not None:
            perspective_id = hub_info.get("perspective_id")
            hub_iri = hub_info.get("hub_iri")
            if isinstance(perspective_id, str) and isinstance(hub_iri, str):
                perspective_name = _hub_perspective_name(
                    cfg.registry_root, perspective_id, resolve_labels
                )
                if hub_iri == iri:
                    is_hub = True
                    dataset_label = perspective_name
                    graph = hub_info.get("graph")
                    hub_graph = (
                        graph
                        if isinstance(graph, str)
                        else crosswalk_runtime.crosswalk_graph_iri(perspective_id)
                    )
                    members = await _hub_members(
                        client,
                        cfg.registry_root,
                        hub_graph,
                        hub_iri,
                        _HUB_MEMBERS_LIMIT,
                        resolve_labels,
                        hub_class_index,
                    )
                    hub = {
                        "perspective_id": perspective_id,
                        "name": perspective_name,
                        "members": members,
                    }
                else:
                    hub_graph = crosswalk_runtime.crosswalk_graph_iri(perspective_id)
                    member_count, hub_dataset_labels = await _hub_of_summary(
                        client, cfg.registry_root, hub_graph, hub_iri
                    )
                    hub_label = hub_info.get("hub_label")
                    hub_of = {
                        "iri": hub_iri,
                        "label": (
                            hub_label
                            if isinstance(hub_label, str)
                            else await _entity_label(client, hub_iri)
                        ),
                        "perspective_name": perspective_name,
                        "member_count": member_count,
                        "dataset_labels": hub_dataset_labels,
                        # 契約メモ contract_pr_f19.md §1.1: 直接のメンバー
                        # （``via_parent`` が None）かどうか。2 段（親経由）
                        # は寄せない判断に使う。
                        "direct": hub_info.get("via_parent") is None,
                    }

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
            "is_hub": is_hub,
            "hub": hub,
            "hub_of": hub_of,
        }

    # ------------------------------------------------------------------
    # 契約メモ contract_pr_f4.md §1-3 — GET /api/subjects/linking-kinds
    #
    # この 1 件を目的語に持つ実例の「種類と述語」（グラフを足すフォームの
    # ③条件で「この 1 件を指す種類」を機械が探すのに使う）。担当 tool が
    # 作る asterism.subject_tools.linking_kinds をそのまま呼ぶだけ — ここで
    # は形を作らない（並列作業中は linking_kinds がまだ無いこともあるが、
    # モジュール自体（subject_tools）は既に import 済みなのでこのファイルの
    # import 自体は失敗しない。呼び出し時にのみ解決される）。
    # ------------------------------------------------------------------

    @app.get("/api/subjects/linking-kinds")
    async def subjects_linking_kinds(iri: str = Query(...)) -> dict[str, Any]:
        return await _run_read(_subjects_linking_kinds_impl(iri))

    async def _subjects_linking_kinds_impl(iri: str) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        try:
            subjects_mod.validate_subject_key({"kind": "individual", "iri": iri})
        except (subjects_mod.SubjectKeyError, subjects_mod.SetSpecError) as exc:
            raise HTTPException(400, str(exc)) from exc
        kinds = await subject_tools.linking_kinds(client, iri, registry_root=cfg.registry_root)
        return {"kinds": kinds}

    # ------------------------------------------------------------------
    # ADR O67 — GET /api/subjects/neighbors
    #
    # この 1 件の隣を 1 段だけ返す（図を人が 1 段ずつ広げるための読み手）。
    # 形と名前の規則は asterism.subject_tools.subject_neighbors の docstring。
    # ここでは境界の検証と、ハブの concept の種類名の差し替え（resolve と同じ
    # _class_label_or_hub）だけをする。
    # ------------------------------------------------------------------

    @app.get("/api/subjects/neighbors")
    async def subjects_neighbors(iri: str = Query(...)) -> dict[str, Any]:
        return await _run_read(_subjects_neighbors_impl(iri))

    async def _subjects_neighbors_impl(iri: str) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        try:
            subjects_mod.validate_subject_key({"kind": "individual", "iri": iri})
        except (subjects_mod.SubjectKeyError, subjects_mod.SetSpecError) as exc:
            raise HTTPException(400, str(exc)) from exc
        out = await subject_tools.subject_neighbors(client, iri, registry_root=cfg.registry_root)
        hub_index = crosswalk_names.hub_class_index(cfg.registry_root)
        if hub_index:
            names: dict[str, str | None] = {}

            async def _hub_name(class_iri: str | None) -> str | None:
                if class_iri is None or class_iri not in hub_index:
                    return None
                if class_iri not in names:
                    names[class_iri] = await _class_label_or_hub(
                        client, cfg.registry_root, class_iri, resolve_labels, hub_index
                    )
                return names[class_iri]

            center = out.get("center")
            if isinstance(center, dict) and (label := await _hub_name(center.get("class_iri"))):
                center["class_label"] = label
            for group in out.get("groups", []):
                if label := await _hub_name(group.get("class_iri")):
                    group["class_label"] = label
        return out

    # ------------------------------------------------------------------
    # GET /api/network — 「値でつなぐ網」（全体グラフ・contract_network_view.md §2）
    #
    # 点・線・束の規則は asterism.network_view.network_view の docstring。ここでは
    # クエリ引数の受け取りと、ハブの concept の種類名の差し替え（neighbors と同じ
    # _class_label_or_hub）だけをする。
    # ------------------------------------------------------------------

    async def _upper_map_or_none(client: Any) -> dict[str, Any] | None:
        """上位構造の対応表（`asterism.shared_vocab.upper_map`）。
        ADR upper-structure-shared-terms.md。
        その関数がまだ無い（上位構造の実装前）か、組み立てに失敗したときは None。全体グラフは
        「同じ述語の同じ値」で合流し、種類ごとに塗る（global-network-view.md）。"""
        try:
            from asterism.shared_vocab import upper_map  # type: ignore[import-not-found]
        except ImportError as exc:
            # まだ無い（モジュールか upper_map が無い）は静かに。
            # 中の import が壊れているときは見えるように warning。
            if exc.name == "asterism.shared_vocab":
                logger.debug("network: asterism.shared_vocab.upper_map not available yet")
            else:
                logger.warning(
                    "network: shared_vocab failed to import; drawing without it", exc_info=True
                )
            return None
        try:
            # 取り決め（upper-structure-shared-terms.md §2.6）は関数名と返り値の形だけなので、
            # 同期でも非同期でも受ける。返り値は {"classes", "properties", "at"}（at は使わない）。
            result = upper_map(client)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception:  # 対応表が無くても網は出す
            logger.warning("network: upper_map failed; drawing without it", exc_info=True)
            return None

    @app.get("/api/network")
    async def network(include_prov: bool = Query(default=False)) -> dict[str, Any]:
        return await _run_read(_network_impl(include_prov))

    async def _network_impl(include_prov: bool) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        out = await network_view_mod.network_view(
            client,
            registry_root=cfg.registry_root,
            include_prov=include_prov,
            upper=await _upper_map_or_none(client),
        )
        hub_index = crosswalk_names.hub_class_index(cfg.registry_root)
        if hub_index:
            names: dict[str, str | None] = {}

            async def _hub_name(class_iri: str | None) -> str | None:
                if class_iri is None or class_iri not in hub_index:
                    return None
                if class_iri not in names:
                    names[class_iri] = await _class_label_or_hub(
                        client, cfg.registry_root, class_iri, resolve_labels, hub_index
                    )
                return names[class_iri]

            for node in out["nodes"]:
                if label := await _hub_name(node.get("class_iri")):
                    node["class_label"] = label
            for kind in out["kinds"]:
                if label := await _hub_name(kind.get("class_iri")):
                    kind["class_label"] = label
        return out

    # ------------------------------------------------------------------
    # §3.4 — GET /api/subjects/search
    # ------------------------------------------------------------------

    @app.get("/api/subjects/search")
    async def subjects_search(
        q: str = Query(default=""),
        limit: int = Query(default=20, ge=1, le=_MAX_SEARCH_LIMIT),
        offset: int = Query(default=0, ge=0),
        dataset_id: str | None = Query(default=None),
        class_iri: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return await _run_read(_subjects_search_impl(q, limit, offset, dataset_id, class_iri))

    async def _subjects_search_impl(
        q: str, limit: int, offset: int, dataset_id: str | None, class_iri: str | None
    ) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        # §3.2: dataset_id は任意 — 指定時はそのデータセットの版グラフに限定
        # する（不正な形は 400。既存の subjects/resolve の subject.iri と同じ
        # 「呼び出し境界で 1 度だけ検証する」流儀）。
        scoped_dataset_id: str | None = None
        if dataset_id is not None:
            scoped_dataset_id = subjects_mod.valid_dataset_id(dataset_id)
            if scoped_dataset_id is None:
                raise HTTPException(400, f"invalid dataset_id: {dataset_id!r}")
        # 契約メモ contract_pr_f9.md §2.2: class_iri は任意 — 指定時は
        # ``?s a <class_iri>`` で種類に限定する（不正な形は 400）。
        class_pattern = ""
        if class_iri is not None:
            clause = subjects_mod.class_type_clause(class_iri)
            if clause is None:
                raise HTTPException(400, f"invalid class_iri: {class_iri!r}")
            class_pattern = clause
        needle = q.strip().lower()
        # §2.2: q が空でもよいのは class_iri で種類が決まっているとき（その
        # 種類の名前順の先頭 limit 件 = 一覧表示用）。class_iri も無い空 q は
        # 従来どおり空振り。
        if not needle and not class_pattern:
            return {"items": [], "total": 0, "offset": offset, "limit": limit}
        graphs = await substrate.canonical_graphs(client)
        if scoped_dataset_id is not None:
            graphs = [
                g for g in graphs if substrate.dataset_id_of_canonical_graph(g) == scoped_dataset_id
            ]
        if not graphs:
            return {"items": [], "total": 0, "offset": offset, "limit": limit}
        named = substrate.canonical_from_clauses(graphs, named=True)
        # §2: 述語の優先順位は共通の LABEL_PREDICATES（rdfs:label が最優先）—
        # 以前はこの検索専用の別リストを持っていて、他の read path と食い違って
        # いた。
        pairs = " ".join(f"(<{p}> {i})" for i, p in enumerate(subjects_mod.LABEL_PREDICATES))
        total: int | None = None
        total_is_lower_bound = False
        if needle:
            escaped = query_tools_mod._escape_literal(needle)
            where = (
                f"GRAPH ?g {{ {class_pattern}VALUES (?__lp ?__rank) {{ {pairs} }} "
                f'?s ?__lp ?label FILTER(CONTAINS(LCASE(STR(?label)), "{escaped}")) }}'
            )
            order_clause = "?s ?__rank ?label ?g"
            # 契約メモ contract_pr_f10.md §2: 検索中は over-fetch のままだと
            # total を正確に数えられないので、同じ WHERE で COUNT(DISTINCT ?s)
            # を別クエリで走らせて total にする（limit/offset に依らない）。
            count_query = f"SELECT (COUNT(DISTINCT ?s) AS ?__cnt)\n{named}WHERE {{ {where} }}"
            count_rows = _rows(await client.sparql_select(count_query))
            total = int(_cell(count_rows[0], "__cnt")) if count_rows else 0
            # Over-fetch (bounded) so a subject matched via several
            # labels/graphs still yields exactly `limit` DISTINCT subjects
            # starting at `offset`, picked deterministically. 修正前は
            # 上限が固定 400 行で、offset が進む（「もっと見る」を繰り返す）
            # と (offset+limit)*4 がすぐ 400 を超えて頭打ちになり、以降は
            # total は正確なまま items だけ増えなくなっていた（既知の欠陥・
            # レビュー指摘）。上限を実際に一致した件数（total）に応じて
            # 伸ばし、total 件ぶんの行を読み切れるだけの余裕を持たせる。
            raw_limit = min((offset + limit) * 4, max(400, total * 4))
        else:
            # §2.2: 空 q（ここに来るのは class_iri 指定時のみ）— その種類の
            # 全実例を、ラベルを主キーに並べ、先頭 limit 件だけ Python 側で
            # 数える（CONTAINS 絞り込みが無いぶん多めに行を読む必要がある）。
            # ラベルの生の行で ORDER BY すると、英語のラベルが先に並ぶ主語だけ
            # が先頭に来て、その主語の日本語ラベルの行は raw_limit の外に落ちる
            # （実機所見: 「Afghanistan」…が並び、日本語の見出しが選ばれない）。
            # 種類の全実例のラベル行を読み（上限つき）、Python 側で主語ごとに
            # pick_label してから名前順に並べ、offset:offset+limit を返す。
            raw_limit = _EMPTY_QUERY_MAX_ROWS
            where = (
                f"GRAPH ?g {{ {class_pattern}"
                f"OPTIONAL {{ VALUES (?__lp ?__rank) {{ {pairs} }} ?s ?__lp ?label }} }}"
            )
            order_clause = "?s ?__rank ?g"
        query = (
            f"SELECT ?s ?label ?__rank (LANG(?label) AS ?__lang) ?g\n{named}"
            f"WHERE {{ {where} }} "
            f"ORDER BY {order_clause} LIMIT {raw_limit}"
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
                if needle and len(order) >= offset + limit:
                    continue
                order.append(s)
                candidates[s] = []
                graph_of[s] = _cell(row, "g") or ""
            rank_raw = _cell(row, "__rank")
            rank = int(rank_raw) if rank_raw is not None else None
            candidates[s].append((_cell(row, "label"), rank, _cell(row, "__lang")))
        if needle:
            # 検索中: over-fetch で集めた order は既に offset+limit 件に頭打ち
            # 済み（ラベル一致順）。先頭 offset 件を飛ばして limit 件を返す。
            # raw_limit（total に応じて伸ばした上限）に頭打ちで読んでいて、
            # かつそれでも offset+limit 件の主語を集めきれなかった場合は
            # （1 主語あたりの重複行が極端に多いなど）、total は正確でも
            # この続きを取得しきれない可能性がある印として立てる。
            if len(rows) >= raw_limit and len(order) < offset + limit:
                total_is_lower_bound = True
            order = order[offset : offset + limit]
        else:
            # 空 q: 主語ごとの見出し（pick_label）で名前順に並べ、
            # offset:offset+limit を返す。文字列の比較は Python のコード
            # ポイント順（決定論・ロケール非依存）。
            def _sort_key(subject: str) -> tuple[str, str]:
                picked = subjects_mod.pick_label(candidates[subject])
                return (picked or subject_tools._local_name(subject), subject)

            sorted_order = sorted(order, key=_sort_key)
            total = len(sorted_order)
            # raw_limit（_EMPTY_QUERY_MAX_ROWS）に頭打ちで読んでいたら、
            # その種類の全実例を読み切れていない可能性がある＝total は下限。
            total_is_lower_bound = len(rows) >= raw_limit
            order = sorted_order[offset : offset + limit]
        # 契約メモ contract_b2_hub_names.md B2-1: 検索結果の「種類」も、ハブの
        # concept の class_iri なら R3。1 リクエストで 1 度だけ読む。
        hub_class_index = crosswalk_names.hub_class_index(cfg.registry_root)
        items: list[dict[str, Any]] = []
        for s in order:
            label = subjects_mod.pick_label(candidates[s]) or subject_tools._local_name(s)
            g = graph_of.get(s)
            dataset_id = substrate.dataset_id_of_canonical_graph(g) if g else None
            types = await subject_tools.subject_types(client, s)
            class_iri = await subject_tools.pick_class_iri(client, types)
            class_label = (
                await _class_label_or_hub(
                    client, cfg.registry_root, class_iri, resolve_labels, hub_class_index
                )
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
        result: dict[str, Any] = {
            "items": items,
            "total": total if total is not None else 0,
            "offset": offset,
            "limit": limit,
        }
        if total_is_lower_bound:
            result["total_is_lower_bound"] = True
        return result

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
        # 契約メモ contract_b2_hub_names.md B2-1: 条件で集めた一覧の題も、
        # 対象の種類がハブの concept の class_iri なら R3。
        hub_class_index = crosswalk_names.hub_class_index(cfg.registry_root)
        class_label = await _class_label_or_hub(
            client, cfg.registry_root, spec["class"], resolve_labels, hub_class_index
        )
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
            property_label = meta.get("label") or subject_tools._local_name(clause["property"])
            if "op" in clause:
                clauses.append(
                    {
                        "property_label": property_label,
                        "op": clause["op"],
                        "value": clause["value"],
                        "unit": meta.get("unit"),
                    }
                )
                continue
            # 線の条件（O59 の where：「この 1 件を指す」`{property, iri}`／経由つき
            # `{property, via: {property, iri}}`）。値は相手の 1 件の名前 — その 1 件の
            # ページの見出しと同じ（K4: 生の IRI を出さない）。O67 の束の「一覧で開く」が
            # この形の where を渡す（以前は "op" を前提にして 500 になっていた）。
            via = clause.get("via")
            if isinstance(via, dict):
                clauses.append(
                    {
                        "property_label": property_label,
                        "op": "via",
                        "value": await _entity_label(client, via["iri"]),
                        "via_property_label": await _property_name(client, via["property"]),
                        "unit": None,
                    }
                )
                continue
            clauses.append(
                {
                    "property_label": property_label,
                    "op": "link",
                    "value": await _entity_label(client, clause["iri"]),
                    "unit": None,
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
