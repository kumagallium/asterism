"""Compiler tests: golden graph-isomorphism against handwritten representative
RML, per-shape emission units, and fail-closed error behavior.

Golden philosophy (ADR §10): the compiled MAPPING graph must be isomorphic
(rdflib.compare — blank-node-safe) with the existing handwritten reference; we
never require byte equality.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml")
rdflib = pytest.importorskip("rdflib")
pytest.importorskip("asterism.functions")

from rdflib.compare import graph_diff, isomorphic  # noqa: E402

from asterism_step0.mapping_ir import parse_mapping_ir  # noqa: E402
from asterism_step0.rml_compile import (  # noqa: E402
    RmlCompileError,
    compile_mapping_ir,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mapping_ir"
REPO_ROOT = Path(__file__).resolve().parents[2]
E2E_REFERENCE = REPO_ROOT / "experiments" / "phase5-morph-kgc-spike" / "e2e" / "mappings.rml.ttl"

_BASE = "http://example.org/mapping"


def compile_text(ir_yaml: str) -> str:
    return compile_mapping_ir(parse_mapping_ir(ir_yaml))


def as_graph(ttl: str | Path) -> rdflib.Graph:
    g = rdflib.Graph()
    if isinstance(ttl, Path):
        g.parse(ttl, format="turtle", publicID=_BASE)
    else:
        g.parse(data=ttl, format="turtle", publicID=_BASE)
    return g


def assert_isomorphic(compiled_ttl: str, reference: str | Path) -> None:
    compiled, ref = as_graph(compiled_ttl), as_graph(reference)
    if not isomorphic(compiled, ref):
        _, only_compiled, only_ref = graph_diff(compiled, ref)
        msg = ["compiled mapping is not isomorphic with the reference:"]
        msg += [f"  only in compiled: {t}" for t in sorted(only_compiled)[:10]]
        msg += [f"  only in reference: {t}" for t in sorted(only_ref)[:10]]
        pytest.fail("\n".join(msg))


# ---------------------------------------------------------------------------
# Goldens
# ---------------------------------------------------------------------------


def test_golden_e2e_isomorphic() -> None:
    """The IR transcription of the e2e spike mapping compiles to the exact same
    mapping graph as the handwritten reference (CSV sources, direct refs,
    1- and 2-input functions, datatypes, template IRI links, classes)."""
    ir_yaml = (FIXTURES / "e2e.yaml").read_text(encoding="utf-8")
    assert_isomorphic(compile_text(ir_yaml), E2E_REFERENCE)


XML_IR = """
version: 1
prefixes:
  doco: "http://purl.org/spar/doco/"
  po: "http://www.essepuntato.it/2008/12/pattern#"
  dcterms: "http://purl.org/dc/terms/"
  lit: "https://example.org/papers/ontology#"
maps:
  - name: paper
    source: doc.xml
    iterator: "/article"
    subject:
      constant: "https://example.org/papers/resource/paper/P1"
      classes: [lit:Paper]
    properties:
      - predicate: dcterms:identifier
        constant: "10.1234/x"
      - predicate: po:contains
        object_template: "https://example.org/papers/resource/paper/P1/sec/{body/sec/@id}"
  - name: section
    source: doc.xml
    iterator: "/article/body//sec"
    subject:
      template: "https://example.org/papers/resource/paper/P1/sec/{@id}"
      classes: [doco:Section]
    properties:
      - predicate: dcterms:title
        column: title
      - predicate: lit:structuralPath
        column: title
        function: structural_slug
"""


def test_golden_xml_isomorphic() -> None:
    """XML/XPath shape (document-ontology patterns): iterator, constant subject,
    attribute references, multi-valued child link, function objectMap."""
    assert_isomorphic(compile_text(XML_IR), FIXTURES / "xml_reference.rml.ttl")


# ---------------------------------------------------------------------------
# Shape units (assert on the parsed graph, not on bytes)
# ---------------------------------------------------------------------------

RR = rdflib.Namespace("http://www.w3.org/ns/r2rml#")
RMLF = rdflib.Namespace("http://w3id.org/rml/")
FN = rdflib.Namespace("https://kumagallium.github.io/asterism/fn/")

BASE_IR = """
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: thing
    source: data.csv
    subject:
      template: "exr:thing/{id}"
      classes: [ex:Thing]
    properties:
      - predicate: ex:name
        column: name
__EXTRA__
"""


def compile_with(extra_props: str) -> rdflib.Graph:
    return as_graph(compile_text(BASE_IR.replace("__EXTRA__", extra_props)))


def test_constant_args_use_rmlf_constant() -> None:
    g = compile_with(
        "      - predicate: ex:flag\n"
        "        column: raw_flag\n"
        "        function: lookup\n"
        "        args: { table: bool }\n"
    )
    # the constant is passed via rmlf:constant against fn:p_table
    consts = {
        (str(p_iri), str(c))
        for inp in g.objects(None, RMLF.input)
        for p_iri in g.objects(inp, RMLF.parameter)
        for vm in g.objects(inp, RMLF.inputValueMap)
        for c in g.objects(vm, RMLF.constant)
    }
    assert (str(FN.p_table), "bool") in consts


def test_multivalue_split_emits_delimiter_constant() -> None:
    g = compile_with(
        "      - predicate: ex:tag\n"
        "        column: tags\n"
        "        function: split\n"
        '        args: { delimiter: "," }\n'
    )
    assert (None, RMLF.function, FN["split"]) in g


def test_transform_emits_nested_template_execution() -> None:
    ir_yaml = """
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: periodical
    source: data.csv
    subject:
      template: "exr:periodical/{journal}"
      transform: { journal: slug }
      classes: [ex:Periodical]
    properties:
      - predicate: ex:name
        column: journal
"""
    g = as_graph(compile_text(ir_yaml))
    # subjectMap carries a fn:template execution whose field1 input nests a
    # fn:slug execution over the column (the probed Morph-KGC nesting shape).
    subj_maps = list(g.objects(None, RR.subjectMap))
    assert len(subj_maps) == 1
    fe = next(g.objects(subj_maps[0], RMLF.functionExecution))
    assert next(g.objects(fe, RMLF.function)) == FN.template
    nested_fns = {
        str(f)
        for inp in g.objects(fe, RMLF.input)
        for vm in g.objects(inp, RMLF.inputValueMap)
        for inner in g.objects(vm, RMLF.functionExecution)
        for f in g.objects(inner, RMLF.function)
    }
    assert str(FN.slug) in nested_fns
    # the constant template got positional tokens and an expanded prefix
    consts = {
        str(c)
        for inp in g.objects(fe, RMLF.input)
        for vm in g.objects(inp, RMLF.inputValueMap)
        for c in g.objects(vm, RMLF.constant)
    }
    assert "https://example.org/r/periodical/{1}" in consts
    assert (subj_maps[0], RR.termType, RR.IRI) in g


def test_literal_template_and_language_and_typed_constant() -> None:
    g = compile_with(
        "      - predicate: ex:ident\n"
        '        object_template: "{id}-{name}"\n'
        "        object_type: literal\n"
        "      - predicate: ex:label\n"
        "        column: name\n"
        "        language: ja\n"
        "      - predicate: ex:since\n"
        '        constant: "2026-01-01"\n'
        "        datatype: xsd:date\n"
    )
    XSD = rdflib.XSD
    om_types = {
        (str(t), str(tmpl))
        for om in g.objects(None, RR.objectMap)
        for t in g.objects(om, RR.termType)
        for tmpl in g.objects(om, RR.template)
    }
    assert (str(RR.Literal), "{id}-{name}") in om_types
    langs = {str(literal_lang) for om in g.objects(None, RR.objectMap)
             for literal_lang in g.objects(om, RR.language)}
    assert "ja" in langs
    typed = [
        o for om in g.objects(None, RR.objectMap) for o in g.objects(om, RR.constant)
        if isinstance(o, rdflib.Literal) and o.datatype == XSD.date
    ]
    assert typed and str(typed[0]) == "2026-01-01"


def test_fallback_comment_survives_in_text() -> None:
    ttl = compile_text(
        BASE_IR.replace(
            "__EXTRA__",
            "      - predicate: ex:authorsRaw\n"
            "        column: author\n"
            "        fallback: true\n",
        )
    )
    assert "# fallback: author not expanded" in ttl


def test_turtle_escaping_of_quotes_and_backslashes() -> None:
    g = compile_with(
        "      - predicate: ex:pat\n"
        "        column: name\n"
        "        function: regex_extract\n"
        "        args: { pattern: 'a\"b\\d+' }\n"
    )
    consts = {
        str(c)
        for vm in g.objects(None, RMLF.inputValueMap)
        for c in g.objects(vm, RMLF.constant)
    }
    # YAML single-quote keeps the backslash; the compiler must escape it into
    # valid Turtle and the round-trip must restore the original pattern.
    assert 'a"b\\d+' in consts


def test_curie_expansion_inside_templates() -> None:
    ttl = compile_text(BASE_IR.replace("__EXTRA__", ""))
    # the sdr-style prefix head must be a full IRI inside the template string —
    # RML engines do not expand prefixes in rr:template literals.
    assert 'rr:template "https://example.org/r/thing/{id}"' in ttl
    assert 'rr:template "exr:' not in ttl


# ---------------------------------------------------------------------------
# Fail-closed errors
# ---------------------------------------------------------------------------


def test_unknown_function_fails_compile() -> None:
    from asterism_step0.mapping_ir import MappingIR, PropertyIR, SubjectIR, TriplesMapIR

    ir = MappingIR(
        prefixes={"ex": "https://example.org/ns#", "exr": "https://example.org/r/"},
        maps=(
            TriplesMapIR(
                name="thing",
                source="data.csv",
                subject=SubjectIR(template="exr:thing/{id}"),
                properties=(
                    PropertyIR(predicate="ex:x", column="c", function="made_up"),
                ),
            ),
        ),
    )
    with pytest.raises(RmlCompileError) as exc:
        compile_mapping_ir(ir)
    assert any("made_up" in i for i in exc.value.issues)


def test_compile_type_cast_pseudo_function_gets_drop_guidance() -> None:
    """The compiler is the layer that runs in the plain materialize path (no
    source dir), so ITS unknown-function message must carry the fix too."""
    ir_yaml = BASE_IR.replace(
        "__EXTRA__",
        "      - predicate: ex:issn\n"
        "        column: name\n"
        "        function: str\n",
    )
    with pytest.raises(RmlCompileError) as exc:
        compile_text(ir_yaml)
    text = "\n".join(exc.value.issues)
    assert "is a type, not a Tier-0 function" in text
    assert "DROP the 'function:' line" in text


def test_compile_unknown_function_message_includes_menu() -> None:
    ir_yaml = BASE_IR.replace(
        "__EXTRA__",
        "      - predicate: ex:x\n"
        "        column: name\n"
        "        function: normalize_stuff\n",
    )
    with pytest.raises(RmlCompileError) as exc:
        compile_text(ir_yaml)
    text = "\n".join(exc.value.issues)
    assert "choose one of:" in text and "date_iso" in text


def test_transformed_template_max_four_placeholders() -> None:
    ir_yaml = """
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: thing
    source: data.csv
    subject:
      template: "exr:t/{a}-{b}-{c}-{d}-{e}"
      transform: { a: slug }
      classes: [ex:Thing]
    properties:
      - predicate: ex:name
        column: name
"""
    with pytest.raises(RmlCompileError) as exc:
        compile_text(ir_yaml)
    assert any("at most 4 placeholders" in i for i in exc.value.issues)


def test_compile_collects_all_issues() -> None:
    from asterism_step0.mapping_ir import MappingIR, PropertyIR, SubjectIR, TriplesMapIR

    ir = MappingIR(
        prefixes={"exr": "https://example.org/r/"},
        maps=(
            TriplesMapIR(
                name="thing",
                source="data.csv",
                subject=SubjectIR(template="exr:thing/{id}"),
                properties=(
                    PropertyIR(predicate="ex:x", column="c", function="made_up"),
                    PropertyIR(predicate="ex:y", column="d", function="also_fake"),
                ),
            ),
        ),
    )
    with pytest.raises(RmlCompileError) as exc:
        compile_mapping_ir(ir)
    text = "\n".join(exc.value.issues)
    assert "made_up" in text and "also_fake" in text and "ex:x" in text


def test_map_node_names_are_deterministic() -> None:
    from asterism_step0.rml_compile import _map_node_name

    assert _map_node_name("paper") == "PaperMap"
    assert _map_node_name("crystal_structure") == "CrystalStructureMap"
    assert _map_node_name("PaperMap") == "PaperMap"


# ---------------------------------------------------------------------------
# Source-dialect annotations (ADR source-dialect.md)
# ---------------------------------------------------------------------------

AST = rdflib.Namespace("https://kumagallium.github.io/asterism/vocab#")
RML = rdflib.Namespace("http://semweb.mmlab.be/ns/rml#")

DIALECT_IR = """
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
dialects:
  "xrd.txt":
    encoding: cp932
    delimiter: "\\t"
    skip_rows: 1
maps:
  - name: point
    source: xrd.txt
    subject:
      template: "exr:point/{angle}"
      classes: [ex:Point]
    properties:
      - predicate: ex:intensity
        column: intensity
"""


def test_dialect_annotations_on_logical_source() -> None:
    ttl = compile_text(DIALECT_IR)
    g = as_graph(ttl)
    (ls,) = list(g.objects(None, RML.logicalSource))
    assert next(g.objects(ls, AST.sourceEncoding)).toPython() == "cp932"
    assert next(g.objects(ls, AST.sourceDelimiter)).toPython() == "\t"
    assert next(g.objects(ls, AST.sourceSkipRows)).toPython() == 1
    assert list(g.objects(ls, AST.sourceCollapse)) == []  # default not emitted
    # the ast: prefix is declared exactly because an annotation was emitted
    assert "@prefix ast: <https://kumagallium.github.io/asterism/vocab#> ." in ttl


def test_dialect_preamble_annotation_emitted() -> None:
    """A non-default preamble mode compiles to ast:sourcePreamble on the logical
    source (drop is never emitted — the header-metadata opt-in travels design →
    artifact → ingest)."""
    ir = DIALECT_IR.replace("skip_rows: 1", "skip_rows: 1\n    preamble: keyvalue")
    g = as_graph(compile_text(ir))
    (ls,) = list(g.objects(None, RML.logicalSource))
    assert next(g.objects(ls, AST.sourcePreamble)).toPython() == "keyvalue"


def test_dialect_default_preamble_not_emitted() -> None:
    g = as_graph(compile_text(DIALECT_IR))  # DIALECT_IR has no preamble ⇒ drop
    (ls,) = list(g.objects(None, RML.logicalSource))
    assert list(g.objects(ls, AST.sourcePreamble)) == []


def test_dialect_whitespace_collapse_annotations() -> None:
    ir = DIALECT_IR.replace(
        'encoding: cp932\n    delimiter: "\\t"\n    skip_rows: 1',
        "delimiter: whitespace\n    collapse: true",
    )
    g = as_graph(compile_text(ir))
    (ls,) = list(g.objects(None, RML.logicalSource))
    assert next(g.objects(ls, AST.sourceDelimiter)).toPython() == "whitespace"
    assert next(g.objects(ls, AST.sourceCollapse)).toPython() is True
    assert list(g.objects(ls, AST.sourceEncoding)) == []


def test_default_dialect_entry_compiles_byte_identically() -> None:
    """The is_default gate: an all-default dialects entry emits NOTHING — the
    output is byte-identical to a spec without the section."""
    plain = BASE_IR.replace("__EXTRA__", "")
    with_default = plain + 'dialects:\n  "data.csv": {}\n'
    assert compile_text(with_default) == compile_text(plain)
    assert "ast:" not in compile_text(plain)


def test_ast_prefix_conflict_fails_closed() -> None:
    bad = DIALECT_IR.replace(
        'ex: "https://example.org/ns#"',
        'ex: "https://example.org/ns#"\n  ast: "https://example.org/other#"',
    )
    with pytest.raises(RmlCompileError) as exc:
        compile_text(bad)
    assert any("reserved for the source-dialect" in i for i in exc.value.issues)
