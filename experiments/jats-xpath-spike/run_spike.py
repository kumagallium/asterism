"""De-risk spike: JATS (XML) -> doco/nif/fabio through the Asterism substrate.

Confirms the document-ontology layer (handoff_to_claude_code_document_ontology.md)
can be built on the existing declarative substrate: morph-kgc reads XML via
`ql:XPath`, and the `po:contains` structure tree (paper -> section -> paragraph)
plus verbatim text (`nif:isString`) come out with no engine change.

Run from the repo root with the ingest venv (which has the morph-kgc extra):

    PYTHONPATH=ingest/src ingest/.venv/bin/python experiments/jats-xpath-spike/run_spike.py

NOTE: the substrate's safety allowlist (`asterism.rml_safety._ALLOWED_SOURCE_SUFFIXES`
= {.csv,.tsv,.json}) blocks `.xml` today. Production must ADD `.xml` there as a
deliberate one-line safety decision (the format is vetted; morph-kgc reads it
declaratively). This spike widens the allowlist at runtime ONLY to demonstrate
the materialize works once that decision is made.
"""
from __future__ import annotations

from pathlib import Path

import asterism.rml_safety as rs

# Spike-only: pretend the production decision to allow .xml has been made.
rs._ALLOWED_SOURCE_SUFFIXES = frozenset(rs._ALLOWED_SOURCE_SUFFIXES | {".xml"})

from asterism.substrate import materialize_to_graph  # noqa: E402  (after allowlist patch)

HERE = Path(__file__).resolve().parent


def main() -> int:
    graph = materialize_to_graph((HERE / "paper.rml.ttl").read_text(encoding="utf-8"), HERE)
    triples = {(str(s), str(p), str(o)) for s, p, o in graph}
    print(f"triples: {len(triples)}")
    for s, p, o in sorted(triples):
        print(" ", s, p.rsplit("/", 1)[-1].split("#")[-1], repr(o)[:48])

    PO = "http://www.essepuntato.it/2008/12/pattern#contains"
    NIF = "http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#isString"
    contains = {(s, o) for s, p, o in triples if p == PO}
    verbatim = {o for s, p, o in triples if p == NIF}
    # paper -> 2 sections, section s3-2 -> 2 paragraphs, s4 -> 1 paragraph = 5 contains.
    assert len(contains) == 5, contains
    assert "Samples were measured at 300 K under Ar." in verbatim
    assert any(s.endswith("/sec/s3-2") and o.endswith("/para/p4") for s, o in contains)
    print("\nOK: po:contains tree + nif:isString verbatim materialized declaratively.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
