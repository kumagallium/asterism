"""IRI dereference — the "phase 2" of ADR instance-iri-base.md.

``GET /describe?iri=<IRI>`` answers "what does this install's PUBLISHED data
say about this identifier?" — content-negotiated: Turtle for machines,
a small self-contained HTML page for humans (object IRIs link back through
/describe, so published data is browsable).

The HTML view is a *shared surface* in the sense of ADR
kantan-mode-two-tier-ux.md §5: it is where someone who was handed a citation
lands. So it speaks the かんたん vocabulary (「項目」「出どころ」「ID」), never
predicate / graph / canonical, and it is Japanese-first (``?lang=`` and
``Accept-Language`` switch it). Technical detail is not deleted — it moves into
a ``<details>`` block, the escape hatch the ADR reserves for it.

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

# This module is a Japanese-language page template: its copy uses full-width
# parentheses and slashes on purpose (ADR kantan-mode-two-tier-ux.md §5 spells
# the terms that way). RUF001 flags those as "ambiguous" ASCII look-alikes,
# which they are not here.
# ruff: noqa: RUF001
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from asterism import substrate
from asterism.oxigraph_client import OxigraphClient

__all__ = [
    "INBOUND_LIMIT",
    "OUTBOUND_LIMIT",
    "fetch_description",
    "pick_language",
    "render_bad_request",
    "render_html",
    "render_not_found",
    "render_upstream_error",
    "shared_terms_label",
    "turtle_queries",
]

# Bounded by design: a huge entity (a curve with thousands of points pointing
# back at it) must not turn one dereference into a full-graph dump.
OUTBOUND_LIMIT = 500
INBOUND_LIMIT = 200
# Labels are fetched for the IRIs actually shown; the cap keeps the extra query
# bounded even when a page is at OUTBOUND_LIMIT.
LABEL_TERM_LIMIT = 400
_ABSOLUTE_IRI = re.compile(r"^https?://\S+$")

_LABEL_PREDICATES = (
    "http://www.w3.org/2000/01/rdf-schema#label",
    "http://www.w3.org/2004/02/skos/core#prefLabel",
    "https://schema.org/name",
    "http://schema.org/name",
)
_TYPE_PREDICATE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

# Predicates in these namespaces are *how the data was recorded*, not the values
# the reader came for (rdf:type, rdfs:label, prov:wasGeneratedBy … are attached
# to every entity by the pipeline). They sort first by IRI string, which used to
# push the user's own columns below the fold — so they move into a folded block.
_RECORD_NAMESPACES = (
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "http://www.w3.org/2002/07/owl#",
    "http://www.w3.org/2004/02/skos/core#",
    "http://www.w3.org/ns/prov#",
    "http://www.w3.org/ns/dcat#",
    "http://purl.org/dc/terms/",
    "http://purl.org/dc/elements/1.1/",
    "http://xmlns.com/foaf/0.1/",
    substrate.ASTERISM_NS,
)

# Asterism mints entity/term IRIs under ``<base>/…/datasets/<slug>/``; that
# namespace is the strongest deterministic hint of "this install can answer for
# it" available without a second round-trip.
_DATASET_NS = re.compile(r"^(https?://.+?/datasets/[^/#?]+/)")


# ---------------------------------------------------------------------------
# Language (this page is Japanese-first; ?lang= / Accept-Language switch it)
# ---------------------------------------------------------------------------

LANGUAGES = ("ja", "en")

_TEXT: dict[str, dict[str, str]] = {
    "ja": {
        "brand": "Asterism",
        "home": "ホームへ",
        "view_dataset": "このデータセットを見る",
        "ask": "このデータに質問する",
        "browse": "このサイトのデータを見る",
        "id_label": "ID（ずっと変わらないウェブ上の住所）",
        "copy": "コピー",
        "copied": "コピーしました",
        "dataset_label": "データセット",
        "kind_label": "種類",
        "intro": "このページは、公開済みデータに記録されている内容です。",
        "statements": "この ID について分かっていること",
        "th_item": "項目",
        "th_value": "値",
        "th_source": "出どころ（データセット）",
        "records": "取り込みの記録（技術情報）",
        "inbound": "この ID を使っている（参照している）データ",
        "th_from": "参照元",
        "only_meta": "この ID について記録されているのは、名前と種類だけです。",
        "out_note": "数が多いので、はじめの {n} 件だけを表示しています。",
        "in_note": "参照しているデータのうち、はじめの {n} 件だけを表示しています。",
        "tech": "詳しい内容（技術情報）",
        "machine": "同じ内容を機械向けに取り出す:",
        "shared_terms": "共通の言葉",
        "nf_title": "見つからない ID",
        "nf_heading": "このリンクのデータは、まだ公開されていないようです",
        "nf_body": (
            "この ID は、このサイトの公開済みデータには入っていません。"
            "まだ公開されていないか、別の場所で作られたデータかもしれません。"
        ),
        "nf_hint": "このリンクを送ってくれた人が公開すると、ここに内容が表示されます。",
        "bad_title": "開けないリンク",
        "bad_heading": "リンクが正しくないようです",
        "bad_body": (
            "貼り付けたリンクが途中で切れているか、余分な文字が混ざっているようです。"
            "送ってくれた人に、もう一度リンクを教えてもらってください。"
        ),
        "err_title": "いま開けません",
        "err_heading": "いま一時的に読み込めませんでした",
        "err_body": "少し待ってから、もう一度開いてください。",
    },
    "en": {
        "brand": "Asterism",
        "home": "Home",
        "view_dataset": "View this dataset",
        "ask": "Ask about this data",
        "browse": "Browse this site's data",
        "id_label": "ID (a permanent web address)",
        "copy": "Copy",
        "copied": "Copied",
        "dataset_label": "Dataset",
        "kind_label": "Type",
        "intro": "This page shows what the published data records about this ID.",
        "statements": "What is known about this ID",
        "th_item": "Item",
        "th_value": "Value",
        "th_source": "Source dataset",
        "records": "How this was recorded (technical)",
        "inbound": "Data that references this ID",
        "th_from": "Referenced from",
        "only_meta": "The only things recorded for this ID are its name and its type.",
        "out_note": "Showing the first {n} items only.",
        "in_note": "Showing the first {n} of the data that references this ID.",
        "tech": "More detail (technical)",
        "machine": "The same content for machines:",
        "shared_terms": "Shared terms",
        "nf_title": "ID not found",
        "nf_heading": "This link's data does not seem to be published yet",
        "nf_body": (
            "This ID is not part of the published data on this site. "
            "It may not be published yet, or it may come from somewhere else."
        ),
        "nf_hint": "Once the person who sent you this link publishes it, "
        "the content will appear here.",
        "bad_title": "Link cannot be opened",
        "bad_heading": "This link does not look right",
        "bad_body": (
            "The link looks cut off, or has extra characters in it. "
            "Ask the person who sent it for the link again."
        ),
        "err_title": "Cannot open right now",
        "err_heading": "This could not be loaded right now",
        "err_body": "Please wait a moment and open it again.",
    },
}


def pick_language(explicit: str | None, accept_language: str | None) -> str:
    """``?lang=`` wins, then ``Accept-Language``, then Japanese (§1 of the UI
    guidelines: Japanese-first)."""
    if explicit and explicit.lower() in LANGUAGES:
        return explicit.lower()
    for part in (accept_language or "").split(","):
        tag = part.split(";", 1)[0].strip().lower()
        if tag.startswith("en"):
            return "en"
        if tag.startswith("ja"):
            return "ja"
    return "ja"


def _t(lang: str) -> dict[str, str]:
    return _TEXT.get(lang, _TEXT["ja"])


def shared_terms_label(lang: str) -> str:
    """Display name for an ontology graph — the app calls it 「共通の言葉」."""
    return _t(lang)["shared_terms"]


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


def label_query(terms: list[str], graphs: list[str]) -> str:
    """One round-trip that pulls the human name of every IRI the page shows.

    The labels already exist in the merged scope (``ontology_projection`` writes
    ``rdfs:label`` for classes and properties), so showing them is deterministic
    — no model is asked to name anything.
    """
    named = _named_clause(graphs)
    values = " ".join(_ref(t) for t in terms)
    preds = " ".join(_ref(p) for p in _LABEL_PREDICATES)
    return (
        f"SELECT ?t ?l\n{named}\n"
        f"WHERE {{ VALUES ?t {{ {values} }}\n"
        f"VALUES ?lp {{ {preds} }}\n"
        f"GRAPH ?g {{ ?t ?lp ?l }}\n"
        f"FILTER(isLiteral(?l)) }}\n"
        f"LIMIT {len(terms) * len(_LABEL_PREDICATES)}"
    )


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


def _label_terms(iri: str, outbound: list[dict], inbound: list[dict]) -> list[str]:
    """Every IRI whose human name the page would like to show, in display order
    (so the cap, when it bites, keeps the rows nearest the top)."""
    seen: dict[str, None] = {iri: None}
    for row in outbound:
        seen.setdefault(row["p"]["value"], None)
        if row["o"]["type"] == "uri":
            seen.setdefault(row["o"]["value"], None)
    for row in inbound:
        seen.setdefault(row["s"]["value"], None)
        seen.setdefault(row["p"]["value"], None)
    return list(seen)[:LABEL_TERM_LIMIT]


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

    terms = _label_terms(iri, outbound, inbound)
    labels: dict[str, list[dict[str, str]]] = {}
    if terms:
        for row in _rows(await client.sparql_select(label_query(terms, graphs))):
            entry = {
                "value": row["l"]["value"],
                "lang": row["l"].get("xml:lang", "") or "",
            }
            labels.setdefault(row["t"]["value"], []).append(entry)

    return {
        "graphs": graphs,
        "outbound": outbound,
        "inbound": inbound,
        "out_truncated": out_truncated,
        "in_truncated": in_truncated,
        "label": label,
        "labels": labels,
        "types": types,
    }


# ---------------------------------------------------------------------------
# HTML rendering (self-contained, same visual family as the app)
# ---------------------------------------------------------------------------


def _local(iri: str) -> str:
    """Human-scannable tail of an IRI (after the last # or /)."""
    tail = iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    return tail or iri


def _urlquote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _pick_label(entries: list[dict[str, str]] | None, lang: str) -> str | None:
    """Prefer the page language, then an untagged literal, then anything."""
    if not entries:
        return None
    for want in (lang, ""):
        for entry in entries:
            if entry.get("lang", "") == want and entry.get("value"):
                return entry["value"]
    return entries[0].get("value") or None


def local_bases(iri: str, iri_base: str | None) -> tuple[str, ...]:
    """Namespaces this install plausibly holds statements about.

    Only IRIs under one of these become ``/describe`` links: an external
    vocabulary term (qudt, schema.org, w3.org …) has no statements in the
    published scope, so linking it would manufacture a dead end on the very
    page a citation lands on.
    """
    bases: list[str] = []
    if iri_base:
        bases.append(iri_base.rstrip("/") + "/")
    m = _DATASET_NS.match(iri)
    if m:
        bases.append(m.group(1))
    # The described entity's own namespace, for mints that do not follow the
    # ``/datasets/<slug>/`` layout (imported snapshots, bundled data).
    if "#" in iri:
        bases.append(iri.rsplit("#", 1)[0] + "#")
    else:
        head = iri.rsplit("/", 1)[0]
        if head:
            bases.append(head + "/")
    return tuple(dict.fromkeys(b for b in bases if b))


@dataclass
class _Ctx:
    """Everything the cell renderers need, resolved once per page."""

    lang: str
    labels: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    bases: tuple[str, ...] = ()
    graph_info: dict[str, dict[str, str]] = field(default_factory=dict)


def _is_local(iri: str, bases: tuple[str, ...]) -> bool:
    return any(iri.startswith(b) for b in bases)


def _term_name(iri: str, ctx: _Ctx) -> str:
    """What an IRI is *called* on this page: its human name when the shared
    vocabulary has one, otherwise the readable tail in code style."""
    label = _pick_label(ctx.labels.get(iri), ctx.lang)
    return html.escape(label) if label else f"<code>{html.escape(_local(iri))}</code>"


def _iri_cell(iri: str, ctx: _Ctx, *, force_link: bool = False) -> str:
    """One IRI cell: its human name, linked only when this install can answer."""
    inner = _term_name(iri, ctx)
    title = html.escape(iri, quote=True)
    if force_link or _is_local(iri, ctx.bases):
        href = html.escape(_urlquote(iri), quote=True)
        return (
            f'<a href="/describe?iri={href}&amp;lang={ctx.lang}" '
            f'title="{title}">{inner}</a>'
        )
    return f'<span title="{title}">{inner}</span>'


def _item_cell(iri: str, ctx: _Ctx) -> str:
    """The 項目 column: a name, never a link.

    A term IRI *does* resolve here — the shared vocabulary is inside the read
    scope — but what it resolves to is a definition of the word, with none of
    the reader's own data on it. Pressing 「温度」 and landing on that is a dead
    end one level deeper, so only the 値 column (a real thing in the data) is
    followed. The full IRI stays available on hover.
    """
    return f'<span title="{html.escape(iri, quote=True)}">{_term_name(iri, ctx)}</span>'


def _term_cell(term: dict[str, str], ctx: _Ctx) -> str:
    if term["type"] == "uri":
        return _iri_cell(term["value"], ctx)
    text = html.escape(term["value"])
    # The RDF literal notation (^^double, @ja) is noise for a reader; keep it
    # available on hover instead of printing it beside every value.
    note = term.get("xml:lang") or (
        _local(term["datatype"]) if term.get("datatype") else ""
    )
    if note:
        return f'<span title="{html.escape(note, quote=True)}">{text}</span>'
    return text


def _source_cell(graph_iri: str, ctx: _Ctx) -> str:
    """The 出どころ column: a dataset name, never an internal graph id."""
    name = (ctx.graph_info.get(graph_iri) or {}).get("name") or ""
    return html.escape(name)


def _primary_dataset(data: dict[str, Any], ctx: _Ctx) -> dict[str, str]:
    """The dataset this entity mostly lives in (first resolvable source)."""
    for row in data["outbound"]:
        info = ctx.graph_info.get(row["g"]["value"])
        if info and info.get("dataset_id"):
            return info
    return {}


_COPY_SCRIPT = """
(function () {
  var copy = "__COPY__", copied = "__COPIED__";
  document.querySelectorAll("button[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (!navigator.clipboard || !navigator.clipboard.writeText) return;
      navigator.clipboard.writeText(b.getAttribute("data-copy") || "").then(
        function () {
          b.textContent = copied;
          setTimeout(function () { b.textContent = copy; }, 1500);
        },
        function () {}
      );
    });
  });
})();
"""

_PAGE_STYLE = """
  :root { --bg: #eef3ec; --surface: #ffffff; --surface-alt: #f4f8f1;
    --surface-sink: #eaf0e8; --fg: #16241a; --body: #33453a; --muted: #54695b;
    --faint: #869a8c; --border: #dde6da; --border-strong: #c7d4c4;
    --primary: #3f6f49; --primary-soft: #e6efe4; --radius: 12px; --radius-sm: 8px; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, "Hanken Grotesk", "Zen Kaku Gothic New",
      "Noto Sans JP", -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica,
      Arial, sans-serif;
    background: var(--bg); color: var(--body); margin: 0; line-height: 1.65; }
  .page { max-width: 900px; margin: 0 auto; padding: 24px 20px 48px; }
  .topbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
    margin-bottom: 16px; font-size: 0.9rem; }
  .topbar a { color: var(--primary); text-decoration: none; font-weight: 600; }
  .topbar a:hover { text-decoration: underline; }
  .card { background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px; }
  h1 { color: var(--fg); font-size: 1.45rem; margin: 0 0 8px; line-height: 1.35; }
  h2 { color: var(--fg); font-size: 1.05rem; margin: 24px 0 8px; }
  p { margin: 8px 0; }
  code, pre { font-family: "IBM Plex Mono", "SF Mono", Menlo, Monaco, Consolas,
    monospace; background: var(--surface-alt); padding: 0.1em 0.35em;
    border-radius: 4px; font-size: 0.92em; }
  pre { padding: 12px 16px; overflow-x: auto; }
  a { color: var(--primary); }
  table { border-collapse: collapse; width: 100%; margin: 12px 0 20px;
    font-size: 0.94rem; }
  th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border);
    vertical-align: top; word-break: break-word; }
  th { background: var(--surface-sink); color: var(--muted); font-weight: 600;
    font-size: 0.82rem; }
  .muted { color: var(--muted); font-size: 0.9em; }
  .meta { color: var(--muted); font-size: 0.92rem; margin: 0 0 12px; }
  .type-chip { display: inline-block; background: var(--primary-soft);
    border: 1px solid var(--primary); color: var(--fg); border-radius: 999px;
    padding: 2px 12px; font-size: 0.82rem; margin-right: 6px; }
  .iri-box { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
    justify-content: space-between; background: var(--surface-alt);
    border: 1px solid var(--border); border-left: 4px solid var(--primary);
    border-radius: var(--radius-sm); padding: 12px 16px; margin: 16px 0;
    word-break: break-all; }
  .iri-box code { background: transparent; padding: 0; }
  button.copy { font: inherit; font-size: 0.85rem; cursor: pointer;
    background: var(--surface); color: var(--primary); border: 1px solid var(--primary);
    border-radius: var(--radius-sm); padding: 4px 12px; white-space: nowrap; }
  .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 24px; }
  .actions a { display: inline-block; background: var(--primary); color: #fff;
    border-radius: var(--radius-sm); padding: 10px 18px; text-decoration: none;
    font-weight: 600; font-size: 0.95rem; }
  .actions a.secondary { background: var(--surface); color: var(--primary);
    border: 1px solid var(--primary); }
  details { margin-top: 20px; border-top: 1px solid var(--border); padding-top: 12px; }
  summary { cursor: pointer; color: var(--muted); font-size: 0.9rem; }
"""


def _shell(*, lang: str, title: str, body: str, script: str = "") -> str:
    tail = f"<script>{script}</script>" if script else ""
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
<div class="page">
{body}
</div>
{tail}
</body>
</html>
"""


def _topbar(lang: str, dataset: dict[str, str] | None = None) -> str:
    """Never leave the reader without a way back into the app (ADR K11)."""
    t = _t(lang)
    links = [f'<a href="/">{html.escape(t["home"])}</a>']
    if dataset and dataset.get("dataset_id"):
        href = html.escape(_urlquote(dataset["dataset_id"]), quote=True)
        links.append(
            f'<a href="/#/datasets/{href}">{html.escape(t["view_dataset"])}</a>'
        )
    return f'<div class="topbar">{"".join(links)}</div>'


def _exits(lang: str, *, ask: bool = True) -> str:
    t = _t(lang)
    parts = [f'<a href="/#/datasets">{html.escape(t["browse"])}</a>']
    if ask:
        parts.append(f'<a class="secondary" href="/#/ask">{html.escape(t["ask"])}</a>')
    return f'<div class="actions">{"".join(parts)}</div>'


def _iri_box(iri: str, lang: str) -> str:
    t = _t(lang)
    return (
        '<div class="iri-box"><div><span class="muted">'
        f'{html.escape(t["id_label"])}</span><br /><code>{html.escape(iri)}</code>'
        '</div><button type="button" class="copy" '
        f'data-copy="{html.escape(iri, quote=True)}">{html.escape(t["copy"])}</button>'
        "</div>"
    )


def _copy_script(lang: str) -> str:
    t = _t(lang)
    return _COPY_SCRIPT.replace("__COPY__", t["copy"]).replace("__COPIED__", t["copied"])


def render_html(
    iri: str,
    data: dict[str, Any],
    *,
    lang: str = "ja",
    graph_info: dict[str, dict[str, str]] | None = None,
    iri_base: str | None = None,
) -> str:
    """The human view: what is known about this ID, in the かんたん vocabulary.

    ``graph_info`` maps a source graph IRI to ``{"name", "dataset_id"}`` — the
    caller resolves it (this module stays free of registry dependencies).
    """
    t = _t(lang)
    ctx = _Ctx(
        lang=lang,
        labels=data.get("labels") or {},
        bases=local_bases(iri, iri_base),
        graph_info=graph_info or {},
    )
    own_label = _pick_label(ctx.labels.get(iri), lang) or data["label"]
    type_names = [
        _pick_label(ctx.labels.get(term), lang) or _local(term)
        for term in data["types"]
    ]
    if own_label:
        title = own_label
    elif type_names:
        title = f"{type_names[0]} {_local(iri)}"
    else:
        title = _local(iri)

    dataset = _primary_dataset(data, ctx)
    meta_bits = []
    if dataset.get("name"):
        meta_bits.append(f'{t["dataset_label"]}: {dataset["name"]}')
    if type_names:
        meta_bits.append(f'{t["kind_label"]}: {"・".join(type_names)}')
    meta_line = (
        f'<p class="meta">{html.escape(" ／ ".join(meta_bits))}</p>'
        if meta_bits
        else ""
    )
    chips = "".join(
        f'<span class="type-chip" title="{html.escape(term, quote=True)}">'
        f"{html.escape(name)}</span>"
        for term, name in zip(data["types"], type_names, strict=True)
    )
    chips_html = f"<p>{chips}</p>" if chips else ""

    show_source = any(
        (ctx.graph_info.get(r["g"]["value"]) or {}).get("name")
        for r in list(data["outbound"]) + list(data["inbound"])
    )

    def _row(cells: list[str], graph_iri: str) -> str:
        body = "".join(f"<td>{c}</td>" for c in cells)
        if show_source:
            body += f"<td>{_source_cell(graph_iri, ctx)}</td>"
        return f"<tr>{body}</tr>"

    def _head(labels: list[str]) -> str:
        cols = list(labels) + ([t["th_source"]] if show_source else [])
        cells = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
        return f"<thead><tr>{cells}</tr></thead>"

    def _already_above(row: dict[str, Any]) -> bool:
        """True when this row is *literally* the heading or a type chip already
        rendered above the table.

        Repeating them would spend the table's first (most-read) rows on
        information the reader just saw, in the technical notation this page
        exists to avoid. Only the exact terms shown above are dropped — an
        alternate name or an unrendered type still reaches the page, so nothing
        the data says disappears.
        """
        p, o = row["p"]["value"], row["o"]
        if p == _TYPE_PREDICATE and o["type"] == "uri":
            return o["value"] in set(data["types"])
        if p in _LABEL_PREDICATES and o["type"] == "literal":
            return o["value"] == own_label
        return False

    # Values the reader came for first; the pipeline's own bookkeeping folded away.
    value_rows: list[dict] = []
    record_rows: list[dict] = []
    for r in data["outbound"]:
        if _already_above(r):
            continue
        bucket = (
            record_rows
            if r["p"]["value"].startswith(_RECORD_NAMESPACES)
            else value_rows
        )
        bucket.append(r)
    if not value_rows:  # nothing but bookkeeping: better shown than an empty table
        value_rows, record_rows = record_rows, []

    def _out_table(rows: list[dict]) -> str:
        body = "".join(
            _row(
                [_item_cell(r["p"]["value"], ctx), _term_cell(r["o"], ctx)],
                r["g"]["value"],
            )
            for r in rows
        )
        return (
            f"<table>{_head([t['th_item'], t['th_value']])}"
            f"<tbody>{body}</tbody></table>"
        )

    out_note = (
        f'<p class="muted">{html.escape(t["out_note"].format(n=OUTBOUND_LIMIT))}</p>'
        if data["out_truncated"]
        else ""
    )
    in_note = (
        f'<p class="muted">{html.escape(t["in_note"].format(n=INBOUND_LIMIT))}</p>'
        if data["in_truncated"]
        else ""
    )
    # When the only outbound rows were the heading and the chips, say so in one
    # sentence rather than printing an empty table under a promising heading.
    if value_rows:
        values_section = (
            f"<h2>{html.escape(t['statements'])}</h2>{out_note}{_out_table(value_rows)}"
        )
    elif data["outbound"]:
        values_section = (
            f"<h2>{html.escape(t['statements'])}</h2>"
            f'<p class="muted">{html.escape(t["only_meta"])}</p>'
        )
    else:  # only referenced by others: the inbound section carries the page
        values_section = ""
    records_section = (
        f"<details><summary>{html.escape(t['records'])}</summary>"
        f"{_out_table(record_rows)}</details>"
        if record_rows
        else ""
    )
    in_body = "".join(
        # Inbound subjects provably have statements here, so they always link.
        _row(
            [
                _iri_cell(r["s"]["value"], ctx, force_link=True),
                _item_cell(r["p"]["value"], ctx),
            ],
            r["g"]["value"],
        )
        for r in data["inbound"]
    )
    inbound_section = (
        f"<h2>{html.escape(t['inbound'])}</h2>{in_note}"
        f"<table>{_head([t['th_from'], t['th_item']])}"
        f"<tbody>{in_body}</tbody></table>"
        if in_body
        else ""
    )
    tech = (
        f"<details><summary>{html.escape(t['tech'])}</summary>"
        f'<p class="muted">{html.escape(t["machine"])}</p>'
        '<pre><code>curl -H "Accept: text/turtle" &lt;this URL&gt;</code></pre>'
        "</details>"
    )
    body = (
        f"{_topbar(lang, dataset)}"
        f'<div class="card">'
        f"<h1>{html.escape(title)}</h1>{meta_line}{chips_html}"
        f"{_iri_box(iri, lang)}"
        f'<p class="muted">{html.escape(t["intro"])}</p>'
        f"{values_section}"
        f"{records_section}{inbound_section}{tech}"
        f"</div>"
        f"{_exits(lang)}"
    )
    return _shell(
        lang=lang,
        title=f"{html.escape(title)} — {t['brand']}",
        body=body,
        script=_copy_script(lang),
    )


def _message_page(
    *, lang: str, page_title: str, heading: str, body_text: str, iri: str | None,
    hint: str = "",
) -> str:
    parts = [f"<h1>{html.escape(heading)}</h1>"]
    if iri:
        parts.append(_iri_box(iri, lang))
    parts.append(f"<p>{html.escape(body_text)}</p>")
    if hint:
        parts.append(f'<p class="muted">{html.escape(hint)}</p>')
    body = (
        f"{_topbar(lang)}"
        f'<div class="card">{"".join(parts)}</div>'
        f"{_exits(lang)}"
    )
    return _shell(
        lang=lang,
        title=f"{html.escape(page_title)} — {_t(lang)['brand']}",
        body=body,
        script=_copy_script(lang) if iri else "",
    )


def render_not_found(
    iri: str, published_graphs: int = 0, *, lang: str = "ja"
) -> str:
    """404 body: says the one thing the reader needs (it is not published yet)
    and always offers a way onward. ``published_graphs`` is kept for callers but
    deliberately not printed — a graph count explains nothing to a reader."""
    t = _t(lang)
    return _message_page(
        lang=lang,
        page_title=t["nf_title"],
        heading=t["nf_heading"],
        body_text=t["nf_body"],
        iri=iri,
        hint=t["nf_hint"],
    )


def render_bad_request(iri: str | None, *, lang: str = "ja") -> str:
    """400 body for a browser: a broken/garbled citation URL is a normal event
    (copy-paste), so it gets a page rather than a raw JSON error."""
    t = _t(lang)
    return _message_page(
        lang=lang,
        page_title=t["bad_title"],
        heading=t["bad_heading"],
        body_text=t["bad_body"],
        iri=iri if iri and len(iri) <= 2048 else None,
    )


def render_upstream_error(*, lang: str = "ja") -> str:
    """502 body for a browser: transient, so the advice is "try again"."""
    t = _t(lang)
    return _message_page(
        lang=lang,
        page_title=t["err_title"],
        heading=t["err_heading"],
        body_text=t["err_body"],
        iri=None,
    )
