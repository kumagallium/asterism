"""asterism.evidence — 論文の根拠の文をストアから引く（ui-redesign PR 12c）。

実物の ``pyoxigraph.Store`` と registry の実ファイルで動かす。公開済みの論文の文だけを引く・
未公開の下書きの文は引かない・形の悪い IRI は SPARQL に入れない・知識の行と星図に根拠が付く。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from asterism import evidence, knowledge, substrate
from asterism import shared_vocab as sv
from asterism.crosswalk_runtime import assert_alignment
from asterism.sky_view import sky_view

pyoxigraph = pytest.importorskip("pyoxigraph")

AT = "2026-11-05T00:00:00+00:00"
S = "https://example.org/paper/px/sent/"
LONG = "Grain boundaries scatter phonons and lower the lattice thermal conductivity. " * 6

PAPER_TTL = f"""
@prefix doco: <http://purl.org/spar/doco/> .
@prefix po: <http://www.essepuntato.it/2008/12/pattern#> .
@prefix nif: <http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#> .
@prefix lit: <https://kumagallium.github.io/asterism/papers/ontology#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
<https://example.org/paper/px/sec/1> a doco:Section ; dcterms:title "Results" ;
    lit:structuralPath "2" ; po:contains <https://example.org/paper/px/p/1> .
<https://example.org/paper/px/sec/2> a doco:Section ; lit:structuralPath "3" ;
    po:contains <https://example.org/paper/px/p/2> .
<https://example.org/paper/px/p/1> po:contains <{S}1> , <{S}long> .
<https://example.org/paper/px/p/2> po:contains <{S}2> .
<{S}1> a doco:Sentence ; nif:anchorOf "Pores lower the conductivity." ; nif:beginIndex 0 .
<{S}2> a doco:Sentence ; nif:anchorOf "No title here." ; nif:beginIndex 1 .
<{S}long> a doco:Sentence ; nif:anchorOf "{LONG}" ; nif:beginIndex 2 .
"""
DRAFT_TTL = PAPER_TTL.replace("paper/px", "paper/py")


class _Client:
    def __init__(self) -> None:
        self.store = pyoxigraph.Store()
        self.queries: list[str] = []

    def load(self, graph: str, ttl: str) -> None:
        self.store.load(
            ttl.encode("utf-8"), mime_type="text/turtle", to_graph=pyoxigraph.NamedNode(graph)
        )

    async def sparql_update(self, update: str) -> None:
        self.store.update(update)

    async def sparql_select(self, query: str) -> dict:
        self.queries.append(query)
        result = self.store.query(query, use_default_graph_as_union=True)
        names = [v.value for v in result.variables]
        bindings = []
        for solution in result:
            row: dict[str, Any] = {}
            for name in names:
                term = solution[name]
                if term is None:
                    continue
                if isinstance(term, pyoxigraph.NamedNode):
                    row[name] = {"type": "uri", "value": term.value}
                else:
                    row[name] = {"type": "literal", "value": term.value}
            bindings.append(row)
        return {"results": {"bindings": bindings}}


def _meta(root: Path, did: str, **fields: Any) -> None:
    (root / did).mkdir(parents=True, exist_ok=True)
    (root / did / "meta.json").write_text(
        json.dumps({"id": did, **fields}, ensure_ascii=False), encoding="utf-8"
    )


def _terms(c: _Client) -> None:
    """下げるもの 2 つ（種類）と下げられるもの（項目）を共有のことばに宣言する。"""
    for slug, typ in (("pore", "Class"), ("other", "Class"), ("tc", "Property")):
        iri = "http://www.w3.org/2000/01/rdf-schema#Class"
        if typ == "Property":
            iri = "http://www.w3.org/1999/02/22-rdf-syntax-ns#Property"
        c.store.update(
            f"INSERT DATA {{ GRAPH <{sv.SHARED_VOCAB_GRAPH}> {{ "
            f"<{sv.term_iri(slug)}> a <{iri}> }} }}"
        )


async def _world(tmp_path: Path) -> tuple[_Client, Path]:
    """公開済みの論文 paper-x と、未公開（下書き）の論文 paper-y。"""
    root = tmp_path / "registry"
    c = _Client()
    _meta(root, "paper-x", name="論文X", promoted=True, source_kind="xml", created_at="2026-11-01",
          live_graph=substrate.versioned_graph_iri("paper-x", 1))  # fmt: skip
    live = substrate.versioned_graph_iri("paper-x", 1)
    c.load(live, PAPER_TTL)
    key = substrate.canonical_graph_iri("paper-x")
    await substrate.mark_graph_promoted(c, key, live_graph=live)
    _meta(root, "paper-y", name="下書き", promoted=False, source_kind="xml",
          graph_iri=substrate.versioned_graph_iri("paper-y", 1))  # fmt: skip
    c.load(substrate.versioned_graph_iri("paper-y", 1), DRAFT_TTL)
    return c, root


async def test_a_published_paper_sentence_comes_with_section_path_and_paper_name(
    tmp_path: Path,
) -> None:
    c, root = await _world(tmp_path)
    got = await evidence.resolve_evidence(c, [S + "1", S + "2"], registry_root=root)
    assert got[S + "1"] == {
        "iri": S + "1",
        "text": "Pores lower the conductivity.",
        "full": "Pores lower the conductivity.",
        "section": "Results",
        "path": "2",
        "dataset_id": "paper-x",
        "dataset_label": "論文X",
    }
    assert got[S + "2"]["section"] is None and got[S + "2"]["path"] == "3"
    assert len(c.queries) == 1  # SPARQL は 1 回


async def test_a_long_sentence_is_cut_at_150_chars_and_full_keeps_all(tmp_path: Path) -> None:
    c, root = await _world(tmp_path)
    got = (await evidence.resolve_evidence(c, [S + "long"], registry_root=root))[S + "long"]
    assert len(got["text"]) == 151 and got["text"].endswith("…")
    assert got["full"] == LONG.strip()


async def test_sentences_of_an_unpublished_paper_and_bad_iris_are_not_read(
    tmp_path: Path,
) -> None:
    c, root = await _world(tmp_path)
    draft = S.replace("px", "py") + "1"
    got = await evidence.resolve_evidence(
        c, [draft, "not an iri", 'https://x/"} DROP ALL {"'], registry_root=root
    )
    assert got == {}
    assert len(c.queries) == 1  # 形の悪い IRI は VALUES に入らない
    assert "not an iri" not in c.queries[0] and "DROP" not in c.queries[0]
    assert await evidence.resolve_evidence(c, [S + "1"], registry_root=None) == {}


async def test_knowledge_lines_carry_evidence_iris_and_none_means_empty(tmp_path: Path) -> None:
    c, _root = await _world(tmp_path)
    _terms(c)
    await assert_alignment(
        c, sv.term_iri("pore"), sv.term_iri("tc"), "lowers", at=AT, evidence=[S + "2", S + "1"]
    )
    await assert_alignment(c, sv.term_iri("other"), sv.term_iri("tc"), "lowers", at=AT)
    items = await knowledge.factors(c, sv.term_iri("tc"), "lowers")
    by = {i["iri"]: i["evidence"] for i in items}
    assert by[sv.term_iri("pore")] == [S + "1", S + "2"]
    assert by[sv.term_iri("other")] == []


async def test_sky_view_lists_evidence_terms_for_papers_only(tmp_path: Path) -> None:
    c, root = await _world(tmp_path)
    _terms(c)
    pore, tc, other = (sv.term_iri(x) for x in ("pore", "tc", "other"))
    await assert_alignment(c, pore, tc, "lowers", at=AT, evidence=[S + "1"])
    await assert_alignment(c, other, tc, "lowers", at=AT, evidence=[S + "2"])  # tc は重複しない
    out = await sky_view(c, root)
    paper = next(d for d in out["datasets"] if d["id"] == "paper-x")
    assert paper["evidence_terms"] == sorted({pore, tc, other})
    # 論文でないデータセットには欄を出さない
    _meta(root, "wx", name="観測", promoted=True, created_at="2026-11-02")
    again = await sky_view(c, root)
    assert "evidence_terms" not in next(d for d in again["datasets"] if d["id"] == "wx")


async def test_an_iri_with_a_trailing_newline_is_dropped_and_the_others_are_still_read(
    tmp_path: Path,
) -> None:
    from asterism import themes

    c, root = await _world(tmp_path)
    got = await evidence.resolve_evidence(
        c, [S + "1\n", "https://x/a\n", S + "2"], registry_root=root
    )
    assert list(got) == [S + "2"]  # 改行つきは入らず、SPARQL 全体は落ちない
    assert "\n<" not in c.queries[0].split("VALUES ?e")[1].split("}")[0]
    assert themes._evidence_iris([S + "1\n", S + "2"]) == (S + "2",)
