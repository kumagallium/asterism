"""asterism.shared_vocab (ADR upper-structure-shared-terms.md §2.1〜§2.4).

The store is a real ``rdflib.Dataset`` behind the same client shape the crosswalk
runtime tests use, so the REAL SPARQL (minting, the aggregate that counts answering
datasets, the upward path, the upper map) runs end to end without a triplestore.
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest
import rdflib
import yaml

from asterism import shared_vocab as sv
from asterism import substrate
from asterism.crosswalk import XW
from asterism.crosswalk_runtime import ALIGNMENT_GRAPH, assert_alignment, remove_alignment
from asterism.query_tools import lint_query_tool, parse_query_tools
from asterism.shared_vocab import (
    CQSpec,
    SharedTerm,
    TermError,
    TermExists,
    TermHasLines,
    TermNotFound,
)

AT = "2026-10-09T00:00:00+00:00"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_A = "https://example.org/xrd-a#"
NS_B = "https://example.org/xrd-b#"
A_REC = NS_A + "Record"
B_REC = NS_B + "Record"
A_PROP = NS_A + "intensity"
B_PROP = NS_B + "counts"


class _Client:
    """OxigraphClient stand-in over a real rdflib Dataset; records every SELECT."""

    def __init__(self, ds: rdflib.Dataset | None = None) -> None:
        self.ds = ds if ds is not None else rdflib.Dataset()
        self.selects: list[str] = []

    async def sparql_select(self, query: str) -> dict:
        self.selects.append(query)
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)


def _key(dataset_id: str) -> str:
    # hub names (``crosswalk/composition``) and ``xrd-a/v2`` are not valid dataset ids
    if "/" in dataset_id:
        return substrate.CANONICAL_GRAPH_BASE + dataset_id
    return substrate.canonical_graph_iri(dataset_id)


async def _publish(
    client: _Client,
    dataset_id: str,
    rows: list[tuple[str, str, str]],
    *,
    version: int | None = 1,
) -> str:
    """Put ``(subject, predicate, object-iri-or-literal)`` rows into a dataset's data
    graph and flag it promoted; returns the data graph IRI. ``version=None`` keeps the
    data in the key graph itself (a pre-version dataset)."""
    key = _key(dataset_id)
    data = substrate.versioned_graph_iri(dataset_id, version) if version else key
    g = client.ds.graph(rdflib.URIRef(data))
    for s, p, o in rows:
        obj = rdflib.URIRef(o) if o.startswith("http") else rdflib.Literal(o)
        g.add((rdflib.URIRef(s), rdflib.URIRef(p), obj))
    await substrate.mark_graph_promoted(client, key, live_graph=data if version else None)
    return data


def _typed(entity: str, cls: str) -> tuple[str, str, str]:
    return (entity, RDF + "type", cls)


async def _seed_two_datasets(client: _Client) -> None:
    await _publish(client, "xrd-a", [_typed("urn:a1", A_REC), ("urn:a1", A_PROP, "12")])
    await _publish(client, "xrd-b", [_typed("urn:b1", B_REC), ("urn:b1", B_PROP, "7")])


def _ontology(client: _Client, dataset_id: str, terms: list[tuple[str, str, str]]) -> None:
    g = client.ds.graph(rdflib.URIRef(substrate.ontology_graph_iri(dataset_id)))
    for iri, kind, label in terms:
        g.add((rdflib.URIRef(iri), rdflib.RDF.type, rdflib.URIRef(kind)))
        g.add((rdflib.URIRef(iri), rdflib.RDFS.label, rdflib.Literal(label, lang="ja")))


def _declare(client: _Client, *iris: str, kind: str = "class") -> None:
    """Put a bare TBox declaration into the shared graph — a term must be minted before
    a line may touch it (``assert_alignment`` refuses an unminted ``sv:`` term)."""
    t = rdflib.URIRef((RDFS + "Class") if kind == "class" else (RDF + "Property"))
    g = client.ds.graph(rdflib.URIRef(sv.SHARED_VOCAB_GRAPH))
    for iri in iris:
        g.add((rdflib.URIRef(iri), rdflib.RDF.type, t))


async def _mint_class(client: _Client, root: Path, slug: str = "diffraction_point") -> dict:
    return await sv.mint_term(
        client,
        root,
        slug=slug,
        kind="class",
        label="回折点",
        cqs=[CQSpec("回折点は、どのデータセットに何件あるか", "count")],
        at=AT,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_term_iri_slug_and_system_entry() -> None:
    assert sv.term_iri("diffraction_point") == sv.SV + "diffraction_point"
    assert sv.slug_of(sv.SV + "a1_b") == "a1_b"
    assert sv.slug_of(sv.SV + "Bad") is None
    assert sv.slug_of("https://example.org/x") is None
    assert sv.is_shared_iri(sv.SV + "x") and not sv.is_shared_iri(XW + "x")
    for bad in ("", "1a", "A", "a-b", "あ"):
        with pytest.raises(ValueError):
            sv.term_iri(bad)
    assert sv.is_system_entry({"is_crosswalk": True})
    assert sv.is_system_entry({"is_shared_vocab": True})
    assert not sv.is_system_entry({"id": "x"})
    assert sv.is_wired(1) and not sv.is_wired(0)
    assert sv.SHARED_VOCAB_GRAPH == substrate.SHARED_VOCAB_GRAPH
    assert sv.SHARED_VOCAB_GRAPH == "https://kumagallium.github.io/asterism/graph/vocab/shared"


def test_normalize_label_matches_step0_meaning_key() -> None:
    def meaning_key(meaning: str) -> str:  # step0 skeleton_annotate._meaning_key, verbatim
        return " ".join(unicodedata.normalize("NFKC", meaning).casefold().split())

    for s in [
        "Composition",
        "composition ",
        "ＣＯＭＰ  ｏｓｉｔｉｏｎ",
        "組成　 ",
        "Ｔ（K）",
        "a\tb\n c",
        "",
    ]:
        assert sv.normalize_label(s) == meaning_key(s)
    assert sv.normalize_label("Ｃｏｍｐ  A") == "comp a"


def test_classify_endpoint_six_branches_namespace_first() -> None:
    onto = {A_REC: "xrd-a", sv.SV + "inside": "xrd-a", "https://w3id.org/cmso/Material": "xrd-a"}
    ns = ["https://w3id.org/cmso/", sv._QUDT_QK]
    shared = {sv.SV + "diffraction_point"}

    def c(iri: str) -> tuple[str, str | None]:
        return sv.classify_endpoint(
            iri, ontology_terms=onto, shared_iris=shared, known_namespaces=ns
        )

    assert c(sv.SV + "diffraction_point") == ("shared", None)
    assert c(sv.SV + "never_minted") == ("shared_unminted", None)
    assert c(XW + "Composition") == ("perspective", None)
    assert c("https://w3id.org/cmso/Material") == ("standard", None)
    assert c("http://qudt.org/vocab/quantitykind/Temperature") == ("standard", None)
    assert c(A_REC) == ("dataset", "xrd-a")
    assert c("https://example.org/else") == ("unknown", None)
    # the namespace decides first even if a dataset's ontology graph lists the term
    assert c(sv.SV + "inside") == ("shared_unminted", None)


def test_known_namespaces_reads_the_catalog_and_adds_qudt_quantitykind() -> None:
    ns = sv.known_namespaces()
    assert sv._QUDT_QK in ns and len(ns) > 3 and all(n.startswith("http") for n in ns)


def _term(slug: str, label: str, label_en: str | None = None, kind: str = "class") -> SharedTerm:
    return SharedTerm(sv.SV + slug, slug, kind, label, label_en, None, AT)


def test_fit_candidates_exact_only_generic_empty_standard_first() -> None:
    shared = [_term("dp", "回折点", "Diffraction Point"), _term("other", "別の語")]
    ds_terms = [
        {"iri": B_PROP, "label": "diffraction  point", "dataset_id": "xrd-b", "kind": "property"},
        {"iri": B_REC, "label": "回折点に近い", "dataset_id": "xrd-b", "kind": "class"},
    ]
    std = [{"iri": "https://w3id.org/cmso/Foo", "label": "Foo"}]
    out = sv.fit_candidates(
        "回折点",
        "DIFFRACTION_POINT",
        shared_terms=shared,
        dataset_terms=ds_terms,
        standard_hits=std,
    )
    assert [(o["kind"], o["term"], o["matched_by"]) for o in out] == [
        ("standard", "https://w3id.org/cmso/Foo", "column"),
        ("shared", sv.SV + "dp", "label"),
    ]
    # the column is a case/width variant of the English label -> matches by column
    out = sv.fit_candidates(
        "ぜんぜん別",
        "ＤＩＦＦＲＡＣＴＩＯＮ  point",
        shared_terms=shared,
        dataset_terms=ds_terms,
        standard_hits=[],
    )
    assert [(o["kind"], o["matched_by"]) for o in out] == [
        ("shared", "column"),
        ("dataset", "column"),
    ]
    assert out[1]["term"] == B_PROP and out[1]["label"] == "diffraction  point"
    # not fuzzy: a substring / near miss is no match
    assert (
        sv.fit_candidates(
            "回折", "x", shared_terms=shared, dataset_terms=ds_terms, standard_hits=[]
        )
        == []
    )
    # generic labels yield nothing, standard hits included
    for generic in ("Name", "ＩＤ", "名前", "値"):
        assert (
            sv.fit_candidates(
                generic,
                "dp",
                shared_terms=[_term("n", generic)],
                dataset_terms=[],
                standard_hits=std,
            )
            == []
        )
    expected = {"name", "id", "type", "date", "value", "no", "名前", "番号", "種類", "日付", "値"}
    assert expected == sv.GENERIC_LABELS


# ---------------------------------------------------------------------------
# default_cq_tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "op", "path"),
    [("class", "count", sv.UPPER_PATH_CLASS), ("property", "values", sv.UPPER_PATH_PROPERTY)],
)
def test_default_cq_tools_pass_lint_and_use_the_one_path_definition(
    kind: str, op: str, path: str
) -> None:
    term = _term("dp", "回折点", kind=kind)
    tools = sv.default_cq_tools(term, [CQSpec("題 1", op), CQSpec("題 2", op)])
    assert [t["name"] for t in tools] == [f"cq_dp_{op}", f"cq_dp_{op}_2"]
    assert tools[0]["title"] == "題 1" and tools[0]["for_terms"] == [term.iri]
    q = tools[0]["query"]
    assert path in q and f"<{term.iri}>" in q and "GROUP BY ?dataset_graph" in q
    base = substrate.CANONICAL_GRAPH_BASE
    assert f'"{base}crosswalk"' in q and f'STRSTARTS(STR(?dataset_graph), "{base}crosswalk/")' in q
    parsed = parse_query_tools({"tools": tools})
    assert len(parsed) == 2
    for qt in parsed:
        assert lint_query_tool(qt).ok, lint_query_tool(qt).errors
    with pytest.raises(TermError):  # the op must match the kind
        sv.default_cq_tools(term, [CQSpec("x", "values" if op == "count" else "count")])


def test_default_cq_runs_against_a_union_default_graph_and_skips_hubs() -> None:
    """The template itself, executed the way the FROM-merge does (alignment graph
    visible in the default graph): hub graphs are not counted as datasets."""
    ds = rdflib.Dataset(default_union=True)
    client = _Client(ds)
    term = _term("dp", "回折点")
    tool = sv.default_cq_tools(term, [CQSpec("q", "count")])[0]
    g = ds.graph(rdflib.URIRef(substrate.versioned_graph_iri("xrd-a", 1)))
    g.add((rdflib.URIRef("urn:a1"), rdflib.RDF.type, rdflib.URIRef(A_REC)))
    hub = ds.graph(rdflib.URIRef(substrate.canonical_graph_iri("crosswalk")))
    hub.add((rdflib.URIRef("urn:h1"), rdflib.RDF.type, rdflib.URIRef(A_REC)))
    ds.graph(rdflib.URIRef(ALIGNMENT_GRAPH)).add(
        (rdflib.URIRef(A_REC), rdflib.RDFS.subClassOf, rdflib.URIRef(term.iri))
    )
    rows = json.loads(ds.query(tool["query"]).serialize(format="json"))["results"]["bindings"]
    assert [r["dataset_graph"]["value"] for r in rows] == [
        substrate.versioned_graph_iri("xrd-a", 1)
    ]
    assert client  # (client unused beyond construction; the query ran on the dataset)


# ---------------------------------------------------------------------------
# mint / list / remove
# ---------------------------------------------------------------------------


async def test_mint_writes_tbox_scaffold_cq_and_wired(tmp_path: Path) -> None:
    client = _Client()
    out = await sv.mint_term(
        client,
        tmp_path,
        slug="diffraction_point",
        kind="class",
        label="回折点",
        label_en="Diffraction point",
        comment="1 本のピーク",
        declined_standard="https://w3id.org/cmso/Foo",
        declined_reason="粒度が違う",
        cqs=[CQSpec("回折点は何件か", "count")],
        at=AT,
    )
    iri = sv.SV + "diffraction_point"
    assert out["iri"] == iri and out["kind"] == "class" and out["created_at"] == AT
    assert out["label_en"] == "Diffraction point" and out["declined_reason"] == "粒度が違う"
    g = client.ds.graph(rdflib.URIRef(sv.SHARED_VOCAB_GRAPH))
    u = rdflib.URIRef(iri)
    assert (u, rdflib.RDF.type, rdflib.RDFS.Class) in g
    assert (u, rdflib.RDFS.label, rdflib.Literal("回折点", lang="ja")) in g
    assert (u, rdflib.RDFS.label, rdflib.Literal("Diffraction point", lang="en")) in g
    assert (
        u,
        rdflib.URIRef(XW + "declinedStandard"),
        rdflib.URIRef("https://w3id.org/cmso/Foo"),
    ) in g
    assert (u, rdflib.URIRef("http://www.w3.org/ns/prov#generatedAtTime"), None) in g
    meta = json.loads((tmp_path / "vocab-shared" / "meta.json").read_text(encoding="utf-8"))
    assert meta == {
        "id": "vocab-shared",
        "is_shared_vocab": True,
        "promoted": True,
        "name": "ことば",
    }
    raw = yaml.safe_load(
        (tmp_path / "vocab-shared" / "query_tools.yaml").read_text(encoding="utf-8")
    )
    assert [t["name"] for t in raw["tools"]] == ["cq_diffraction_point_count"]
    assert raw["tools"][0]["for_terms"] == [iri]
    assert len(parse_query_tools(raw)) == 1
    assert sv.load_wired(tmp_path) == {iri: 0}
    wired = json.loads((tmp_path / "vocab-shared" / "wired.json").read_text(encoding="utf-8"))
    assert wired["terms"] == {iri: 0} and wired["at"]
    assert not (tmp_path / "vocab-shared" / "wired.json.tmp").exists()


async def test_mint_without_cq_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    client = _Client()
    with pytest.raises(TermError):
        await sv.mint_term(client, tmp_path, slug="x_y", kind="class", label="語", cqs=[], at=AT)
    assert len(client.ds.graph(rdflib.URIRef(sv.SHARED_VOCAB_GRAPH))) == 0
    assert not (tmp_path / "vocab-shared").exists()


async def test_mint_same_slug_is_term_exists_and_bad_input_is_value_error(tmp_path: Path) -> None:
    client = _Client()
    await _mint_class(client, tmp_path)
    with pytest.raises(TermExists):
        await _mint_class(client, tmp_path)
    # one term, one CQ still
    tools = yaml.safe_load((tmp_path / "vocab-shared" / "query_tools.yaml").read_text("utf-8"))
    assert len(tools["tools"]) == 1
    cq = [CQSpec("q", "count")]
    for kwargs in (
        {"slug": "Bad Slug", "kind": "class", "label": "語"},
        {"slug": "ok", "kind": "individual", "label": "語"},
        {"slug": "ok", "kind": "class", "label": "  "},
    ):
        with pytest.raises(ValueError):
            await sv.mint_term(client, tmp_path, cqs=cq, at=AT, **kwargs)
    with pytest.raises(ValueError):
        await sv.mint_term(
            client, tmp_path, slug="ok", kind="class", label="語", cqs=cq, at="yesterday"
        )
    with pytest.raises(TermError):  # op does not fit the kind
        await sv.mint_term(client, tmp_path, slug="ok", kind="property", label="語", cqs=cq, at=AT)


async def test_mint_rolls_back_the_cq_when_the_store_insert_fails(tmp_path: Path) -> None:
    class _Failing(_Client):
        fail = True

        async def sparql_update(self, update: str) -> None:
            if self.fail:
                raise RuntimeError("store down")
            await super().sparql_update(update)

    client = _Failing()
    d = tmp_path / "vocab-shared"
    d.mkdir()
    # a hand-written tool already in the file must survive the rollback
    (d / "query_tools.yaml").write_text(
        yaml.safe_dump({"tools": [{"name": "keep_me", "title": "t", "query": "SELECT 1"}]}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="store down"):
        await _mint_class(client, tmp_path)
    raw = yaml.safe_load((d / "query_tools.yaml").read_text(encoding="utf-8"))
    assert [t["name"] for t in raw["tools"]] == ["keep_me"]
    assert len(client.ds.graph(rdflib.URIRef(sv.SHARED_VOCAB_GRAPH))) == 0
    assert not list(d.glob("*.tmp")) and not list(d.glob(".*.tmp"))
    # a retry is a plain mint, not TermExists
    client.fail = False
    out = await _mint_class(client, tmp_path)
    assert out["slug"] == "diffraction_point"
    raw = yaml.safe_load((d / "query_tools.yaml").read_text(encoding="utf-8"))
    assert [t["name"] for t in raw["tools"]] == ["keep_me", "cq_diffraction_point_count"]


def test_regexes_refuse_a_trailing_newline() -> None:
    with pytest.raises(ValueError):
        sv.term_iri("ok\n")
    assert sv.slug_of(sv.SV + "ok\n") is None
    assert not sv._ISO_AT.match("2026-10-09T00:00:00Z\n")
    assert sv._ISO_AT.match("2026-10-09T00:00:00Z")
    assert not sv._IRI.match("https://example.org/x\n")
    assert sv._IRI.match("https://example.org/x")


async def test_mint_aborts_on_an_unreadable_query_tools_file(tmp_path: Path) -> None:
    client = _Client()
    d = tmp_path / "vocab-shared"
    d.mkdir()
    (d / "query_tools.yaml").write_text("tools: [unclosed", encoding="utf-8")
    with pytest.raises(TermError):
        await _mint_class(client, tmp_path)
    assert (d / "query_tools.yaml").read_text(encoding="utf-8") == "tools: [unclosed"
    assert len(client.ds.graph(rdflib.URIRef(sv.SHARED_VOCAB_GRAPH))) == 0


async def test_remove_refused_while_a_line_exists_then_cascades_cqs(tmp_path: Path) -> None:
    client = _Client()
    await _seed_two_datasets(client)
    await _mint_class(client, tmp_path, "alpha")
    await _mint_class(client, tmp_path, "beta")
    a, b = sv.term_iri("alpha"), sv.term_iri("beta")
    # a CQ over both terms (what 「ことばへ写す」 will produce), appended by name
    multi = {**sv.default_cq_tools(_term("alpha", "x"), [CQSpec("両方", "count")])[0]}
    multi.update(name="cq_both", for_terms=[a, b])
    sv._write_cq_tools(tmp_path, [multi])

    await assert_alignment(client, A_REC, a, "subClassOf", at=AT)
    with pytest.raises(TermHasLines):
        await sv.remove_term(client, tmp_path, "alpha")
    await remove_alignment(client, A_REC, a, "subClassOf")

    await sv.remove_term(client, tmp_path, "alpha")
    g = client.ds.graph(rdflib.URIRef(sv.SHARED_VOCAB_GRAPH))
    assert (rdflib.URIRef(a), None, None) not in g and (rdflib.URIRef(b), None, None) in g
    tools = {
        t["name"]: t
        for t in yaml.safe_load(
            (tmp_path / "vocab-shared" / "query_tools.yaml").read_text("utf-8")
        )["tools"]
    }
    assert set(tools) == {"cq_beta_count", "cq_both"}  # alpha's own CQ is gone
    assert tools["cq_both"]["for_terms"] == [b]  # the shared CQ just loses alpha
    assert set(sv.load_wired(tmp_path)) == {b}
    with pytest.raises(TermNotFound):
        await sv.remove_term(client, tmp_path, "alpha")


async def test_list_terms_reports_narrower_standards_cqs_and_wired(tmp_path: Path) -> None:
    client = _Client()
    await _seed_two_datasets(client)
    _ontology(client, "xrd-a", [(A_REC, RDFS + "Class", "記録 A")])
    _ontology(client, "xrd-b", [(B_REC, RDFS + "Class", "記録 B")])
    await _mint_class(client, tmp_path)
    iri = sv.term_iri("diffraction_point")
    std = sv.known_namespaces()[0] + "Thing"
    await assert_alignment(client, A_REC, iri, "subClassOf", at=AT)
    await assert_alignment(client, iri, B_REC, "equivalentClass", at=AT)  # sv -> dataset
    await assert_alignment(client, iri, std, "equivalentClass", at=AT)
    await sv.recompute_wired(client, tmp_path)

    (term,) = await sv.list_terms(client, tmp_path)
    assert term["slug"] == "diffraction_point" and term["wired"] is True
    assert term["answering_datasets"] == 2
    assert term["narrower"] == [
        {"iri": A_REC, "kind": "dataset", "dataset_id": "xrd-a", "label": "記録 A"},
        {"iri": B_REC, "kind": "dataset", "dataset_id": "xrd-b", "label": "記録 B"},
    ]
    assert term["standards"] == [{"iri": std, "relation": "equivalentClass"}]
    assert term["cqs"] == [
        {
            "tool_name": "cq_diffraction_point_count",
            "title": "回折点は、どのデータセットに何件あるか",
            "answering_datasets": 2,
        }
    ]
    assert await sv.list_terms(_Client(), tmp_path) == []


# ---------------------------------------------------------------------------
# answering_datasets / wired
# ---------------------------------------------------------------------------


async def test_answering_datasets_goes_0_1_2_with_mixed_lines(tmp_path: Path) -> None:
    client = _Client()
    await _seed_two_datasets(client)
    await _mint_class(client, tmp_path)
    iri = sv.term_iri("diffraction_point")
    assert (await sv.recompute_wired(client, tmp_path)) == {iri: 0}
    assert not sv.is_wired(sv.load_wired(tmp_path)[iri])

    await assert_alignment(client, A_REC, iri, "subClassOf", at=AT)  # one ⊂ line
    assert await sv.answering_datasets(client, [iri]) == {iri: 1}
    assert (await sv.recompute_wired(client, tmp_path)) == {iri: 1}
    assert sv.is_wired(sv.load_wired(tmp_path)[iri])

    await assert_alignment(client, iri, B_REC, "equivalentClass", at=AT)  # one ≡ line, reversed
    assert await sv.answering_datasets(client, [iri]) == {iri: 2}

    await remove_alignment(client, A_REC, iri, "subClassOf")  # reversible
    assert await sv.answering_datasets(client, [iri]) == {iri: 1}


async def test_answering_datasets_is_one_aggregate_query_for_all_terms_and_counts_properties() -> (
    None
):
    client = _Client()
    await _seed_two_datasets(client)
    c, p, lonely = sv.term_iri("cls"), sv.term_iri("prop"), sv.term_iri("lonely")
    _declare(client, c, lonely)
    _declare(client, p, kind="property")
    await assert_alignment(client, A_REC, c, "subClassOf", at=AT)
    await assert_alignment(client, A_PROP, p, "subPropertyOf", at=AT)
    await assert_alignment(client, p, B_PROP, "equivalentProperty", at=AT)
    before = len(client.selects)
    out = await sv.answering_datasets(client, [c, p, lonely])
    assert out == {c: 1, p: 2, lonely: 0}
    # canonical_graphs + exactly one aggregate query
    assert len(client.selects) - before == 2


async def test_direct_use_of_a_shared_term_counts_with_no_line() -> None:
    client = _Client()
    iri = sv.term_iri("direct")
    await _publish(client, "xrd-a", [_typed("urn:a1", iri)])
    await _publish(client, "xrd-b", [("urn:b1", sv.term_iri("direct_prop"), "3")])
    assert await sv.answering_datasets(client, [iri, sv.term_iri("direct_prop")]) == {
        iri: 1,
        sv.term_iri("direct_prop"): 1,
    }


async def test_answering_datasets_ignores_both_hub_forms_and_never_double_counts_versions() -> None:
    client = _Client()
    iri = sv.term_iri("diffraction_point")
    _declare(client, iri)
    await assert_alignment(client, A_REC, iri, "subClassOf", at=AT)
    # A hub graph holding an entity of the aligned class (new form AND the legacy
    # unslashed form, whose "id" `crosswalk` is a valid dataset id) is not a dataset.
    for hub in ("crosswalk", "crosswalk/composition"):
        await _publish(client, hub, [_typed("urn:hub", A_REC)], version=None)
    assert await sv.answering_datasets(client, [iri]) == {iri: 0}

    # one dataset flagged under both its key graph and a versioned graph -> counts once
    await _publish(client, "xrd-a", [_typed("urn:a1", A_REC)], version=None)
    await _publish(client, "xrd-a/v2", [_typed("urn:a2", A_REC)], version=None)
    assert await sv.answering_datasets(client, [iri]) == {iri: 1}


async def test_no_published_graph_sends_no_aggregate_and_counts_zero(tmp_path: Path) -> None:
    client = _Client()
    iri = sv.term_iri("diffraction_point")
    _declare(client, iri)
    # a promoted alignment graph alone is not a dataset
    await assert_alignment(client, A_REC, iri, "subClassOf", at=AT)
    client.selects.clear()
    assert await sv.answering_datasets(client, [iri]) == {iri: 0}
    assert len(client.selects) == 1 and "VALUES ?t" not in client.selects[0]
    assert await sv.answering_datasets(client, []) == {}
    with pytest.raises(ValueError):  # an IRI is injected into the query: refuse junk
        await sv.answering_datasets(client, ["x> } ; DROP"])


def test_load_wired_tolerates_missing_broken_and_flat_files(tmp_path: Path) -> None:
    assert sv.load_wired(tmp_path) == {}
    d = tmp_path / "vocab-shared"
    d.mkdir()
    (d / "wired.json").write_text("{not json", encoding="utf-8")
    assert sv.load_wired(tmp_path) == {}
    (d / "wired.json").write_text(json.dumps({"https://x/a": 2, "at": "t"}), encoding="utf-8")
    assert sv.load_wired(tmp_path) == {"https://x/a": 2}


# ---------------------------------------------------------------------------
# upper_map
# ---------------------------------------------------------------------------


async def test_upper_map_collapses_to_the_topmost_shared_term() -> None:
    client = _Client()
    s = sv.term_iri
    c1, c2, c3, c4, c5, c6 = (NS_A + f"C{i}" for i in range(1, 7))
    std = "https://w3id.org/cmso/Material"
    _declare(
        client,
        *(s(n) for n in ("mid", "top", "eq", "b1", "b2", "zed", "alpha", "loop")),
    )
    _declare(client, s("pp"), kind="property")
    for src, rel, tgt in [
        (c1, "subClassOf", s("mid")),  # C1 ⊂ mid ⊂ top
        (s("mid"), "subClassOf", s("top")),
        (c2, "equivalentClass", s("eq")),  # C2 ≡ eq ⊂ top, mixed ⊂/≡
        (s("eq"), "subClassOf", s("top")),
        (s("b2"), "equivalentClass", s("b1")),  # b1 ≡ b2: neither is above the other
        (c3, "subClassOf", s("b2")),
        (c4, "subClassOf", s("zed")),  # two tops -> first by slug
        (c4, "subClassOf", s("alpha")),
        (c5, "subClassOf", std),  # nothing shared above -> itself
        (s("loop"), "equivalentClass", c6),  # ≡ written sv -> dataset
    ]:
        await assert_alignment(client, src, tgt, rel, at=AT)
    await assert_alignment(client, NS_A + "p1", s("pp"), "subPropertyOf", at=AT)
    await assert_alignment(client, B_PROP, NS_A + "p1", "equivalentProperty", at=AT)
    await assert_alignment(
        client, B_PROP, "http://qudt.org/schema/qudt/Q", "hasQuantityKind", at=AT
    )

    out = await sv.upper_map(client)
    cl = out["classes"]
    assert cl[c1] == cl[s("mid")] == cl[s("top")] == cl[c2] == cl[s("eq")] == s("top")
    assert cl[c3] == cl[s("b1")] == cl[s("b2")] == s("b1")
    assert cl[c4] == s("alpha") and cl[s("alpha")] == s("alpha") and cl[s("zed")] == s("zed")
    assert cl[c5] == c5 and cl[std] == std
    assert cl[c6] == s("loop") and cl[s("loop")] == s("loop")
    assert NS_A + "p1" not in cl and B_PROP not in cl  # property lines are not class lines
    pr = out["properties"]
    assert pr[NS_A + "p1"] == pr[B_PROP] == pr[s("pp")] == s("pp")
    assert "http://qudt.org/schema/qudt/Q" not in pr  # hasQuantityKind is no upper line
    assert await sv.upper_map(_Client()) == {"classes": {}, "properties": {}}


# ---------------------------------------------------------------------------
# substrate: allowlist yes, canonical set no
# ---------------------------------------------------------------------------


async def test_shared_graph_is_allowlisted_but_never_canonical(tmp_path: Path) -> None:
    client = _Client()
    await _seed_two_datasets(client)
    await _mint_class(client, tmp_path)
    assert sv.SHARED_VOCAB_GRAPH in await substrate.readable_graph_iris(client)
    assert sv.SHARED_VOCAB_GRAPH not in await substrate.canonical_graphs(client)
    assert sv.SHARED_VOCAB_GRAPH not in await substrate.ontology_graphs(client)

    q = f"SELECT ?s WHERE {{ ?s a <{RDFS}Class> }}"
    # a caller may name it explicitly ...
    named = f"SELECT ?s FROM <{sv.SHARED_VOCAB_GRAPH}> WHERE {{ ?s a <{RDFS}Class> }}"
    assert await substrate.canonical_merge_query(client, named) == named
    # ... but the default FROM-merge does not include it, and a draft is still refused
    merged = await substrate.canonical_merge_query(client, q)
    assert sv.SHARED_VOCAB_GRAPH not in merged and substrate.canonical_graph_iri("xrd-a") in merged
    with pytest.raises(ValueError):
        await substrate.canonical_merge_query(
            client, "SELECT ?s FROM <https://example.org/draft> WHERE { ?s ?p ?o }"
        )
