"""Phase 0.5 用 papers.csv -> Turtle 変換スクリプト。

設計プラン §4 のスキーマを **最小限**に踏襲した spike 実装。Phase 1 で
本格的な ingester に書き直すための叩き台。

- sd:Paper / schema:author / schema:datePublished / dcterms:identifier (SID) を生成
- authors の JSON は schema:Person のリストに展開 (氏名のみ。affiliation は無視)
- issued の JSON ({"date_parts": [[YYYY, MM, DD]]}) は xsd:date に変換 (best effort)
- IRI のホストは Phase 1 で確定。spike では http://localhost/asterism/ をプレースホルダ
- 失敗行は stderr に WARN を出すだけで処理続行

依存:
    pip install rdflib

使い方:
    python csv_to_ttl.py <input.csv> <output.ttl>
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, RDF, XSD

SD = Namespace("http://localhost/asterism/starrydata/ontology#")
SDR = Namespace("http://localhost/asterism/starrydata/resource/")
SCHEMA = Namespace("https://schema.org/")
BIBO = Namespace("http://purl.org/ontology/bibo/")


def parse_issued(raw: str) -> str | None:
    """{"date_parts": [[YYYY, MM?, DD?]]} -> ISO 8601 date best effort."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        parts = data.get("date_parts", [[]])[0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
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


def strip_quoted(value: str) -> str:
    """starrydata の文字列カラムは JSON literal で二重引用符が残ることがある。"""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v.strip('"')
    return v


def build(g: Graph, row: dict[str, str]) -> None:
    sid = row.get("SID", "").strip()
    if not sid:
        return
    paper = SDR[f"paper/{sid}"]
    g.add((paper, RDF.type, SD.Paper))
    g.add((paper, RDF.type, SCHEMA.ScholarlyArticle))
    g.add((paper, DCTERMS.identifier, Literal(sid)))

    doi = row.get("DOI", "").strip()
    if doi:
        g.add((paper, SCHEMA.identifier, Literal(doi)))
    url = row.get("URL", "").strip()
    if url:
        g.add((paper, SCHEMA.url, URIRef(url)))

    title = strip_quoted(row.get("title", ""))
    if title:
        g.add((paper, SCHEMA.name, Literal(title)))

    container = strip_quoted(row.get("container_title", ""))
    if container:
        # schema:isPartOf -> Periodical の代わりに spike では bnode で済ます
        from rdflib import BNode

        periodical = BNode()
        g.add((paper, SCHEMA.isPartOf, periodical))
        g.add((periodical, RDF.type, SCHEMA.Periodical))
        g.add((periodical, SCHEMA.name, Literal(container)))

    issued_iso = parse_issued(row.get("issued", ""))
    if issued_iso:
        g.add(
            (paper, SCHEMA.datePublished, Literal(issued_iso, datatype=XSD.date))
        )

    for field, ns in (("volume", BIBO.volume), ("issue", BIBO.issue)):
        v = strip_quoted(row.get(field, ""))
        if v:
            g.add((paper, ns, Literal(v)))
    page = strip_quoted(row.get("page", ""))
    if page:
        g.add((paper, BIBO.pages, Literal(page)))

    publisher = strip_quoted(row.get("publisher", ""))
    if publisher:
        g.add((paper, SCHEMA.publisher, Literal(publisher)))

    for i, author in enumerate(parse_authors(row.get("author", ""))):
        person = SDR[f"person/{sid}/{i}"]
        g.add((paper, SCHEMA.author, person))
        g.add((person, RDF.type, SCHEMA.Person))
        if author["given"]:
            g.add((person, SCHEMA.givenName, Literal(author["given"])))
        if author["family"]:
            g.add((person, SCHEMA.familyName, Literal(author["family"])))


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <papers.csv> <out.ttl>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    dst.parent.mkdir(parents=True, exist_ok=True)

    g = Graph()
    g.bind("sd", SD)
    g.bind("sdr", SDR)
    g.bind("schema", SCHEMA)
    g.bind("dcterms", DCTERMS)
    g.bind("bibo", BIBO)

    n_in = n_ok = n_err = 0
    with src.open(encoding="utf-8", newline="") as fi:
        reader = csv.DictReader(fi)
        for row in reader:
            n_in += 1
            try:
                before = len(g)
                build(g, row)
                if len(g) > before:
                    n_ok += 1
            except Exception as exc:  # noqa: BLE001
                n_err += 1
                print(
                    f"WARN row {n_in} SID={row.get('SID')}: {exc}",
                    file=sys.stderr,
                )

    g.serialize(destination=str(dst), format="turtle")
    print(
        f"in={n_in} ok={n_ok} err={n_err} triples={len(g)} -> {dst}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
