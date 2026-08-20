#!/usr/bin/env python3
"""Regenerate `ingest/src/asterism/grounding/qudt_units.yaml` from the QUDT unit vocabulary.

WHY THIS ONE IS A MIRROR, NOT A CURATION. `known_vocabs.yaml` is deliberately
hand-curated ("CURATION, not mirroring") because picking WHICH class/property a
dataset should reuse is a design judgement, and a catalog full of near-synonyms
makes that judgement worse. Units are not like that: `V/K` has exactly one
answer, and a missing unit is a silent hole (the value keeps its string and
quietly gets no QUDT IRI). So the unit catalog mirrors the vocabulary whole, in
its OWN file, and stays out of the term catalog.

Run it only when moving to a new QUDT release:

    python scripts/build_qudt_units.py --version 3.1.0

Requires pyoxigraph (already an ingest dependency) so the Turtle is parsed by a
real RDF parser rather than a regex over machine-generated text.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import urllib.request
from pathlib import Path

import pyoxigraph as ox
import yaml

QUDT = "http://qudt.org/schema/qudt/"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
UNIT_NS = "http://qudt.org/vocab/unit/"
QK_NS = "http://qudt.org/vocab/quantitykind/"
SOU_SI = "http://qudt.org/vocab/sou/SI"

OUT = Path(__file__).resolve().parents[1] / "ingest/src/asterism/grounding/qudt_units.yaml"


def _local(iri: str, ns: str) -> str | None:
    return iri[len(ns):] if iri.startswith(ns) else None


def _objects(store: ox.Store, subj, pred: str) -> list:
    return [q.object for q in store.quads_for_pattern(subj, ox.NamedNode(pred), None)]


def _en_label(store: ox.Store, subj) -> str:
    """The English label. QUDT carries several languages; keep `en` (or the tagless
    one) so the catalog reads the same everywhere and never depends on parse order."""
    best = ""
    for o in _objects(store, subj, RDFS + "label"):
        if not isinstance(o, ox.Literal):
            continue
        lang = (o.language or "").lower()
        if lang in ("en", ""):
            return o.value
        best = best or o.value
    return best


def _load_turtle(ttl: Path) -> ox.Store:
    """Parse the Turtle with whichever pyoxigraph is installed. The substrate extra
    pins <0.4 (morph-kgc), a plain install gets the current one, and the two spell
    loading differently — this script has to run under either."""
    store = ox.Store()
    try:  # pyoxigraph >= 0.4
        store.bulk_load(path=str(ttl), format=ox.RdfFormat.TURTLE)
    except (AttributeError, TypeError):  # pyoxigraph 0.3.x
        store.load(ttl.read_bytes(), mime_type="text/turtle")
    return store


def build(ttl: Path, version: str, retrieved: str) -> dict:
    store = _load_turtle(ttl)
    type_ = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    subjects = [q.subject for q in store.quads_for_pattern(None, type_, ox.NamedNode(QUDT + "Unit"))]

    units: dict[str, dict] = {}
    skipped_deprecated = 0
    for s in subjects:
        name = _local(s.value, UNIT_NS)
        if not name:
            continue
        # A deprecated unit is still a real IRI, but proposing it to a person as
        # "the standard for this column" would be wrong.
        deprecated = any(
            isinstance(o, ox.Literal) and o.value.lower() == "true"
            for pred in (QUDT + "deprecated", OWL + "deprecated")
            for o in _objects(store, s, pred)
        )
        if deprecated:
            skipped_deprecated += 1
            continue
        entry: dict = {}
        label = _en_label(store, s)
        if label:
            entry["label"] = label
        symbols = [o.value for o in _objects(store, s, QUDT + "symbol") if isinstance(o, ox.Literal)]
        if symbols:
            entry["symbol"] = symbols[0]
        ucum = sorted({o.value for o in _objects(store, s, QUDT + "ucumCode") if isinstance(o, ox.Literal)})
        if ucum:
            entry["ucum"] = ucum
        # 66 symbols are claimed by more than one unit ("K" is kelvin AND kayser,
        # "S" is siemens AND solar mass). Recording SI membership lets the resolver
        # settle those the way every reader means them, instead of asking.
        if any(
            isinstance(o, ox.NamedNode) and o.value == SOU_SI
            for o in _objects(store, s, QUDT + "applicableSystem")
        ):
            entry["si"] = True
        qks = sorted(
            {
                lo
                for o in _objects(store, s, QUDT + "hasQuantityKind")
                if isinstance(o, ox.NamedNode) and (lo := _local(o.value, QK_NS))
            }
        )
        if qks:
            entry["qk"] = qks
        units[name] = entry

    print(f"units: {len(units)} (skipped deprecated: {skipped_deprecated})", file=sys.stderr)
    return {
        "source": f"https://qudt.org/{version}/vocab/unit",
        "version": version,
        "retrieved": retrieved,
        "license": "CC-BY-4.0",
        "namespace": UNIT_NS,
        "quantity_kind_namespace": QK_NS,
        "units": dict(sorted(units.items())),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="3.1.0", help="QUDT release to mirror")
    ap.add_argument("--ttl", type=Path, default=None, help="local unit.ttl (default: download)")
    ap.add_argument("--retrieved", default=None, help="YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    ttl = args.ttl
    tmp = None
    if ttl is None:
        url = f"https://qudt.org/{args.version}/vocab/unit"
        print(f"downloading {url}", file=sys.stderr)
        tmp = Path(f"/tmp/qudt-unit-{args.version}.ttl")
        urllib.request.urlretrieve(url, tmp)
        ttl = tmp

    retrieved = args.retrieved or _dt.datetime.now(_dt.UTC).date().isoformat()
    data = build(ttl, args.version, retrieved)

    header = (
        "# GENERATED — do not edit by hand. Regenerate with:\n"
        f"#     python scripts/build_qudt_units.py --version {data['version']}\n"
        "#\n"
        "# A MIRROR of the QUDT unit vocabulary (CC-BY 4.0), unlike known_vocabs.yaml\n"
        "# which is hand-curated. Rationale in scripts/build_qudt_units.py.\n"
    )
    args.out.write_text(
        header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
