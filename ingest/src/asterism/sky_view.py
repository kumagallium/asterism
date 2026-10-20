"""「星図」（ホーム）の読み手 — データセット・種類・ことば・標準用語・視点・問いを 1 枚で返す。

公開済みのものだけを読む（draft は読まない）。:func:`asterism.network_view.network_view` と同じ
流儀: 決定論・LLM を呼ばない・同じ入力は同じ返り値。

読むもの（契約 pr2-contract.md §A）

* データセット = registry の公開済み（``promoted`` かつ取り下げ・削除でなく、システムの項目
  でない）。名前は :func:`asterism.subjects.resolve_dataset_label`。
* 種類 = :func:`asterism.kind_counts.kind_counts`（データセット × 種類 × 件数）。線の graph・
  ハブの graph は中身として数えない。PROV の型（取り込みの記録）と ``xw:CrosswalkLink`` も
  データの種類ではないので出さない（``network_view`` が点にしないのと同じ判定）。
* 種類どうしの線 = そのデータセットの ontology graph の ``rdfs:domain`` → ``rdfs:range``
  （両端が同じデータセットの種類のときだけ。項目＝リテラルは出さない）。
* ことば = 共有のことばの graph（``vocab/shared``）。``members`` は線の graph の**直接の線**で、
  畳まない（上位パスをたどらない）。
* 標準用語 = 線の graph の端が ``grounding`` の名前空間にあるもの。
* 視点（ハブ）= :func:`asterism.crosswalk_runtime.list_perspectives`。参加者の種類が ``members``。
* 問い = ``registry/vocab-shared/query_tools.yaml`` の ``for_terms`` 付きの問い（``mode`` =
  ``data``。答えるデータセットの数は ``wired.json``）と、``themes.json`` の知識の問い
  （``mode`` = ``knowledge``・tool_name ``kq_…``。対象の語の確定した知識の線の数は
  ``knowledge.json``）。「答えられる」は データ ≥ 1 **または** 知識 ≥ 1。
* 知識の線 = 語どうしの ``isPartOf`` / ``lowers`` / ``raises`` / ``affects``（``edges[]``）。

公開済みのデータセットが 0 件なら、store に 1 回も問い合わせず空の形を返す（空の
``FROM NAMED`` は draft まで読んでしまうため）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asterism import crosswalk_runtime, substrate, themes
from asterism.class_schema import class_labels
from asterism.crosswalk import XW as _XW_NS
from asterism.grounding import term_display_name, vocabularies
from asterism.kind_counts import kind_counts
from asterism.prov_graph import PROV as _PROV_NS
from asterism.query_tools import read_registry_raw_tools
from asterism.shared_vocab import (
    REGISTRY_ID as _VOCAB_REGISTRY_ID,
)
from asterism.shared_vocab import (
    _read_terms,
    is_system_entry,
    known_namespaces,
    load_knowledge,
    load_wired,
    term_iri,
)
from asterism.subject_tools import (
    _cell,
    _fallback_label,
    _ref,
    _rows,
)
from asterism.subjects import resolve_dataset_label, valid_dataset_id
from asterism.substrate import (
    SupportsSparql,
    canonical_graphs,
    dataset_id_of_canonical_graph,
    is_hub_graph,
    ontology_graph_iri,
    without_alignment_graph,
)

#: 返す (データセット, 種類) の組の既定の上限。超えたら件数の多い順に切って ``truncated``。
DEFAULT_MAX_KINDS = 600
#: 「最近の育ち」の件数。
RECENT_LIMIT = 5
#: ontology graph の線（domain→range）を読む行の上限。
_MAX_LINK_ROWS = 50000
#: 空の問いの見出し（テーマは PR 4 で入る）。
FIRST_QUESTIONS_LABEL = "はじめの問い"

_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
_QUDT_QK = "http://qudt.org/vocab/quantitykind/"
_QK_PREFIX = "quantitykind"
_XW_LINK_CLASS = _XW_NS + "CrosswalkLink"
_LINE_RELATIONS = ("subClassOf", "equivalentClass", "subPropertyOf", "equivalentProperty")
_SUBSUMPTION = ("subClassOf", "subPropertyOf")
#: 語どうしの「知識の線」として ``edges[]`` に返す関係（一部・下げる・上げる・変える）。
_EDGE_RELATIONS = frozenset(crosswalk_runtime.KNOWLEDGE_RELATIONS)
#: 「標準用語に一致」と数える線。lowers / isPartOf など、同じものを言わない線は含めない
_STANDARD_MATCH_RELATIONS = frozenset({"equivalentClass", "equivalentProperty", "hasQuantityKind"})
_UNPUBLISHED_STATUS = ("retracted", "deleted")

#: 「決めたこと」として出す関係。文は画面（UI）が言語に合わせて組む。
_DECISION_RELATIONS = frozenset(
    {"equivalentClass", "equivalentProperty", "subClassOf", "subPropertyOf", "hasQuantityKind"}
)


def _facts(**over: int) -> dict[str, int]:
    base = {
        "triples": 0,
        "datasets": 0,
        "kinds": 0,
        "terms": 0,
        "questions_answerable": 0,
        "questions_total": 0,
        "published_graphs": 0,
    }
    base.update(over)
    return base


def _empty() -> dict[str, Any]:
    return {
        "datasets": [],
        "terms": [],
        "standards": [],
        "hubs": [],
        "questions": [],
        "edges": [],
        "facts": _facts(),
        "recent": [],
        "truncated": False,
    }


# --- registry（ファイルだけ。store は読まない）--------------------------------------------


def _read_meta(path: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _published_datasets(root: Path) -> list[dict[str, Any]]:
    """公開済みのデータセット（``created_at`` 順・同じなら id 順）。"""
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        did = valid_dataset_id(child.name)
        meta = _read_meta(child / "meta.json") if did else None
        if did is None or meta is None:
            continue
        if is_system_entry(meta) or not meta.get("promoted"):
            continue
        if meta.get("status") in _UNPUBLISHED_STATUS:
            continue
        created = str(meta.get("created_at") or meta.get("promoted_at") or "")
        out.append(
            {
                "id": did,
                "label": resolve_dataset_label(root, did),
                "created_at": created,
                "promoted_at": str(meta.get("promoted_at") or ""),
            }
        )
    out.sort(key=lambda d: (d["created_at"], d["id"]))
    return out


def _parse_at(value: str) -> datetime | None:
    """ISO の日時（日付だけも可）を UTC の aware にそろえる。読めなければ ``None``。"""
    if not value:
        return None
    try:
        at = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


# --- 標準用語 ------------------------------------------------------------------------------


def _standard_namespaces() -> list[str]:
    """標準の名前空間（長い順）。``known_namespaces`` は QUDT の量の種類を含む。"""
    return sorted(set(known_namespaces()), key=lambda ns: (-len(ns), ns))


def _standard_entry(iri: str) -> dict[str, str]:
    """``standards[]`` の 1 行。``prefix`` は ``grounding`` の表（無ければ量の種類の接頭辞）。"""
    best = ""
    prefix = ""
    for v in vocabularies():
        if iri.startswith(v.namespace) and len(v.namespace) > len(best):
            best, prefix = v.namespace, v.prefix
    if not prefix and iri.startswith(_QUDT_QK):
        prefix = _QK_PREFIX
    return {"iri": iri, "label": term_display_name(iri) or _fallback_label(iri), "prefix": prefix}


# --- store -----------------------------------------------------------------------------------


async def _links_by_dataset(
    client: SupportsSparql, kinds_by_ds: dict[str, set[str]]
) -> dict[tuple[str, str], list[str]]:
    """(データセット, 種類) → つながる種類。ontology graph の ``domain`` → ``range``。"""
    graphs = sorted({ontology_graph_iri(d) for d in kinds_by_ds})
    if not graphs:
        return {}
    from_named = "".join(f"FROM NAMED {_ref(g)}\n" for g in graphs)
    raw = await client.sparql_select(
        "SELECT DISTINCT ?g ?a ?b\n"
        f"{from_named}"
        f"WHERE {{ GRAPH ?g {{ ?p <{_RDFS}domain> ?a ; <{_RDFS}range> ?b }} "
        "FILTER(isIRI(?a) && isIRI(?b) && ?a != ?b) } "
        f"ORDER BY ?g ?a ?b LIMIT {_MAX_LINK_ROWS}"
    )
    prefix = ontology_graph_iri("x")[:-1]
    out: dict[tuple[str, str], set[str]] = {}
    for r in _rows(raw):
        g, a, b = _cell(r, "g"), _cell(r, "a"), _cell(r, "b")
        if not (g and a and b) or not g.startswith(prefix):
            continue
        ds = g[len(prefix) :]
        kinds = kinds_by_ds.get(ds, set())
        if a in kinds and b in kinds:  # 種類どうしだけ（項目＝リテラルや外の型は出さない）
            out.setdefault((ds, a), set()).add(b)
    return {k: sorted(v) for k, v in out.items()}


async def _count_triples(client: SupportsSparql) -> int:
    """ホームの事実の数（``getGraphStats`` と同じ数え方）。"""
    # `/api/sparql` と同じく、公開済み graph の FROM 合併を読む（素の default graph は
    # 本物の Oxigraph では空）。
    query = await substrate.canonical_merge_query(
        client, "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
    )
    raw = await client.sparql_select(query)
    rows = _rows(raw)
    try:
        return int(_cell(rows[0], "n") or 0) if rows else 0
    except ValueError:
        return 0


# --- 問い ------------------------------------------------------------------------------------


def _questions(
    root: Path,
    wired: dict[str, int],
    knowledge: dict[str, int],
    total_datasets: int,
    term_iris: set[str],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """``questions[]`` と 語 → その語の問い（tool_name）。

    データの問い（``for_terms`` 付き・``mode`` = ``data``）と、``themes.json`` の知識の問い
    （``selections[tool_name].mode == "knowledge"``・``mode`` = ``knowledge``）。知識の問いの
    ``answering`` は対象の語のデータの数（``wired.json``）のまま、``knowledge`` に対象の語の確定した
    知識の線の数（``knowledge.json``）を入れる。データの問いの ``knowledge`` は 0。
    """
    tools = read_registry_raw_tools(root, _VOCAB_REGISTRY_ID) or []
    questions: list[dict[str, Any]] = []
    by_term: dict[str, list[str]] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        needs = sorted({str(x) for x in (tool.get("for_terms") or [])})
        name = str(tool.get("name") or "")
        if not needs or not name:
            continue
        title = str(tool.get("title") or name)
        questions.append(
            {
                "tool_name": name,
                "title": title,
                "mode": "data",
                "theme_id": None,
                "theme_label": FIRST_QUESTIONS_LABEL,
                "answering": max((wired.get(x, 0) for x in needs), default=0),
                "knowledge": 0,
                "total_datasets": total_datasets,
                "needs": needs,
                "shape": title,
            }
        )
        for iri in needs:
            by_term.setdefault(iri, []).append(name)
    seen = {q["tool_name"] for q in questions}
    stored = themes.load_themes_file(root)
    for theme in (stored or {}).get("themes", []):
        selections = theme.get("selections")
        if not isinstance(selections, dict):
            continue
        for name in theme.get("questions") or []:
            sel = selections.get(name)
            if not (isinstance(sel, dict) and sel.get("mode") == "knowledge") or name in seen:
                continue
            try:
                target = term_iri(str(sel.get("target_slug") or ""))
            except ValueError:
                continue
            if target not in term_iris:  # 語が無い（外された）問いは出さない
                continue
            title = str(sel.get("title") or name)
            seen.add(name)
            questions.append(
                {
                    "tool_name": str(name),
                    "title": title,
                    "mode": "knowledge",
                    "theme_id": str(theme.get("id") or "") or None,
                    "theme_label": str(theme.get("label") or theme.get("id") or ""),
                    "answering": wired.get(target, 0),
                    "knowledge": knowledge.get(target, 0),
                    "total_datasets": total_datasets,
                    "needs": [target],
                    "shape": title,
                }
            )
            by_term.setdefault(target, []).append(str(name))
    questions.sort(key=lambda q: q["tool_name"])
    return questions, {k: sorted(set(v)) for k, v in by_term.items()}


def _answerable(q: dict[str, Any]) -> bool:
    """答えられる = データで 1 つ以上 **または** 知識で 1 つ以上。"""
    return q["answering"] >= 1 or q["knowledge"] >= 1


def _edges(alignments: list[dict[str, Any]], term_iris: set[str]) -> list[dict[str, Any]]:
    """語どうしの知識の線（``isPartOf`` / ``lowers`` / ``raises`` / ``affects``）。決定論の順。"""
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in alignments:
        rel, src, tgt = row["relation"], row["source"], row["target"]
        if rel not in _EDGE_RELATIONS or src not in term_iris or tgt not in term_iris:
            continue
        seen.setdefault(
            (src, tgt, rel),
            {"from": src, "to": tgt, "relation": rel, "reason": row.get("reason") or None},
        )
    return [seen[k] for k in sorted(seen)]


# --- 視点（ハブ）-----------------------------------------------------------------------------


def _hubs(root: Path, ds_ids: set[str], shown_kinds: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for meta in crosswalk_runtime.list_perspectives(root):
        if not meta.get("promoted") or meta.get("status") in _UNPUBLISHED_STATUS:
            continue
        pid = str(meta.get("crosswalk_perspective_id") or crosswalk_runtime.DEFAULT_PERSPECTIVE_ID)
        try:
            config = crosswalk_runtime.load_config(root, pid)
        except Exception:  # 壊れた設定の視点は参加者なしで出す（星図を止めない）
            config = None
        members: set[str] = set()
        if config is not None:
            for concept in config.concepts:
                for p in concept.participants:
                    if p.dataset_id in ds_ids and p.subject_class in shown_kinds:
                        members.add(str(p.subject_class))
        out.append(
            {
                "id": pid,
                "label": str(meta.get("name") or crosswalk_runtime.DEFAULT_PERSPECTIVE_NAME),
                "members": sorted(members),
            }
        )
    out.sort(key=lambda h: h["id"])
    return out


# --- 本体 ------------------------------------------------------------------------------------


async def sky_view(
    client: SupportsSparql,
    registry_root: Path | str | None,
    *,
    max_kinds: int = DEFAULT_MAX_KINDS,
) -> dict[str, Any]:
    """「星図」を組み立てる（返り値の形はモジュールの説明と契約メモ）。"""
    if registry_root is None:
        return _empty()
    root = Path(registry_root)
    published = _published_datasets(root)
    if not published:
        return _empty()  # 公開 0 件: store には問い合わせない
    ds_ids = {d["id"] for d in published}

    # 公開 graph の数は datasets・kinds と同じ「公開済み」で数える
    # （registry に公開済みの登録がある graph とハブの graph だけ。登録なし・取り下げは数えない）。
    graphs = [
        g
        for g in without_alignment_graph(await canonical_graphs(client))
        if is_hub_graph(g) or dataset_id_of_canonical_graph(g) in ds_ids
    ]

    # --- 種類（データセット × 種類 × 件数）-------------------------------------------------
    raw_counts = await kind_counts(client)
    truncated = bool(raw_counts.get("truncated"))
    pairs: dict[tuple[str, str], int] = {}
    for g in raw_counts.get("graphs", []):
        did = g.get("dataset_id")
        if g.get("hub") or did not in ds_ids:  # ハブ・登録の無い graph は中身として数えない
            continue
        for k in g.get("kinds", []):
            iri = k["class_iri"]
            if iri.startswith(_PROV_NS) or iri == _XW_LINK_CLASS:
                continue  # 取り込みの記録・ハブの per-link の記録はデータの種類ではない
            key = (did, iri)
            pairs[key] = max(pairs.get(key, 0), int(k["count"]))  # 版 graph を二重に数えない
    all_kind_iris = {iri for _d, iri in pairs}  # 切る前（切れた種類をことばの members に残さない）
    if len(pairs) > max_kinds:
        truncated = True
        keep = sorted(pairs, key=lambda k: (-pairs[k], k[0], k[1]))[:max_kinds]
        pairs = {k: pairs[k] for k in keep}
    kinds_by_ds: dict[str, set[str]] = {}
    for did, iri in pairs:
        kinds_by_ds.setdefault(did, set()).add(iri)
    shown_kinds = {iri for _d, iri in pairs}

    links = await _links_by_dataset(client, kinds_by_ds)
    # 種類の名前は一括で引く（種類ごとに registry 走査・問い合わせしない）。引けなければ読みくだし。
    try:
        kind_label = await class_labels(client, root, shown_kinds)
    except Exception:
        kind_label = {}

    # --- 線（直接の線だけ）と標準 ----------------------------------------------------------
    alignments = await crosswalk_runtime.list_alignments(client, registry_root=root)
    std_ns = _standard_namespaces()

    def is_standard(iri: str) -> bool:
        return any(iri.startswith(ns) for ns in std_ns)

    kind_standard: dict[str, str] = {}
    for iri in sorted(shown_kinds):
        if is_standard(iri):
            kind_standard[iri] = iri
    for row in sorted(alignments, key=lambda r: (r["source"], r["target"])):
        if (
            row["relation"] == "equivalentClass"
            and row["source"] in shown_kinds
            and row["source"] not in kind_standard
            and is_standard(row["target"])
        ):
            kind_standard[row["source"]] = row["target"]

    # --- ことば ----------------------------------------------------------------------------
    shared = await _read_terms(client)
    term_iris = {t.iri for t in shared}
    wired = load_wired(root)
    questions, questions_by_term = _questions(
        root, wired, load_knowledge(root), len(published), term_iris
    )

    members: dict[str, set[tuple[str, str, str]]] = {t.iri: set() for t in shared}
    term_standard: dict[str, dict[str, str]] = {}

    def add_member(term: str, source: str, relation: str, source_dataset: str | None) -> None:
        owners = sorted(d for d in ds_ids if source in kinds_by_ds.get(d, ()))
        if not owners and source not in all_kind_iris and source_dataset in ds_ids:
            owners = [str(source_dataset)]  # 項目（述語）など、種類として数えないもの
        for did in owners:
            members[term].add((source, did, relation))

    for row in alignments:
        rel, src, tgt = row["relation"], row["source"], row["target"]
        if (
            src in term_iris and is_standard(tgt) and rel in _STANDARD_MATCH_RELATIONS
        ):  # 標準への「一致」の線（量の種類 hasQuantityKind も。lowers / isPartOf は除く）
            cur = term_standard.get(src)
            if cur is None or (tgt, rel) < (cur["iri"], cur["relation"]):
                term_standard[src] = {"iri": tgt, "relation": rel}
        if rel not in _LINE_RELATIONS:
            continue
        if tgt in term_iris and row["source_kind"] == "dataset":
            add_member(tgt, src, rel, row["source_dataset"])
        if rel not in _SUBSUMPTION and src in term_iris and row["target_kind"] == "dataset":
            add_member(src, tgt, rel, row["target_dataset"])  # ≡ は向きを問わない

    terms = [
        {
            "iri": t.iri,
            "slug": t.slug,
            "label": t.label,
            "label_en": t.label_en,
            "kind": t.kind,
            "members": [
                {"iri": i, "dataset_id": d, "relation": r} for i, d, r in sorted(members[t.iri])
            ],
            "standard": term_standard.get(t.iri),
            "answering": wired.get(t.iri, 0),
            "questions": questions_by_term.get(t.iri, []),
        }
        for t in shared
    ]

    standard_iris = {t["standard"]["iri"] for t in terms if t["standard"]} | set(
        kind_standard.values()
    )
    standards = [_standard_entry(i) for i in sorted(standard_iris)]

    # --- データセット ----------------------------------------------------------------------
    datasets = [
        {
            "id": d["id"],
            "label": d["label"],
            "created_at": d["created_at"],
            "kinds": [
                {
                    "iri": iri,
                    "label": kind_label.get(iri) or _fallback_label(iri),
                    "count": pairs[(d["id"], iri)],
                    "links": links.get((d["id"], iri), []),
                    "standard": kind_standard.get(iri),
                }
                for iri in sorted(
                    kinds_by_ds.get(d["id"], ()), key=lambda i, did=d["id"]: (-pairs[(did, i)], i)
                )
            ],
        }
        for d in published
    ]

    hubs = _hubs(root, ds_ids, shown_kinds)

    # --- 事実と最近 ------------------------------------------------------------------------
    facts = _facts(
        triples=await _count_triples(client),
        datasets=len(published),
        kinds=len(shown_kinds),
        terms=len(terms),
        questions_answerable=sum(1 for q in questions if _answerable(q)),
        questions_total=len(questions),
        published_graphs=len(graphs),
    )
    recent = _recent(published, shared, alignments)

    return {
        "datasets": datasets,
        "terms": terms,
        "standards": standards,
        "hubs": hubs,
        "questions": questions,
        "edges": _edges(alignments, term_iris),
        "facts": facts,
        "recent": recent,
        "truncated": truncated,
    }


def _recent(
    published: list[dict[str, Any]], shared: list[Any], alignments: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """直近の公開・用語の鋳造・決めたこと（新しい順に :data:`RECENT_LIMIT` 件）。

    文は組まない（言語は画面が決める）。``a`` は主たる名前、``b`` は決めたことの相手の名前
    （それ以外は空）。``relation`` は決めたことの関係（それ以外は空）。
    """
    items: list[tuple[datetime, str, str, str, str, str]] = []
    for d in published:
        raw = d["promoted_at"] or d["created_at"]
        if (at := _parse_at(raw)) is not None:
            items.append((at, raw, "publish", d["label"], "", ""))
    for t in shared:
        if (at := _parse_at(t.created_at)) is not None:
            items.append((at, t.created_at, "term", t.label, "", ""))
    for al in alignments:
        if al["relation"] not in _DECISION_RELATIONS:
            continue
        if (at := _parse_at(al.get("at", ""))) is None:
            continue
        items.append(
            (at, al["at"], "decision", al["source_label"], al["target_label"], al["relation"])
        )
    items.sort(key=lambda x: (x[0], x[2], x[3], x[4], x[5]), reverse=True)
    return [
        {"at": raw, "kind": kind, "a": a, "b": b, "relation": rel}
        for _at, raw, kind, a, b, rel in items[:RECENT_LIMIT]
    ]
