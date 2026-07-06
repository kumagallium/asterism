"""Gated end-to-end: compiled mappings run under REAL Morph-KGC.

Skipped unless morph-kgc + the ingest package are importable (like the other
morph-gated suites). Pins the two runtime behaviors the compiler design leans
on (probed in the ADR, ``mapping-ir-compiler.md``):

1. ``rr:template`` placeholders are R2RML-percent-encoded by the engine, so raw
   data columns in IRI templates are load-safe without any wrapping — if a
   future Morph-KGC drops this, THIS test fails, not a production ingest.
2. Nested ``rmlf:functionExecution`` (fn:template + fn:slug) works for
   readable-segment transforms.

Also proves parity: the compiled Materials Project IR materializes the same
facts as the handwritten ``datasets/materials_project/json/mp.rml.ttl``
(modulo the two bare-reference IRIs the compiler deliberately refuses — they
go through ``fn:iri_safe``, identity for clean URLs).
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml")
rdflib = pytest.importorskip("rdflib")
pytest.importorskip("asterism.functions")
pytest.importorskip("morph_kgc")

from asterism import substrate  # noqa: E402

from asterism_step0.mapping_ir import parse_mapping_ir  # noqa: E402
from asterism_step0.rml_compile import compile_mapping_ir  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MP_DIR = REPO_ROOT / "datasets" / "materials_project" / "json"


def test_compiled_mapping_materializes_with_encoding_and_transform(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text(
        "id,name,journal,tags\n"
        'A1,Bi2 "Te3"<x>,Applied Physics Letters,"a,b"\n',
        encoding="utf-8",
    )
    ir = parse_mapping_ir(
        """
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
      - predicate: ex:comp
        object_template: "exr:comp/{name}"
      - predicate: ex:journal
        object_template: "exr:periodical/{journal}"
        transform: { journal: slug }
      - predicate: ex:tag
        column: tags
        function: split
        args: { delimiter: "," }
"""
    )
    g = substrate.materialize_to_graph(compile_mapping_ir(ir), tmp_path)

    ex = "https://example.org/ns#"
    exr = "https://example.org/r/"
    objs = {str(o) for o in g.objects(None, rdflib.URIRef(ex + "comp"))}
    # engine percent-encoding of raw template placeholders (regression pin #1)
    assert objs == {exr + "comp/Bi2%20%22Te3%22%3Cx%3E"}
    # nested fn:template + fn:slug transform (regression pin #2)
    journals = {str(o) for o in g.objects(None, rdflib.URIRef(ex + "journal"))}
    assert journals == {exr + "periodical/applied-physics-letters"}
    # multi-value explode
    tags = {str(o) for o in g.objects(None, rdflib.URIRef(ex + "tag"))}
    assert tags == {"a", "b"}
    # the whole output round-trips strict N-Triples (Oxigraph-grade validity)
    rt = rdflib.Graph()
    rt.parse(data=g.serialize(format="nt"), format="nt")


MP_IR = """
version: 1
prefixes:
  mp: "https://kumagallium.github.io/asterism/materials_project/ontology#"
  mpr: "https://kumagallium.github.io/asterism/materials_project/resource/"
  prov: "http://www.w3.org/ns/prov#"
  schema: "https://schema.org/"
maps:
  - name: material
    source: mp.csv
    subject:
      template: "mpr:material/{mp_id}"
      classes: [prov:Entity, mp:Material]
    properties:
      - predicate: mp:mpId
        column: mp_id
      - predicate: mp:formula
        column: formula
      - predicate: schema:url
        column: mp_page
        function: iri_safe
        object_type: iri
      - predicate: mp:hasCrystalStructure
        object_template: "mpr:structure/{mp_id}"
  - name: crystal_structure
    source: mp.csv
    subject:
      template: "mpr:structure/{mp_id}"
      classes: [prov:Entity, mp:CrystalStructure]
    properties:
      - predicate: mp:spaceGroupSymbol
        column: structure.space_group_symbol
      - predicate: mp:spaceGroupNumber
        column: structure.space_group_number
        datatype: xsd:integer
      - predicate: mp:crystalSystem
        column: structure.crystal_system
      - predicate: mp:idealizedFrom
        column: mp_page
        function: iri_safe
        object_type: iri
      - predicate: mp:ofMaterial
        object_template: "mpr:material/{mp_id}"
"""


@pytest.mark.skipif(not MP_DIR.exists(), reason="materials_project dataset content absent")
def test_compiled_mp_parity_with_handwritten_mapping(tmp_path: Path) -> None:
    """The IR transcription of the Materials Project mapping materializes the
    SAME triple set as the handwritten reference over the real mp.json (the
    substrate tabularizes it to mp.csv on the fly for both runs)."""
    import shutil

    for run in ("ref", "ir"):
        (tmp_path / run).mkdir()
        shutil.copy(MP_DIR / "mp.json", tmp_path / run / "mp.json")

    reference_ttl = (MP_DIR / "mp.rml.ttl").read_text(encoding="utf-8")
    g_ref = substrate.materialize_to_graph(reference_ttl, tmp_path / "ref")

    compiled_ttl = compile_mapping_ir(parse_mapping_ir(MP_IR))
    g_ir = substrate.materialize_to_graph(compiled_ttl, tmp_path / "ir")

    assert set(g_ref) == set(g_ir)
