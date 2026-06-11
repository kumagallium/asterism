"""Runtime safety gate for declarative RML, enforced *before* Morph-KGC runs it.

The Phase 5 invariant (CLAUDE.md「生成コードを実行しない」,
``docs/architecture/ingestion-execution-safety.md``) is that an AI- or
operator-authored RML mapping may reference **only** the closed, once-vetted
Tier 0 function set (:mod:`asterism.functions`) and may read **only** data files
confined to the dataset's own source directory — never arbitrary code, arbitrary
SQL (Morph-KGC's DuckDB ``rml:query`` path), or arbitrary host files.

``asterism_step0`` ships an *offline* linter for the same property (trap T9), but
that runs only in CI / the design CLI. This module enforces the invariant **on
the runtime ingest path**, immediately before Morph-KGC is invoked, and it
**fails closed**: a missing dependency, an unparseable mapping, a non-Tier-0
function, a SQL/query source, or a source path that escapes the dataset dir are
all rejections — never a silent skip. The offline T9 is best-effort; this gate is
the trust boundary.
"""
from __future__ import annotations

import re
from pathlib import Path

# Predicates that name an executable function in RML/FnO. Morph-KGC resolves any
# of these against its function table (built-in GREL — which includes the
# eval-bearing ``controls_if`` family — plus registered UDFs), so the allowlist
# must cover every one of them, not just the current ``rmlf:function``.
_FUNCTION_PREDICATES: tuple[str, ...] = (
    "http://w3id.org/rml/function",  # rmlf:function (current RML-FNML)
    "http://semweb.mmlab.be/ns/fnml#function",  # legacy FnML
    "https://w3id.org/function/ontology#executes",  # fno:executes
    "http://w3id.org/function/ontology#executes",
)

# Predicates that introduce a SQL / relational / tabular-query source. Morph-KGC
# runs ``rml:query`` bodies through DuckDB (arbitrary SQL ⇒ local file read/write,
# ``COPY ... TO``, ``read_*('http://...')`` SSRF), so any of these is rejected
# outright: asterism only ingests confined CSV/JSON *files*.
_QUERY_PREDICATES: tuple[str, ...] = (
    "http://w3id.org/rml/query",
    "http://semweb.mmlab.be/ns/rml#query",
    "http://www.w3.org/ns/r2rml#sqlQuery",
    "http://w3id.org/rml/tableName",
    "http://semweb.mmlab.be/ns/rml#tableName",
    "http://www.w3.org/ns/r2rml#tableName",
)

_SOURCE_PREDICATES: tuple[str, ...] = (
    "http://w3id.org/rml/source",
    "http://semweb.mmlab.be/ns/rml#source",
)

# Only these data-file extensions may back a logical source.
#
# ``.xml`` is a *deliberate* addition for the document-ontology layer (the
# JATS/TEI full-text path, ADR ``document-ontology-layer.md``): Morph-KGC reads
# XML declaratively via ``rml:referenceFormulation ql:XPath`` — no generated
# code, same trust model as the CSV/JSON readers. The format is vetted; an XML
# logical source still passes through every other gate here (confined path,
# Tier-0-only functions, no SQL/query source), so widening the file-type
# allowlist does not widen what an RML mapping may *execute* or *reach*.
_ALLOWED_SOURCE_SUFFIXES = frozenset({".csv", ".tsv", ".json", ".xml"})

_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class RmlSafetyError(ValueError):
    """An RML mapping violated the closed-function / confined-source invariant.

    Callers on the HTTP surface map this to a ``422``; the substrate refuses to
    hand the mapping to Morph-KGC.
    """


def _allowed_function_iris() -> set[str]:
    """The Tier 0 allowlist, derived from the live ``asterism.functions`` registry.

    Raises ``ImportError`` if the registry is unavailable — the caller treats that
    as a *failure* (reject), not a skip, because the gate is the trust boundary.
    """
    from asterism.functions import REGISTRY

    return {spec.fun_id for spec in REGISTRY}


def assert_rml_safe(rml_ttl: str, csv_dir: Path | str) -> None:
    """Reject an RML mapping that would execute non-Tier-0 code or read outside ``csv_dir``.

    Fail-closed. Raises :class:`RmlSafetyError` if rdflib or ``asterism.functions``
    is unavailable, if the Turtle is unparseable, if any referenced function is
    outside the Tier 0 allowlist, if the mapping declares a SQL/query/relational
    source, or if any ``rml:source`` is a URL, is absolute, traverses out of, or is
    not a vetted-extension file strictly under ``csv_dir``. Returns ``None`` when
    the mapping is safe to materialize.
    """
    try:
        import rdflib
    except ImportError as exc:  # pragma: no cover - rdflib ships with the substrate
        raise RmlSafetyError(
            "rdflib is required to validate RML before ingestion; refusing to run "
            "an unvetted mapping."
        ) from exc

    try:
        allowed = _allowed_function_iris()
    except ImportError as exc:
        raise RmlSafetyError(
            "asterism.functions (the Tier 0 registry) is not importable; refusing "
            "to run an unvetted mapping."
        ) from exc

    graph = rdflib.Graph()
    try:
        graph.parse(data=rml_ttl, format="turtle")
    except Exception as exc:  # malformed Turtle — do not hand it to Morph-KGC
        raise RmlSafetyError(f"RML mapping is not parseable Turtle: {exc}") from exc

    # 1) Functions: every named function must be in the closed Tier 0 set.
    used: set[str] = set()
    for pred in _FUNCTION_PREDICATES:
        used |= {str(o) for o in graph.objects(predicate=rdflib.URIRef(pred))}
    violations = sorted(used - allowed)
    if violations:
        raise RmlSafetyError(
            "RML references functions outside the closed Tier 0 set: "
            + ", ".join(violations)
        )

    # 2) No SQL / query / relational sources (the Morph-KGC DuckDB path).
    for pred in _QUERY_PREDICATES:
        if next(graph.objects(predicate=rdflib.URIRef(pred)), None) is not None:
            raise RmlSafetyError(
                "RML declares a SQL/query/table source; only confined CSV/JSON file "
                "sources are allowed."
            )

    # 3) Every rml:source must be a vetted-extension file strictly under csv_dir.
    base = Path(csv_dir).resolve()
    for pred in _SOURCE_PREDICATES:
        for obj in graph.objects(predicate=rdflib.URIRef(pred)):
            if not isinstance(obj, rdflib.Literal):
                raise RmlSafetyError(
                    "RML rml:source must be a simple file-path literal; complex / "
                    "database sources are not allowed."
                )
            _assert_source_path_safe(str(obj), base)


def _assert_source_path_safe(src: str, base: Path) -> None:
    """Reject a single ``rml:source`` value that escapes ``base`` or is not a data file."""
    raw = src.strip()
    if not raw:
        raise RmlSafetyError("RML rml:source is empty.")
    if _URL_SCHEME.match(raw):
        raise RmlSafetyError(f"RML rml:source must be a local file, not a URL: {src!r}")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise RmlSafetyError(
            f"RML rml:source must be relative to the dataset source dir: {src!r}"
        )
    if ".." in candidate.parts:
        raise RmlSafetyError(f"RML rml:source must not traverse with '..': {src!r}")
    resolved = (base / candidate).resolve()
    if not resolved.is_relative_to(base):
        raise RmlSafetyError(
            f"RML rml:source resolves outside the dataset source dir: {src!r}"
        )
    if resolved.suffix.lower() not in _ALLOWED_SOURCE_SUFFIXES:
        raise RmlSafetyError(
            "RML rml:source must be a "
            f"{sorted(_ALLOWED_SOURCE_SUFFIXES)} file: {src!r}"
        )
