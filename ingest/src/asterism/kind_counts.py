"""種類（rdf:type の IRI）ごとの件数を、データセット（版 graph）単位で返す読み手。

``schema_summary`` のクラス件数は全データセットを混ぜた合計で、上位 N 種類で切られる。
同じ種類 IRI を 2 つのデータセットが使うと、どちらの箱にも合計が出て、件数の少ない
種類は黙って欠ける。この読み手は「graph × 種類 × 件数」をそのまま返し、行数の上限を
超えたときは ``truncated`` で言う（黙って欠けさせない）。

読み取り範囲は :func:`asterism.substrate.canonical_graphs`（公開済みの版 graph と
ハブ graph）だけ。公開済みが 0 件なら SPARQL を投げずに空を返す — 空の
``FROM NAMED`` は draft まで読んでしまうため（``prov_graph`` と同じ理由）。
"""
from __future__ import annotations

from typing import Any

from asterism.substrate import (
    SupportsSparql,
    canonical_graphs,
    dataset_id_of_canonical_graph,
    is_hub_graph,
)

#: 返す (graph, 種類) 行の既定の上限。超えたら ``truncated: true``。
DEFAULT_MAX_ROWS: int = 5000


def _kind_counts_query(graphs: list[str], limit: int) -> str:
    from_named = "".join(f"FROM NAMED <{g}>\n" for g in graphs)
    return (
        "SELECT ?g ?c (COUNT(DISTINCT ?s) AS ?n)\n"
        f"{from_named}"
        "WHERE { GRAPH ?g { ?s a ?c } FILTER(isIRI(?c)) }\n"
        "GROUP BY ?g ?c\n"
        "ORDER BY ?g DESC(?n) ?c\n"
        f"LIMIT {limit}"
    )


async def kind_counts(
    client: SupportsSparql, *, max_rows: int = DEFAULT_MAX_ROWS
) -> dict[str, Any]:
    """``{"graphs": [{graph, dataset_id, hub, kinds: [{class_iri, count}]}], "truncated"}``。

    ``graphs`` は graph IRI 順、``kinds`` は件数の多い順→種類 IRI 順（決定論）。
    ``dataset_id`` は版 graph から引いた登録 id（ハブは ``None``）。
    """
    graphs = await canonical_graphs(client)
    if not graphs:
        return {"graphs": [], "truncated": False}
    raw = await client.sparql_select(_kind_counts_query(graphs, max_rows + 1))
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    rows = results.get("bindings", []) if isinstance(results, dict) else []
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]

    by_graph: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        g = (row.get("g") or {}).get("value")
        c = (row.get("c") or {}).get("value")
        n = (row.get("n") or {}).get("value")
        if not g or not c or n is None:
            continue
        by_graph.setdefault(g, []).append({"class_iri": c, "count": int(n)})

    out = []
    for g in sorted(by_graph):
        kinds = sorted(by_graph[g], key=lambda k: (-k["count"], k["class_iri"]))
        hub = is_hub_graph(g)
        out.append(
            {
                "graph": g,
                # 旧形式のハブ（…/canonical/crosswalk）は id の形に合ってしまうので、
                # ハブは登録 id を持たないものとして None にそろえる。
                "dataset_id": None if hub else dataset_id_of_canonical_graph(g),
                "hub": hub,
                "kinds": kinds,
            }
        )
    return {"graphs": out, "truncated": truncated}
