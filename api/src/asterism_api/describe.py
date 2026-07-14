"""IRI dereference — the "phase 2" of ADR instance-iri-base.md.

``GET /describe?iri=<IRI>`` answers "what does this install's PUBLISHED data
say about this identifier?" — content-negotiated: Turtle for machines,
a small self-contained HTML page for humans (object IRIs link back through
/describe, so published data is browsable).

Scope and exposure (the security judgement, mirrored in the ADR):

* Reads ONLY the canonical + ontology graphs — the same merged scope every
  typed tool and Ask answer reads. Drafts / control graphs are unreachable by
  construction (the graph list comes from the server, never the caller).
* Requires no token: one IRI in, its published description out is a bounded
  read of already-published data (same exposure class as the typed tools),
  strictly narrower than the raw-SPARQL escape — so it stays available even
  on deployments that withhold ``/api/sparql``. The whole-site cookie gate
  (Caddy) still fronts it on a private box.
"""

from __future__ import annotations

import html
import re
from typing import Any

from asterism import substrate
from asterism.oxigraph_client import OxigraphClient

__all__ = [
    "INBOUND_LIMIT",
    "OUTBOUND_LIMIT",
    "fetch_description",
    "render_html",
    "turtle_queries",
]

# Bounded by design: a huge entity (a curve with thousands of points pointing
# back at it) must not turn one dereference into a full-graph dump.
OUTBOUND_LIMIT = 500
INBOUND_LIMIT = 200
_ABSOLUTE_IRI = re.compile(r"^https?://\S+$")

_LABEL_PREDICATES = (
    "http://www.w3.org/2000/01/rdf-schema#label",
    "http://www.w3.org/2004/02/skos/core#prefLabel",
    "https://schema.org/name",
    "http://schema.org/name",
)
_TYPE_PREDICATE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def valid_iri(iri: str) -> bool:
    """Only absolute http(s) IRIs are dereferenceable here (matches what the
    pipeline mints; also keeps injection surface at zero — see ``_ref``)."""
    return bool(_ABSOLUTE_IRI.match(iri)) and "<" not in iri and ">" not in iri


def _ref(iri: str) -> str:
    """``<iri>`` for embedding in a query. ``valid_iri`` has already rejected
    angle brackets and whitespace, so the term cannot escape its brackets."""
    return f"<{iri}>"


def _named_clause(graphs: list[str]) -> str:
    return "\n".join(f"FROM NAMED <{g}>" for g in graphs)


def description_queries(iri: str, graphs: list[str]) -> tuple[str, str]:
    """The two SELECTs backing the HTML view (outbound / inbound), each row
    carrying its source graph so provenance stays visible."""
    named = _named_clause(graphs)
    outbound = (
        f"SELECT ?p ?o ?g\n{named}\n"
        f"WHERE {{ GRAPH ?g {{ {_ref(iri)} ?p ?o }} }}\n"
        f"ORDER BY ?p ?o LIMIT {OUTBOUND_LIMIT + 1}"
    )
    inbound = (
        f"SELECT ?s ?p ?g\n{named}\n"
        f"WHERE {{ GRAPH ?g {{ ?s ?p {_ref(iri)} }} }}\n"
        f"ORDER BY ?s ?p LIMIT {INBOUND_LIMIT + 1}"
    )
    return outbound, inbound


def turtle_queries(iri: str, graphs: list[str]) -> tuple[str, str]:
    """The two CONSTRUCTs backing the machine (Turtle) view. Concatenating the
    two documents is valid Turtle (re-declared prefixes and duplicate triples
    are both legal)."""
    named = _named_clause(graphs)
    outbound = (
        f"CONSTRUCT {{ {_ref(iri)} ?p ?o }}\n{named}\n"
        f"WHERE {{ GRAPH ?g {{ {_ref(iri)} ?p ?o }} }} LIMIT {OUTBOUND_LIMIT}"
    )
    inbound = (
        f"CONSTRUCT {{ ?s ?p {_ref(iri)} }}\n{named}\n"
        f"WHERE {{ GRAPH ?g {{ ?s ?p {_ref(iri)} }} }} LIMIT {INBOUND_LIMIT}"
    )
    return outbound, inbound


def _rows(result: dict[str, Any]) -> list[dict[str, dict[str, str]]]:
    return list(result.get("results", {}).get("bindings", []))


async def fetch_description(
    client: OxigraphClient, iri: str
) -> dict[str, Any] | None:
    """Everything the HTML view needs, or None when the published scope holds
    nothing about the IRI (callers map that to 404)."""
    graphs = sorted(
        set(await substrate.canonical_graphs(client))
        | set(await substrate.ontology_graphs(client))
    )
    if not graphs:
        return None
    q_out, q_in = description_queries(iri, graphs)
    outbound = _rows(await client.sparql_select(q_out))
    inbound = _rows(await client.sparql_select(q_in))
    if not outbound and not inbound:
        return None

    out_truncated = len(outbound) > OUTBOUND_LIMIT
    in_truncated = len(inbound) > INBOUND_LIMIT
    outbound = outbound[:OUTBOUND_LIMIT]
    inbound = inbound[:INBOUND_LIMIT]

    label: str | None = None
    types: list[str] = []
    for row in outbound:
        p = row["p"]["value"]
        o = row["o"]
        if p == _TYPE_PREDICATE and o["type"] == "uri":
            types.append(o["value"])
        elif label is None and p in _LABEL_PREDICATES and o["type"] == "literal":
            label = o["value"]
    return {
        "graphs": graphs,
        "outbound": outbound,
        "inbound": inbound,
        "out_truncated": out_truncated,
        "in_truncated": in_truncated,
        "label": label,
        "types": types,
    }


# ---------------------------------------------------------------------------
# HTML rendering (self-contained, same visual family as docs/404.html)
# ---------------------------------------------------------------------------


def _local(iri: str) -> str:
    """Human-scannable tail of an IRI (after the last # or /)."""
    tail = iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    return tail or iri


def _iri_link(iri: str) -> str:
    """An IRI cell: dereference further through /describe (browsable data)."""
    e = html.escape(iri, quote=True)
    return (
        f'<a href="/describe?iri={html.escape(_urlquote(iri), quote=True)}" '
        f'title="{e}"><code>{html.escape(_local(iri))}</code></a>'
    )


def _urlquote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _term_cell(term: dict[str, str]) -> str:
    if term["type"] == "uri":
        return _iri_link(term["value"])
    text = html.escape(term["value"])
    if term.get("xml:lang"):
        return f"{text} <span class=\"muted\">@{html.escape(term['xml:lang'])}</span>"
    dt = term.get("datatype")
    if dt:
        return f"{text} <span class=\"muted\">^^{html.escape(_local(dt))}</span>"
    return text


def _graph_cell(graph_iri: str) -> str:
    title = html.escape(graph_iri, quote=True)
    return f'<span class="muted" title="{title}">{html.escape(_local(graph_iri))}</span>'


_PAGE_STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial,
    sans-serif; color: #1f2933; max-width: 900px; margin: 0 auto;
    padding: 2rem 1.25rem; line-height: 1.55; }
  h1 { border-bottom: 2px solid #4B7A52; padding-bottom: 0.3rem; font-size: 1.4rem; }
  code, pre { font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
    background: #f3f4f6; padding: 0.1em 0.35em; border-radius: 3px; font-size: 0.92em; }
  pre { padding: 0.75rem 1rem; overflow-x: auto; }
  a { color: #4B7A52; }
  table { border-collapse: collapse; width: 100%; margin: 0.75rem 0 1.5rem;
    font-size: 0.92rem; }
  th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #e5e7eb;
    vertical-align: top; word-break: break-word; }
  th { color: #6b7280; font-weight: 600; font-size: 0.8rem; }
  .muted { color: #6b7280; font-size: 0.88em; }
  .type-chip { display: inline-block; background: #eef2ee; border: 1px solid #4B7A52;
    border-radius: 999px; padding: 0.05rem 0.6rem; font-size: 0.8rem;
    margin-right: 0.35rem; }
  .iri-box { border: 1px solid #d2d6dc; border-left: 4px solid #4B7A52;
    border-radius: 4px; padding: 0.6rem 0.9rem; margin: 1rem 0; word-break: break-all; }
"""


def render_html(iri: str, data: dict[str, Any]) -> str:
    """The human view: label/types header, outbound and inbound property
    tables (object IRIs dereference further), provenance column, and the
    equivalent machine requests for reproducibility."""
    e_iri = html.escape(iri)
    title = html.escape(data["label"] or _local(iri))
    chips = "".join(
        f'<span class="type-chip" title="{html.escape(t, quote=True)}">'
        f"{html.escape(_local(t))}</span>"
        for t in data["types"]
    )
    out_rows = "".join(
        f"<tr><td>{_iri_link(r['p']['value'])}</td><td>{_term_cell(r['o'])}</td>"
        f"<td>{_graph_cell(r['g']['value'])}</td></tr>"
        for r in data["outbound"]
    )
    in_rows = "".join(
        f"<tr><td>{_iri_link(r['s']['value'])}</td><td>{_iri_link(r['p']['value'])}</td>"
        f"<td>{_graph_cell(r['g']['value'])}</td></tr>"
        for r in data["inbound"]
    )
    out_note = (
        f'<p class="muted">Showing the first {OUTBOUND_LIMIT} statements.</p>'
        if data["out_truncated"]
        else ""
    )
    in_note = (
        f'<p class="muted">Showing the first {INBOUND_LIMIT} references.</p>'
        if data["in_truncated"]
        else ""
    )
    inbound_section = (
        f"<h2>Referenced by</h2>{in_note}"
        f"<table><thead><tr><th>subject</th><th>predicate</th><th>graph</th></tr></thead>"
        f"<tbody>{in_rows}</tbody></table>"
        if in_rows
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title} — Asterism</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
<h1>{title}</h1>
<p>{chips}</p>
<div class="iri-box"><span class="muted">Identifier</span><br /><code>{e_iri}</code></div>
<p class="muted">Published description from this Asterism instance's canonical data
({len(data["graphs"])} graph(s) merged). Machine version:
<code>curl -H "Accept: text/turtle" &lt;this URL&gt;</code></p>
<h2>Statements</h2>{out_note}
<table><thead><tr><th>predicate</th><th>value</th><th>graph</th></tr></thead>
<tbody>{out_rows}</tbody></table>
{inbound_section}
</body>
</html>
"""


def render_not_found(iri: str, published_graphs: int) -> str:
    """404 body: honest about WHAT was searched, pointing at the likely causes
    (not yet promoted, or a different install minted it)."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Unknown identifier — Asterism</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
<h1>Not in this instance's published data</h1>
<div class="iri-box"><span class="muted">Identifier</span><br />
<code>{html.escape(iri)}</code></div>
<p>This Asterism instance searched its {published_graphs} published (canonical)
graph(s) and holds no statements about this identifier. Either the dataset it
belongs to has not been promoted here, or the identifier was minted by a
different install.</p>
</body>
</html>
"""
