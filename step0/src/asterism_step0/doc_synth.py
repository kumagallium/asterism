"""Deterministic synthesis of the design document's prose sections from §9.

Why this module exists
----------------------
Round-0 produces two very different kinds of output. §9 (the Mapping IR) is the
DESIGN: everything that becomes RDF — classes, keys, predicates, columns — and
it is generated in a small guided-JSON shape a weak model can finish. §1-§8 are
the human-readable write-up ABOUT that design, and they are long free prose:
exactly what a weak model runs out of tokens on (live: Kimi cut off at the
provider's 600 s ceiling, Qwen truncated mid-section).

Losing the prose used to lose the whole run — the staged proposer had no
``try`` around the document call, and ``MaterializeResult.complete`` requires
all four core artifacts, so a missing §6/§7/§8 became the S5 stop card "the
AI's design output was cut off" whose only exits are another full-document LLM
round (which truncates again) or the detail tier.

Nothing in §1-§8 is a fact the machine does not already hold: the diagram is
already compiled from the IR, §2/§3 are projections of the IR, §6 is the same
class/property set in rdf-config's shape, §7's keywords are derived by the T4
recipe, and §8 is an unexecuted sketch. So when the model cannot write them,
the machine writes them — LLM-free, from the IR alone.

Two hard rules, both from the "citable facts" invariant:

* **Nothing is invented.** Every identifier, column, class and predicate here
  comes out of the IR verbatim. No example row values, no ``sample_rdf_entries``
  (T6 only warns when they are missing — and a fabricated example would be a
  fact with no source), no design rationale attributed to a model that never
  wrote one.
* **The synthesis is announced.** Callers surface it as an informational note,
  never silently — the rules tab must be able to say which sections a human
  (or an AI) did not write.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

__all__ = [
    "keyword_candidates",
    "query_probe",
    "synthesize_document",
    "synthesize_ingester_py",
    "synthesize_mie_yaml",
    "synthesize_model_yaml",
]

# Section headings the materialize extractor matches on (keyword-based, see
# ``materialize._MODEL_HEADERS`` & co). Synthesized blocks MUST land under a
# heading materialize will find again, or a re-materialize would drop them.
_H_MODEL = "### 6. rdf-config model.yaml"
_H_MIE = "### 7. MIE YAML extras"
_H_INGESTER = "### 8. Ingester sketch"

_MAX_QUERY_EXAMPLES = 5
_MAX_KEYWORDS = 8

_SLUG_RE = re.compile(r"[^0-9A-Za-z]+")


def _local(term: str) -> str:
    """CURIE/IRI -> local name (``xr:Peak`` -> ``Peak``)."""
    tail = re.split(r"[:#/]", str(term).strip())[-1]
    return tail or str(term).strip()


def _expand(term: str, prefixes: Mapping[str, str]) -> str:
    """CURIE -> full IRI through the IR's prefix map; full IRIs pass through."""
    text = str(term).strip()
    if text.startswith(("http://", "https://")):
        return text
    if ":" in text:
        prefix, local = text.split(":", 1)
        base = prefixes.get(prefix)
        if base is not None:
            return f"{base}{local}"
    return text


def _py_ident(name: str) -> str:
    """A safe Python identifier from a map name (``peak data`` -> ``peak_data``)."""
    out = _SLUG_RE.sub("_", str(name).strip()).strip("_").lower()
    if not out or out[0].isdigit():
        out = f"m_{out}" if out else "records"
    return out


def _iter_properties(ir: object) -> Iterable[tuple[object, object]]:
    for m in getattr(ir, "maps", ()) or ():
        for p in getattr(m, "properties", ()) or ():
            yield m, p


# ---------------------------------------------------------------------------
# §7 raw material: keyword candidates (T4) and runnable example queries (T10)
# ---------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    """Word tokens of a name: separators AND camelCase boundaries.

    ``Resistivity(Ohm m)`` → ``Resistivity``, ``Ohm``, ``m``;
    ``sampleId`` → ``sample``, ``Id``. A compound name is several search terms,
    all of them still the design's own words.
    """
    parts: list[str] = []
    for chunk in _SLUG_RE.split(str(text)):
        if not chunk:
            continue
        parts += re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk) or [chunk]
    return parts


def keyword_candidates(ir: object, *, dataset_name: str | None = None) -> list[str]:
    """Search terms derived from the design itself, in source-priority order.

    Same principle as the T4 fix recipe (``validate._t4_candidate_terms``): the
    dataset's own name, its class local names, its map names, its source stems,
    its column headers and the words inside them — never a vocabulary of any
    scientific field, so the result stays domain-independent. The source format
    comes last: a weak term, but a true one, and it is what lets a one-class
    one-column design still reach the five tags discovery needs.
    """
    terms: list[str] = []
    if dataset_name:
        terms += _tokens(dataset_name)
    formats: list[str] = []
    for m in getattr(ir, "maps", ()) or ():
        subject = getattr(m, "subject", None)
        for cls in getattr(subject, "classes", ()) or ():
            terms.append(_local(cls))
            terms += _tokens(_local(cls))
        name = getattr(m, "name", None)
        if name:
            terms.append(str(name))
        source = getattr(m, "source", None)
        if source:
            stem, _, suffix = str(source).rpartition(".")
            terms.append(stem or str(source))
            terms += _tokens(stem or str(source))
            if suffix:
                formats.append(suffix.lower())
    for _m, p in _iter_properties(ir):
        column = getattr(p, "column", None)
        if column:
            terms.append(str(column))
            terms += _tokens(str(column))
    return terms + formats


def query_probe(ir: object) -> str:
    """One broad, always-runnable query over something this mapping emits.

    The replacement for an example query that does not run (T10). Derived from
    the design, so an agent imitating it sees a shape the data really has.
    """
    return _query_examples(ir)[0]["query"]


def _query_examples(ir: object) -> list[dict[str, str]]:
    """Runnable §7 examples built ONLY from what the mapping actually emits.

    Full IRIs (no ``PREFIX`` header) so each query parses standalone, and every
    class/predicate is one this IR writes — so the examples return rows against
    the draft instead of teaching an agent a shape that does not exist (the
    2026-06-01 stale-example incident).
    """
    prefixes = dict(getattr(ir, "prefixes", {}) or {})
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in getattr(ir, "maps", ()) or ():
        subject = getattr(m, "subject", None)
        for cls in getattr(subject, "classes", ()) or ():
            iri = _expand(cls, prefixes)
            if iri in seen:
                continue
            seen.add(iri)
            out.append(
                {
                    "title": f"List {_local(cls)} records",
                    "description": f"Every {_local(cls)} the dataset contains.",
                    "query": f"SELECT ?s WHERE {{ ?s a <{iri}> }} LIMIT 20",
                }
            )
            if len(out) >= _MAX_QUERY_EXAMPLES - 1:
                break
        if len(out) >= _MAX_QUERY_EXAMPLES - 1:
            break
    for _m, p in _iter_properties(ir):
        predicate = getattr(p, "predicate", None)
        if not predicate:
            continue
        iri = _expand(predicate, prefixes)
        if iri in seen:
            continue
        seen.add(iri)
        out.append(
            {
                "title": f"Values of {_local(predicate)}",
                "description": f"Each record with its {_local(predicate)} value.",
                "query": f"SELECT ?s ?value WHERE {{ ?s <{iri}> ?value }} LIMIT 20",
            }
        )
        break
    if not out:
        out.append(
            {
                "title": "Inspect typed records",
                "description": "One row per record with its class.",
                "query": "SELECT ?s ?type WHERE { ?s a ?type } LIMIT 20",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Section synthesizers
# ---------------------------------------------------------------------------


def synthesize_model_yaml(ir: object) -> str:
    """§6 rdf-config ``model.yaml``, projected from the IR.

    The subject TEMPLATE stands in for rdf-config's example IRI — it is what the
    design says, whereas a rendered example ID would be a value with no source.
    Property entries carry their variable name only, for the same reason.
    """
    import yaml  # lazy — PyYAML is the compiler's dependency, not this module's

    prefixes = dict(getattr(ir, "prefixes", {}) or {})
    blocks: list[str] = []
    for m in getattr(ir, "maps", ()) or ():
        subject = getattr(m, "subject", None)
        classes = list(getattr(subject, "classes", ()) or [])
        template = getattr(subject, "template", None) or getattr(subject, "constant", None) or ""
        name = _local(classes[0]) if classes else _py_ident(str(getattr(m, "name", "record")))
        lines = [f"- {name} <{_expand(str(template), prefixes)}>:"]
        for cls in classes:
            lines.append(f"    - a: {cls}")
        used: set[str] = set()
        for p in getattr(m, "properties", ()) or ():
            predicate = str(getattr(p, "predicate", "") or "")
            if not predicate or predicate in used:
                continue
            used.add(predicate)
            var = _py_ident(_local(predicate))
            lines.append(f"    - {predicate}:")
            lines.append(f"        - {var}:")
        blocks.append("\n".join(lines))
    if not blocks:
        # An IR with no maps cannot happen on the synthesis path (it would not
        # have compiled), but a valid empty document beats a crash.
        return yaml.safe_dump([], sort_keys=False, allow_unicode=True).rstrip()
    return "\n".join(blocks)


def synthesize_mie_yaml(
    ir: object,
    *,
    dataset_name: str,
    existing: Mapping[str, object] | None = None,
) -> str:
    """§7 MIE extras: ``schema_info`` (T4-complete) + runnable examples (T10).

    ``existing`` is a previously parsed §7 whose non-``schema_info`` keys are
    preserved verbatim — a re-synthesis after a partial repair must not throw
    away the author's ``anti_patterns`` or their own example queries.

    ``sample_rdf_entries`` are deliberately NOT synthesized: T6 only warns when
    they are absent, and any example ID we made up would be a fact with no
    source (exactly what T6 exists to catch).
    """
    import yaml  # lazy

    from asterism_step0.validate import repair_schema_info

    document: dict[str, object] = dict(existing or {})
    schema_info = document.get("schema_info")
    document["schema_info"] = repair_schema_info(
        schema_info,
        keyword_candidates(ir, dataset_name=dataset_name),
        title_fallback=dataset_name,
    )
    if not document.get("sparql_query_examples"):
        document["sparql_query_examples"] = _query_examples(ir)
    if not document.get("architectural_notes"):
        # Honest, not flattering: no rationale was recorded because no model
        # wrote one. Inventing "Why / Alternatives / Trade-offs" here would make
        # T7 pass on a lie (T7 only warns, so there is nothing to buy).
        document["architectural_notes"] = (
            "This section was generated deterministically from the declarative "
            "mapping spec (§9); no design rationale was recorded for it."
        )
    ordered = {
        key: document[key]
        for key in ("schema_info", "sample_rdf_entries", "sparql_query_examples")
        if key in document
    }
    ordered.update({k: v for k, v in document.items() if k not in ordered})
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True).rstrip()


def synthesize_ingester_py(ir: object, *, dataset_name: str) -> str:
    """§8 ingester sketch — signatures only, ``utf-8-sig``, no blank nodes.

    The sketch is never executed (ingest runs the compiled RML), so this is
    documentation: one reader function per source, opened the BOM-safe way, with
    the map's subject template quoted as the IRI recipe. Because it is
    synthesized rather than written by a model it can never be the thing that
    fails T2 (missing ``utf-8-sig``) or T3 (a stray ``BNode(``).
    """
    prefixes = dict(getattr(ir, "prefixes", {}) or {})
    lines = [
        '"""Ingester sketch (auto-generated from the mapping spec).',
        "",
        "Reference only: the real ingest compiles §9 to RML and runs it. Every",
        "source is read with utf-8-sig so a BOM never leaks into a column name,",
        "and every record gets a template-minted IRI (never a blank node).",
        '"""',
        "",
        "import csv",
        "",
    ]
    seen_sources: set[str] = set()
    for m in getattr(ir, "maps", ()) or ():
        source = str(getattr(m, "source", "") or "")
        name = _py_ident(str(getattr(m, "name", "") or source or "records"))
        subject = getattr(m, "subject", None)
        template = getattr(subject, "template", None) or getattr(subject, "constant", None) or ""
        func = f"read_{name}"
        if func in seen_sources:
            continue
        seen_sources.add(func)
        lines += [
            f"def {func}(path):",
            f'    """Rows of {source or "the source"}; IRI recipe: '
            f'{_expand(str(template), prefixes)}"""',
            '    with open(path, encoding="utf-8-sig", newline="") as fh:',
            "        yield from csv.DictReader(fh)",
            "",
            "",
        ]
    lines += [
        f"def ingest_{_py_ident(dataset_name)}(source_dir):",
        '    """Entry point — see the compiled RML for the authoritative mapping."""',
        "    raise NotImplementedError",
        "",
    ]
    return "\n".join(lines)


def _iri_scheme_md(ir: object) -> list[str]:
    prefixes = dict(getattr(ir, "prefixes", {}) or {})
    lines = ["### 2. IRI scheme", ""]
    for name, iri in prefixes.items():
        lines.append(f"- `{name}:` → `{iri}`")
    if prefixes:
        lines.append("")
    for m in getattr(ir, "maps", ()) or ():
        subject = getattr(m, "subject", None)
        template = getattr(subject, "template", None) or getattr(subject, "constant", None) or "—"
        classes = ", ".join(f"`{c}`" for c in getattr(subject, "classes", ()) or ()) or "—"
        lines.append(f"- {classes} — `{template}` (source `{getattr(m, 'source', '')}`)")
    lines.append("")
    return lines


def synthesize_document(ir: object, ir_yaml: str, *, dataset_name: str) -> str:
    """The whole §1-§9 Markdown, deterministically, from a parsed IR.

    Used when the document stage failed outright (truncated / empty answer):
    the design itself already exists, so the run finishes instead of asking a
    model that just ran out of tokens to try the same long write-up again.
    """
    from asterism_step0.ir2mermaid import build_graph_from_ir, property_table_md
    from asterism_step0.ttl2mermaid import render_mermaid_body

    mermaid = render_mermaid_body(build_graph_from_ir(ir)).rstrip("\n")  # type: ignore[arg-type]
    table = property_table_md(ir) or ""  # type: ignore[arg-type]

    parts: list[str] = [
        "### 1. Class hierarchy",
        "",
        "```mermaid",
        mermaid,
        "```",
        "",
        *_iri_scheme_md(ir),
        "### 3. Property design",
        "",
        table.strip() or "(no properties in the mapping spec)",
        "",
        "### 4. JSON column strategy",
        "",
        "Every column is mapped as declared in §9; nested values use the vetted "
        "Tier-0 functions the spec names.",
        "",
        "### 5. Design rationale",
        "",
        "Auto-generated from the mapping spec — no AI-written rationale is "
        "recorded for this design.",
        "",
        _H_MODEL,
        "",
        "```yaml",
        synthesize_model_yaml(ir),
        "```",
        "",
        _H_MIE,
        "",
        "```yaml",
        synthesize_mie_yaml(ir, dataset_name=dataset_name),
        "```",
        "",
        _H_INGESTER,
        "",
        "```python",
        synthesize_ingester_py(ir, dataset_name=dataset_name).rstrip("\n"),
        "```",
        "",
        "### 9. Declarative mapping spec",
        "",
        "```yaml",
        ir_yaml.strip("\n"),
        "```",
        "",
    ]
    return "\n".join(parts)
