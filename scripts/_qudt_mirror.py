"""Shared plumbing for the QUDT mirrors (`build_qudt_units.py`, `build_qudt_quantitykinds.py`).

WHY THESE ARE MIRRORS, NOT CURATIONS. `known_vocabs.yaml` is deliberately hand-curated
("CURATION, not mirroring") because picking WHICH class/property a dataset should reuse
is a design judgement, and a catalog full of near-synonyms makes that judgement worse.
Units and quantity kinds are not like that: `V/K` has exactly one answer, and so does
"what quantity is this column" for temperature. A missing entry is a silent hole, not a
kindness. So these two mirror their vocabularies whole, in their OWN files, and stay out
of the term catalog.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import pyoxigraph as ox

QUDT = "http://qudt.org/schema/qudt/"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
UNIT_NS = "http://qudt.org/vocab/unit/"
QK_NS = "http://qudt.org/vocab/quantitykind/"
SOU_SI = "http://qudt.org/vocab/sou/SI"


def load_turtle(ttl: Path) -> ox.Store:
    """Parse the Turtle with whichever pyoxigraph is installed. The substrate extra
    pins <0.4 (morph-kgc), a plain install gets the current one, and the two spell
    loading differently — these scripts have to run under either."""
    store = ox.Store()
    try:  # pyoxigraph >= 0.4
        store.bulk_load(path=str(ttl), format=ox.RdfFormat.TURTLE)
    except (AttributeError, TypeError):  # pyoxigraph 0.3.x
        store.load(ttl.read_bytes(), mime_type="text/turtle")
    return store


def download(url: str, dest: Path) -> Path:
    urllib.request.urlretrieve(url, dest)  # fixed https host
    return dest


def local(iri: str, ns: str) -> str | None:
    return iri[len(ns) :] if iri.startswith(ns) else None


def objects(store: ox.Store, subj, pred: str) -> list:
    return [q.object for q in store.quads_for_pattern(subj, ox.NamedNode(pred), None)]


def subjects_of_type(store: ox.Store, cls: str) -> list:
    return [
        q.subject
        for q in store.quads_for_pattern(None, ox.NamedNode(RDF_TYPE), ox.NamedNode(cls))
    ]


def en_label(store: ox.Store, subj) -> str:
    """The English label. QUDT carries several languages; keep `en` (or the tagless one)
    so the catalog reads the same everywhere and never depends on parse order."""
    best = ""
    for o in objects(store, subj, RDFS + "label"):
        if not isinstance(o, ox.Literal):
            continue
        lang = (o.language or "").lower()
        if lang in ("en", ""):
            return o.value
        best = best or o.value
    return best


def is_deprecated(store: ox.Store, subj) -> bool:
    """A deprecated term is still a real IRI, but proposing it to a person as "the
    standard for this column" would be wrong."""
    return any(
        isinstance(o, ox.Literal) and o.value.lower() == "true"
        for pred in (QUDT + "deprecated", OWL + "deprecated")
        for o in objects(store, subj, pred)
    )


def header(script: str, version: str, extra: str) -> str:
    return (
        "# GENERATED — do not edit by hand. Regenerate with:\n"
        f"#     python scripts/{script} --version {version}\n"
        "#\n"
        f"# {extra}\n"
        "# Rationale in scripts/_qudt_mirror.py.\n"
    )
