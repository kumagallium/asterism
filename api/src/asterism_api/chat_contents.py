"""点の中身（ABox）— ui-redesign PR 11k・§19.11。地図で押したものの実データを表で返す。

``GET /api/chat/contents`` の組み立て。AI は呼ばない・読むだけ。表は :func:`chat_tables.
table_for_kind` をそのまま使う（チャットに差し込まれる表と同じ形）。押したものごとの出し方:

* ``kind`` 単独 — 表の種類 1 つ。持ち主の公開済みデータセットごとに、そのデータセットの graph
  だけを読んだ表（同じ種類の IRI が複数にあっても行は混ざらない）。
* ``dataset`` + ``kind`` — そのデータセットの graph だけを読んだ、その種類の表 1 つ。
* ``term`` — 用語。members が種類ならデータセットごとのその種類の表。members が項目
  （property）なら、その項目を持つ種類を ``class_schema`` で引く（引けなければ ``tables: []``
  と ``note``）。
* ``dataset`` — そのデータセットの種類の表。公開済みならその版グラフ、未公開（取り込み済み）なら
  登録の ``graph_iri``（公開前の版グラフ）を読む。取り込んでいなければ ``tables: []``。
* ``session`` — 何も押していないとき。話の最後の添えた表（``attachment.dataset_id``）。

返りは ``{title, tables: [{dataset: {id, label}, table}], more, note}``。``tables`` は最大
:data:`MAX_TABLES`、残りの数が ``more``。SPARQL は :mod:`chat_tables` の固定の形と、種類を数える
1 本（graph は決定論に決めたものだけ）で、利用者の文字列は埋め込まない。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from asterism import substrate
from asterism.class_schema import class_schema
from asterism.mapping_ir_read import BUILTIN_PREFIXES, read_mapping_ir
from asterism.mapping_ir_read import expand as mi_expand
from asterism.subjects import resolve_dataset_label

from asterism_api import chat_tables, registry

__all__ = ["MAX_TABLES", "ContentsError", "build_contents", "targets_of_term"]

logger = logging.getLogger(__name__)

MAX_TABLES = 3
_MAX_KINDS = 50  # 1 つの graph から数える種類の上限
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

_NOTES = {
    "ja": {
        "no_term": "この用語に結ばれた表はありません",
        "unpublished": "確認して公開すると、ここに行が出ます",
    },
    "en": {
        "no_term": "No table is linked to this term",
        "unpublished": "Rows appear here once you confirm and publish",
    },
}


class ContentsError(Exception):
    """入力の誤り（``status`` は HTTP の番号: 400 / 404）。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _note(lang: str, key: str) -> str:
    return _NOTES.get(lang, _NOTES["ja"])[key]


def _term_label(term: dict, lang: str) -> str:
    if lang == "en":
        return str(term.get("label_en") or term.get("label") or term.get("iri") or "")
    return str(term.get("label") or term.get("label_en") or term.get("iri") or "")


async def _kinds_of(client: Any, graphs: list[str]) -> list[str]:
    """graph の中の種類（``rdf:type`` の目的語）を個体の多い順に。"""
    if not graphs:
        return []
    named = substrate.canonical_from_clauses(graphs, named=True)
    raw = await client.sparql_select(
        f"SELECT ?c (COUNT(DISTINCT ?s) AS ?__n)\n{named}"
        f"WHERE {{ GRAPH ?g {{ ?s <{_RDF_TYPE}> ?c FILTER(isIRI(?c)) }} }} "
        f"GROUP BY ?c ORDER BY DESC(?__n) ?c LIMIT {_MAX_KINDS}"
    )
    out = [chat_tables._v(r, "c") for r in chat_tables._rows(raw)]
    return [c for c in out if c and chat_tables._IRI_RE.match(c)]


async def targets_of_term(
    client: Any, root: Path, sky: dict, term_iri: str
) -> tuple[dict | None, list[tuple[str, str]], int]:
    """用語 → ``(用語, [(dataset_id, 種類の IRI)], 引かずに残した数)``。

    用語が星図に無ければ ``(None, [], 0)``。members が公開済みの種類ならそのまま。そうでなければ
    項目とみなし、そのデータセットの種類のうち ``class_schema`` の性質にその項目を持つ最初の 1 つ。
    項目の members は :data:`MAX_TABLES` 件で打ち切ってから引く（逐次の読みを増やさない）。
    残した数は 3 つ目より後の項目の members の数。"""
    term = next((t for t in sky.get("terms") or [] if t.get("iri") == term_iri), None)
    if term is None:
        return None, [], 0
    kinds_by_ds: dict[str, list[str]] = {
        str(d["id"]): [str(k["iri"]) for k in d.get("kinds") or []]
        for d in sky.get("datasets") or []
    }
    all_kinds = {k for ks in kinds_by_ds.values() for k in ks}
    out: list[tuple[str, str]] = []
    props_seen = 0
    skipped = 0
    for m in term.get("members") or []:
        iri, did = str(m.get("iri") or ""), str(m.get("dataset_id") or "")
        if not iri or not did:
            continue
        if iri in all_kinds:
            pair = (did, iri)
            if iri in kinds_by_ds.get(did, []) and pair not in out:
                out.append(pair)
            continue
        props_seen += 1
        if props_seen > MAX_TABLES:
            skipped += 1
            continue
        for kind in kinds_by_ds.get(did, []):  # 項目: それを持つ種類を引く
            try:
                schema = await class_schema(client, root, kind)
            except Exception:
                logger.warning("contents: class_schema failed", exc_info=True)
                continue
            props = {str(p.get("iri")) for p in (schema or {}).get("properties") or []}
            if iri in props:
                if (did, kind) not in out:
                    out.append((did, kind))
                break
    return term, out, skipped


async def _owners_of_kind(client: Any, kind_iri: str, published: list[str]) -> list[str]:
    """種類を持つ公開済みのデータセット id（版グラフごとに調べ、id の順）。"""
    if not published:
        return []
    named = substrate.canonical_from_clauses(published, named=True)
    raw = await client.sparql_select(
        f"SELECT DISTINCT ?g\n{named}"
        f"WHERE {{ GRAPH ?g {{ ?s <{_RDF_TYPE}> <{kind_iri}> }} }} LIMIT 200"
    )
    ids = {
        substrate.dataset_id_of_canonical_graph(g)
        for g in (chat_tables._v(r, "g") for r in chat_tables._rows(raw))
        if g
    }
    return sorted(i for i in ids if i)


def registry_names(root: Path, dataset_id: str, kind_iri: str) -> dict[str, str]:
    """登録の設計が付けた名前 → ``{IRI: 名前}``（種類の ``subject.label`` と、その種類の項目の
    ``label``。同じ項目に名前が割れるときは付けない）。読めなければ空。"""
    data = registry.load_dataset(root, dataset_id)
    text = ((data or {}).get("artifacts") or {}).get("mapping.yaml") or ""
    if not text.strip():
        return {}
    try:
        ir = read_mapping_ir(text)
    except Exception:
        return {}
    prefixes = dict(BUILTIN_PREFIXES) | dict(ir.prefixes)
    out: dict[str, str] = {}
    found: dict[str, set[str]] = {}
    for tm in ir.maps:
        if kind_iri not in {mi_expand(prefixes, c) for c in tm.subject_classes}:
            continue
        if (tm.subject_label or "").strip():
            out.setdefault(kind_iri, tm.subject_label.strip())
        for prop in tm.properties:
            label = (prop.label or prop.link_label or "").strip()
            if prop.predicate and label:
                found.setdefault(mi_expand(prefixes, prop.predicate), set()).add(label)
    out.update({p: next(iter(v)) for p, v in found.items() if len(v) == 1})
    return out


def _last_attached(session: dict | None) -> str | None:
    """話の最後の添えた表の dataset id（戻した手番は数えない）。"""
    for turn in reversed((session or {}).get("turns") or []):
        att = turn.get("attachment") or {}
        if att.get("dataset_id") and turn.get("state") != "reverted":
            return str(att["dataset_id"])
    return None


async def _dataset_targets(
    client: Any, root: Path, dataset_id: str, published: list[str]
) -> tuple[list[str], list[tuple[str, str]], bool]:
    """データセット → ``(読む graph, [(id, 種類)], 公開前で読む graph が無い)``。"""
    data = registry.load_dataset(root, dataset_id)
    if data is None:
        raise ContentsError(404, "そのデータセットはありません")
    meta = data.get("meta") or {}
    if meta.get("promoted"):
        graphs = [g for g in published if substrate.dataset_id_of_canonical_graph(g) == dataset_id]
    else:
        iri = meta.get("graph_iri")
        graphs = [iri] if isinstance(iri, str) and chat_tables._IRI_RE.match(iri) else []
    kinds = await _kinds_of(client, graphs)
    return graphs, [(dataset_id, k) for k in kinds], not graphs


async def build_contents(
    client: Any,
    root: Path | str,
    *,
    kind: str | None = None,
    term: str | None = None,
    dataset: str | None = None,
    session: dict | None = None,
    use_session: bool = False,
    sky: dict | None = None,
    lang: str = "ja",
    limit: int = chat_tables.MAX_ROWS,
) -> dict[str, Any]:
    """押したもの → ``{title, tables, more, note}``。

    指定は 5 通り: ``kind`` 単独・``term``・``dataset`` 単独・``use_session``（``session`` は
    読み込んだ話）・``dataset`` + ``kind``（そのデータセットの graph だけを読む）。``term`` は
    ``sky``（``GET /api/sky`` の形）が要る。表は (データセット, 種類) の対ごとに、そのデータセットの
    graph だけを読む（同じ種類の IRI が複数のデータセットにあっても行が混ざらない）。"""
    root = Path(root)
    chosen = [term is not None, use_session, dataset is None and kind is not None]
    chosen.append(dataset is not None)  # dataset 単独と dataset + kind は 1 通りに数える
    if sum(chosen) != 1:
        raise ContentsError(
            400,
            "kind・term・dataset・session のどれか 1 つ（dataset と kind は一緒に可）を渡す",
        )
    for iri in (kind, term):
        if iri is not None:
            try:
                chat_tables.check_iri(iri)
            except ValueError as exc:
                raise ContentsError(400, str(exc)) from exc
    limit = max(1, min(int(limit), chat_tables.MAX_ROWS))
    title, note, skipped = "", None, 0
    #: データセット id → 読む graph（下の表を読むとき必ず渡す）
    scoped: dict[str, list[str]] = {}
    drafts: set[str] = set()  # 公開前のデータセット（名前は登録の設計から）
    pairs: list[tuple[str, str]] = []
    published = await chat_tables._published_graphs(client, root)

    def graphs_of(did: str) -> list[str]:
        return [g for g in published if substrate.dataset_id_of_canonical_graph(g) == did]

    if kind is not None and dataset is None:
        owners = await _owners_of_kind(client, kind, published)
        pairs = [(d, kind) for d in owners] or [("", kind)]
        scoped = {d: graphs_of(d) for d in owners}
    elif term is not None:
        found, pairs, skipped = await targets_of_term(client, root, sky or {}, term)
        title = _term_label(found, lang) if found else term
        if not pairs:
            note = _note(lang, "no_term")
        scoped = {d: graphs_of(d) for d, _k in pairs}
    else:
        dataset_id = dataset if dataset is not None else _last_attached(session)
        if dataset_id is not None:
            try:
                graphs, pairs, unread = await _dataset_targets(client, root, dataset_id, published)
            except ContentsError:
                if dataset is not None:
                    raise
                return {"title": "", "tables": [], "more": 0, "note": None}  # 添えた表が消えた
            title = resolve_dataset_label(root, dataset_id)
            scoped = {dataset_id: graphs}
            if not (registry.load_dataset(root, dataset_id) or {}).get("meta", {}).get("promoted"):
                drafts.add(dataset_id)
            if kind is not None:  # dataset + kind: その種類の表 1 つだけ
                pairs = [(dataset_id, kind)]
            if unread:
                note = _note(lang, "unpublished")
    tables: list[dict[str, Any]] = []
    for did, kind_iri in pairs[:MAX_TABLES]:
        table = await chat_tables.table_for_kind(
            client,
            root,
            kind_iri,
            limit=limit,
            lang=lang,
            graphs=scoped.get(did),
            names=registry_names(root, did, kind_iri) if did in drafts else None,
        )
        if kind is not None:
            title = table["kind"]["label"]
        if not did:  # 持ち主が見つからない種類: 登録の設計から引く（無ければ空）
            try:
                schema = await class_schema(client, root, kind_iri)
            except Exception:
                schema = None
            did = str((schema or {}).get("dataset_id") or "")
        label = resolve_dataset_label(root, did) if did else ""
        tables.append({"dataset": {"id": did, "label": label}, "table": table})
    more = max(0, len(pairs) - MAX_TABLES) + skipped
    return {"title": title, "tables": tables, "more": more, "note": note}
