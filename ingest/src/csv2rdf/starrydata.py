"""Starrydata CSV -> RDF ingester (Phase 1).

Phase 0.5 で書いた experiments/phase05/scripts/csv_to_ttl.py を本実装版に格上げ:

- **blank node 排除**: Phase 0.5 で `schema:Periodical` を bnode にしていた。
  Phase 0.5 oxigraph 素振り §4.4 の通り、bnode は re-ingest 時に重複するため
  csv2rdf-mcp の auto-reindex フローと相性が悪い。Phase 1 では
  `sdr:periodical/{slug}` のような IRI に統一する。
- **PROV-O IngestionActivity**: 設計プラン §4 / option-b-architecture.md §3 に
  従い、各 ingest 実行で 1 つの `sd:IngestionActivity` を発行し、生成する全
  Entity に `prov:wasGeneratedBy` を付ける。
- **構造化エラー出力**: 失敗行は `error_log_path` に jsonl で残す。本体処理は
  止めない。
- **設定可能な base IRI**: design-plan §4.0 通り、Phase 1 は GitHub Pages を
  想定したホスト名を default にするが、上書き可能。
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import IO, Iterator, TextIO

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, XSD

# ----------------------------------------------------------------------------
# Default namespaces (design-plan §4 / §4.0)
# ----------------------------------------------------------------------------

DEFAULT_ONTOLOGY = "https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#"
DEFAULT_RESOURCE = "https://kumagallium.github.io/csv2rdf-mcp/starrydata/resource/"
SOFTWARE_AGENT_IRI = "https://github.com/kumagallium/csv2rdf-mcp"

SCHEMA = Namespace("https://schema.org/")
BIBO = Namespace("http://purl.org/ontology/bibo/")


# ----------------------------------------------------------------------------
# Stats / config
# ----------------------------------------------------------------------------


@dataclass
class IngestStats:
    rows_in: int = 0
    rows_ok: int = 0
    rows_err: int = 0
    triples_out: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None


@dataclass
class IngestConfig:
    ontology_iri: str = DEFAULT_ONTOLOGY
    resource_iri: str = DEFAULT_RESOURCE
    software_agent_iri: str = SOFTWARE_AGENT_IRI
    emit_prov: bool = True


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

_SLUG_DROP = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_len: int = 80) -> str:
    """IRI segment 用の slug。a-z0-9 + 1 個の `-` のみに正規化。

    starrydata の container_title (journal 名) は引用符付き文字列なので、まず
    json.loads を試みて剥がしてから slug 化する。
    """
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        try:
            s = json.loads(s)
        except json.JSONDecodeError:
            s = s.strip('"')
    s = s.lower()
    s = _SLUG_DROP.sub("-", s).strip("-")
    if not s:
        return "unknown"
    return s[:max_len]


def parse_issued(raw: str) -> str | None:
    """{"date_parts": [[YYYY, MM?, DD?]]} -> ISO 8601 date (best effort)."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        parts = data.get("date_parts", [[]])[0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
        return None
    if not parts:
        return None
    y = parts[0] if len(parts) >= 1 else None
    m = parts[1] if len(parts) >= 2 else 1
    d = parts[2] if len(parts) >= 3 else 1
    if not isinstance(y, int):
        return None
    try:
        return date(y, m or 1, d or 1).isoformat()
    except ValueError:
        return None


def parse_authors(raw: str) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        given = str(item.get("given", "")).strip()
        family = str(item.get("family", "")).strip()
        if not (given or family):
            continue
        out.append({"given": given, "family": family})
    return out


def parse_project_names(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if str(x).strip()]


def strip_quoted(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v.strip('"')
    return v


# ----------------------------------------------------------------------------
# Core: build_graph_for_paper
# ----------------------------------------------------------------------------


def _emit_paper(
    g: Graph,
    row: dict[str, str],
    sd: Namespace,
    sdr: Namespace,
    ingestion_iri: URIRef | None,
) -> bool:
    """1 行 (papers.csv) を Graph に追加。成功で True、SID 不在なら False。"""
    sid = row.get("SID", "").strip()
    if not sid:
        return False
    paper = sdr[f"paper/{sid}"]

    g.add((paper, RDF.type, sd.Paper))
    g.add((paper, RDF.type, SCHEMA.ScholarlyArticle))
    g.add((paper, RDF.type, PROV.Entity))  # design-plan §4: Paper is prov:Entity
    g.add((paper, DCTERMS.identifier, Literal(sid)))

    if ingestion_iri is not None:
        g.add((paper, PROV.wasGeneratedBy, ingestion_iri))

    if doi := row.get("DOI", "").strip():
        g.add((paper, SCHEMA.identifier, Literal(doi)))
    if url := row.get("URL", "").strip():
        g.add((paper, SCHEMA.url, URIRef(url)))
    if title := strip_quoted(row.get("title", "")):
        g.add((paper, SCHEMA.name, Literal(title)))

    if container := strip_quoted(row.get("container_title", "")):
        slug = slugify(container)
        periodical = sdr[f"periodical/{slug}"]
        g.add((paper, SCHEMA.isPartOf, periodical))
        # The periodical is shared across multiple papers; we add the type/name
        # triples idempotently (Oxigraph set semantics deduplicates).
        g.add((periodical, RDF.type, SCHEMA.Periodical))
        g.add((periodical, SCHEMA.name, Literal(container)))
        if container_short := strip_quoted(row.get("container_title_short", "")):
            g.add((periodical, SCHEMA.alternateName, Literal(container_short)))

    if issued_iso := parse_issued(row.get("issued", "")):
        g.add(
            (paper, SCHEMA.datePublished, Literal(issued_iso, datatype=XSD.date))
        )

    for csv_col, ns in (("volume", BIBO.volume), ("issue", BIBO.issue)):
        if v := strip_quoted(row.get(csv_col, "")):
            g.add((paper, ns, Literal(v)))
    if pages := strip_quoted(row.get("page", "")):
        g.add((paper, BIBO.pages, Literal(pages)))

    if publisher := strip_quoted(row.get("publisher", "")):
        g.add((paper, SCHEMA.publisher, Literal(publisher)))

    for project in parse_project_names(row.get("project_names", "")):
        g.add((paper, sd.projectName, Literal(project)))

    for i, author in enumerate(parse_authors(row.get("author", ""))):
        person = sdr[f"person/{sid}/{i}"]
        g.add((paper, SCHEMA.author, person))
        g.add((person, RDF.type, SCHEMA.Person))
        g.add((person, RDF.type, PROV.Agent))
        if author["given"]:
            g.add((person, SCHEMA.givenName, Literal(author["given"])))
        if author["family"]:
            g.add((person, SCHEMA.familyName, Literal(author["family"])))

    # created_at column is the starrydata curator's import timestamp (loosely formatted).
    # We keep it as a literal string rather than xsd:dateTime to avoid parse fragility.
    if created := strip_quoted(row.get("created_at", "")):
        g.add((paper, DCTERMS.created, Literal(created)))

    return True


def _emit_ingestion_activity(
    g: Graph,
    sd: Namespace,
    sdr: Namespace,
    csv_path: Path,
    run_id: str,
    started_at: datetime,
    software_agent_iri: str,
) -> URIRef:
    activity = sdr[f"ingestion/{run_id}"]
    g.add((activity, RDF.type, sd.IngestionActivity))
    g.add((activity, RDF.type, PROV.Activity))
    g.add(
        (activity, PROV.atTime, Literal(started_at.isoformat(), datatype=XSD.dateTime))
    )
    source = sdr[f"source/{csv_path.name}"]
    g.add((activity, PROV.used, source))
    g.add((source, RDF.type, PROV.Entity))
    g.add((source, SCHEMA.name, Literal(csv_path.name)))
    g.add((activity, PROV.wasAssociatedWith, URIRef(software_agent_iri)))
    return activity


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


def ingest_papers(
    csv_path: Path | str,
    out_path: Path | str,
    config: IngestConfig | None = None,
    error_log_path: Path | str | None = None,
) -> IngestStats:
    """Convert papers.csv to a Turtle file at `out_path`.

    Args:
        csv_path: Path to starrydata_papers.csv (or any compatible subset).
        out_path: Destination Turtle file. Parent dirs are created on demand.
        config: IngestConfig overrides (defaults to GitHub Pages namespace).
        error_log_path: Optional jsonl file for failed rows.

    Returns:
        IngestStats with row counts and triple count.
    """
    cfg = config or IngestConfig()
    csv_path = Path(csv_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sd = Namespace(cfg.ontology_iri)
    sdr = Namespace(cfg.resource_iri)

    g = Graph()
    g.bind("sd", sd)
    g.bind("sdr", sdr)
    g.bind("schema", SCHEMA)
    g.bind("dcterms", DCTERMS)
    g.bind("bibo", BIBO)
    g.bind("prov", PROV)

    stats = IngestStats()
    err_fh: TextIO | None = None
    if error_log_path is not None:
        err_fh = Path(error_log_path).open("w", encoding="utf-8")

    ingestion_iri: URIRef | None = None
    if cfg.emit_prov:
        run_id = "run-" + stats.started_at.strftime("%Y%m%dT%H%M%SZ")
        ingestion_iri = _emit_ingestion_activity(
            g, sd, sdr, csv_path, run_id, stats.started_at, cfg.software_agent_iri
        )

    try:
        with csv_path.open(encoding="utf-8", newline="") as fi:
            reader = csv.DictReader(fi)
            for row in reader:
                stats.rows_in += 1
                try:
                    if _emit_paper(g, row, sd, sdr, ingestion_iri):
                        stats.rows_ok += 1
                except Exception as exc:  # noqa: BLE001
                    stats.rows_err += 1
                    if err_fh is not None:
                        err_fh.write(
                            json.dumps(
                                {
                                    "row": stats.rows_in,
                                    "sid": row.get("SID"),
                                    "error": repr(exc),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
    finally:
        if err_fh is not None:
            err_fh.close()

    if ingestion_iri is not None:
        stats.ended_at = datetime.now(timezone.utc)
        g.add(
            (
                ingestion_iri,
                PROV.endedAtTime,
                Literal(stats.ended_at.isoformat(), datatype=XSD.dateTime),
            )
        )
    else:
        stats.ended_at = datetime.now(timezone.utc)

    g.serialize(destination=str(out_path), format="turtle")
    stats.triples_out = len(g)
    return stats


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="csv2rdf-starrydata-papers",
        description="Ingest starrydata papers.csv into a Turtle file.",
    )
    p.add_argument("csv", type=Path, help="Path to starrydata_papers.csv")
    p.add_argument("ttl", type=Path, help="Destination Turtle file")
    p.add_argument(
        "--ontology", default=DEFAULT_ONTOLOGY, help="Override ontology namespace IRI"
    )
    p.add_argument(
        "--resource", default=DEFAULT_RESOURCE, help="Override resource namespace IRI"
    )
    p.add_argument(
        "--no-prov",
        action="store_true",
        help="Skip emitting sd:IngestionActivity (for tests)",
    )
    p.add_argument(
        "--errors", type=Path, default=None, help="Optional jsonl path for failed rows"
    )
    args = p.parse_args(argv)

    cfg = IngestConfig(
        ontology_iri=args.ontology,
        resource_iri=args.resource,
        emit_prov=not args.no_prov,
    )
    stats = ingest_papers(args.csv, args.ttl, cfg, error_log_path=args.errors)
    print(
        f"in={stats.rows_in} ok={stats.rows_ok} err={stats.rows_err} "
        f"triples={stats.triples_out} -> {args.ttl}",
        file=sys.stderr,
    )
    return 0 if stats.rows_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_main())
