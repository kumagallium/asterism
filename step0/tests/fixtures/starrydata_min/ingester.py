"""Minimal starrydata-shaped ingester — a csv2rdf-validate CI fixture.

Not a real ingester: it only emits enough triples to exercise the 8-trap
validator end-to-end. The IRI builders use the *composite* keys the validator
should recover:

    sdr:paper/{SID}
    sdr:sample/{SID}-{sample_id}
    sdr:curve/{SID}-{figure_id}-{sample_id}

Opens CSVs with ``utf-8-sig`` (T2) and never mints blank nodes (T3).
"""
from __future__ import annotations

import csv
from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import RDF

SDR = Namespace("https://example.com/starrydata/resource/")
SD = Namespace("https://example.com/starrydata/ontology#")


def paper_iri(sid: str):
    return SDR[f"paper/{sid}"]


def sample_iri(sid: str, sample_id: str):
    return SDR[f"sample/{sid}-{sample_id}"]


def curve_iri(sid: str, figure_id: str, sample_id: str):
    return SDR[f"curve/{sid}-{figure_id}-{sample_id}"]


def ingest_papers(path: Path, g: Graph) -> None:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            sid = row.get("SID", "").strip()
            if not sid:
                continue
            g.add((paper_iri(sid), RDF.type, SD.Paper))


def ingest_samples(path: Path, g: Graph) -> None:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            sid = row.get("SID", "").strip()
            sample_id = row.get("sample_id", "").strip()
            if not sid or not sample_id:
                continue
            sample = sample_iri(sid, sample_id)
            g.add((sample, RDF.type, SD.Sample))
            g.add((sample, SD.fromPaper, paper_iri(sid)))


def ingest_curves(path: Path, g: Graph) -> None:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            sid = row.get("SID", "").strip()
            figure_id = row.get("figure_id", "").strip()
            sample_id = row.get("sample_id", "").strip()
            if not sid or not figure_id or not sample_id:
                continue
            curve = curve_iri(sid, figure_id, sample_id)
            g.add((curve, RDF.type, SD.Curve))
            g.add((curve, SD.ofSample, sample_iri(sid, sample_id)))
