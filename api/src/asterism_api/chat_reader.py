"""公開済みの論文の本文を、節・段落・文の順で引く（ui-redesign PR 13b・design.md §22）。

根拠の文を押したときに、会話の列を論文の本文に入れ替えるための読み出し。
``GET /api/chat/paper`` の本体。

* 見る graph は :func:`asterism.evidence._paper_graphs` と同じ（公開済みの論文だけ・graph IRI は
  registry から引いて形を検査済み）。外からの文字列は SPARQL に入れない。
* SPARQL は 2 回（文＋段落＋節の対応・節の題名と入れ子）。文書の順は ``nif:beginIndex``。
* 入れ子の節は平らにして ``depth``（0 始まり）。文を持たない節は出さない。
* ``evidence`` = この論文の文のうち、確定した関係の根拠になっているもの。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from asterism import crosswalk_runtime, evidence
from asterism import documents as doc

__all__ = ["MAX_SENTENCES", "PaperNotFound", "build_paper", "flatten"]

#: 返す文の上限（超えたら先頭から・``truncated: true``）
MAX_SENTENCES = 5000


class PaperNotFound(Exception):
    """公開済みの論文ではない（未公開・論文でない・無い）。"""


def _v(b: dict, k: str) -> str | None:
    return (b.get(k) or {}).get("value")


def _rows(res: Any) -> list[dict]:
    results = res.get("results") if isinstance(res, dict) else None
    return list(results.get("bindings", [])) if isinstance(results, dict) else []


def flatten(sentence_bindings: list[dict], section_bindings: list[dict]) -> list[dict]:
    """SPARQL の結果 → 平らな節の並び（文書の順）。純関数。

    節 = ``{iri, title, path, role, depth, paragraphs: [{iri, sentences: [{iri, text}]}]}``。
    節の順は「その節（子を含む）の最初の文の ``beginIndex``」・段落と文は ``beginIndex`` 順。"""
    secs: dict[str, dict] = {}

    def sec_of(iri: str) -> dict:
        return secs.setdefault(
            iri, {"title": "", "path": "", "role": None, "children": set(), "paras": {}}
        )

    for b in section_bindings:
        iri = _v(b, "sec")
        if not iri:
            continue
        s = sec_of(iri)
        if _v(b, "title"):
            s["title"] = _v(b, "title") or ""
        if _v(b, "path"):
            s["path"] = _v(b, "path") or ""
        if _v(b, "role") and s["role"] is None:
            s["role"] = (_v(b, "role") or "").rsplit("/", 1)[-1].lower() or None
        if _v(b, "child"):
            s["children"].add(_v(b, "child"))
    seen: set[str] = set()
    for b in sentence_bindings:
        iri, body, sec, para = _v(b, "s"), _v(b, "t"), _v(b, "sec"), _v(b, "p")
        if not iri or iri in seen or not body or not body.strip() or not sec or not para:
            continue
        seen.add(iri)
        try:
            begin = int(_v(b, "b") or 0)
        except ValueError:
            begin = 0
        p = sec_of(sec)["paras"].setdefault(para, {"iri": para, "begin": begin, "sentences": []})
        p["begin"] = min(p["begin"], begin)
        p["sentences"].append({"iri": iri, "text": " ".join(body.split()), "begin": begin})

    children = {c for s in secs.values() for c in s["children"]}
    inf = float("inf")
    memo: dict[str, float] = {}

    def first(iri: str, trail: frozenset[str] = frozenset()) -> float:
        if iri in memo:
            return memo[iri]
        if iri in trail or iri not in secs:
            return inf
        s = secs[iri]
        own = [p["begin"] for p in s["paras"].values()]
        sub = [first(c, trail | {iri}) for c in s["children"]]
        memo[iri] = min([*own, *sub], default=inf)
        return memo[iri]

    out: list[dict] = []
    emitted: set[str] = set()

    def walk(iri: str, depth: int) -> None:
        if iri in emitted or iri not in secs or first(iri) == inf:
            return
        emitted.add(iri)
        s = secs[iri]
        paras = sorted(s["paras"].values(), key=lambda p: (p["begin"], p["iri"]))
        out.append(
            {
                "iri": iri,
                "title": s["title"] or None,
                "path": s["path"] or None,
                "role": s["role"],
                "depth": depth,
                "paragraphs": [
                    {
                        "iri": p["iri"],
                        "sentences": [
                            {"iri": x["iri"], "text": x["text"]}
                            for x in sorted(p["sentences"], key=lambda q: (q["begin"], q["iri"]))
                        ],
                    }
                    for p in paras
                ],
            }
        )
        depth_next = depth + 1
        for c in sorted(s["children"], key=lambda c: (first(c), c)):
            walk(c, depth_next)

    roots = [i for i in secs if i not in children]
    for r in sorted(roots, key=lambda i: (first(i), i)):
        walk(r, 0)
    return out


async def build_paper(client: Any, registry_root: Path | str, dataset_id: str) -> dict[str, Any]:
    """``GET /api/chat/paper`` の返り。公開済みの論文でなければ :class:`PaperNotFound`。"""
    root = Path(registry_root)
    graphs = evidence._paper_graphs(root)
    found = next(((g, v[1]) for g, v in graphs.items() if v[0] == dataset_id), None)
    if found is None:
        raise PaperNotFound(dataset_id)
    graph, label = found
    po, nif, lit = doc.PO, doc.NIF, doc.LIT
    section = f"<{doc.DOCO}Section>"
    sent = await client.sparql_select(
        f"SELECT ?s ?t ?b ?p ?sec WHERE {{ GRAPH <{graph}> {{ "
        f"?s a <{doc.DOCO}Sentence> ; <{nif}anchorOf> ?t ; <{nif}beginIndex> ?b . "
        f"?p <{po}contains> ?s . ?sec <{po}contains> ?p . ?sec a {section} }} }} "
        f"ORDER BY ?b LIMIT {MAX_SENTENCES + 1}"
    )
    secs = await client.sparql_select(
        f"SELECT ?sec ?title ?path ?role ?child WHERE {{ GRAPH <{graph}> {{ ?sec a {section} . "
        f"OPTIONAL {{ ?sec <{doc.DCTERMS}title> ?title }} "
        f"OPTIONAL {{ ?sec <{lit}structuralPath> ?path }} "
        f'OPTIONAL {{ ?sec a ?role . FILTER(STRSTARTS(STR(?role), "{doc.DEO}")) }} '
        f"OPTIONAL {{ ?sec <{po}contains> ?child . ?child a {section} }} }} }}"
    )
    sent_rows = _rows(sent)
    truncated = len(sent_rows) > MAX_SENTENCES
    sections = flatten(sent_rows[:MAX_SENTENCES], _rows(secs))
    mine = {x["iri"] for s in sections for p in s["paragraphs"] for x in p["sentences"]}
    lines = await crosswalk_runtime.list_alignments(client, registry_root=root)
    cited = {e for ln in lines for e in ln.get("evidence") or []}
    ordered = [
        x["iri"]
        for s in sections
        for p in s["paragraphs"]
        for x in p["sentences"]
        if x["iri"] in cited and x["iri"] in mine
    ]
    return {
        "dataset_id": dataset_id,
        "label": label,
        "sections": sections,
        "evidence": ordered,
        "truncated": truncated,
    }
