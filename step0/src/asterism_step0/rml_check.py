"""T9 — closed-set check for declarative RML mappings.

The whole point of the declarative substrate (Phase 5) is that an AI-authored
mapping can reference only a **closed, once-vetted set of functions** — never
new code. This module enforces that property on a piece of RML/FnO Turtle:
every ``rmlf:function`` object must be one of the allowed Tier 0 function IRIs.
See ``docs/architecture/step0-rml-emission.md`` §5.2.

Design: the core check is a **pure function** that takes the allowed IRI set as
an argument, so it is fully testable without importing the ``asterism`` (ingest)
package. :func:`load_registry_fn_iris` is a best-effort bridge that derives the
canonical set from ``asterism.functions.REGISTRY`` when that package is available
(e.g. in CI / the monorepo) — keeping a single source of truth.
"""
from __future__ import annotations

import sys

# FnO namespace shared with ``asterism.functions.FN`` (data identity — keep stable).
FN_NAMESPACE = "https://kumagallium.github.io/asterism/fn/"
# The R2RML-FnO predicate naming the function to execute (``rmlf:function``).
RMLF_FUNCTION = "http://w3id.org/rml/function"


def referenced_function_iris(rml_ttl: str) -> set[str]:
    """Parse an RML/FnO Turtle string and return every ``rmlf:function`` object IRI.

    Raises ``ImportError`` if rdflib is unavailable, or a parse error from rdflib
    if the Turtle is malformed (callers that also run the T-syntax check will have
    already caught that).
    """
    import rdflib

    g = rdflib.Graph()
    g.parse(data=rml_ttl, format="turtle")
    pred = rdflib.URIRef(RMLF_FUNCTION)
    return {str(o) for o in g.objects(predicate=pred)}


def closed_set_violations(rml_ttl: str, allowed_fn_iris: set[str]) -> list[str]:
    """Return the referenced function IRIs that are NOT in ``allowed_fn_iris``.

    Empty list ⇒ the mapping is closed over the allowed set (T9 passes). The
    result is sorted for deterministic reporting.
    """
    used = referenced_function_iris(rml_ttl)
    return sorted(used - allowed_fn_iris)


def load_registry_fn_iris() -> set[str]:
    """Best-effort: derive the allowed function IRI set from ``asterism.functions``.

    Returns ``{FN + name for each REGISTRY spec}``. Raises ``ImportError`` when the
    ``asterism`` (ingest) package is not importable in the current environment —
    callers should treat that as "T9 skipped" (like the opt-in T8), not a failure.
    """
    from asterism.functions import REGISTRY  # type: ignore[import-not-found]

    return {spec.fun_id for spec in REGISTRY}


def check_rml_closed_set(rml_ttl: str) -> list[str]:
    """Convenience: check ``rml_ttl`` against the live ``asterism.functions`` REGISTRY.

    Returns the list of out-of-set function IRIs (empty ⇒ pass). Propagates
    ``ImportError`` if ``asterism`` is unavailable (caller decides skip vs fail).
    """
    return closed_set_violations(rml_ttl, load_registry_fn_iris())


def _main(argv: list[str] | None = None) -> int:
    """``python -m asterism_step0.rml_check <mapping.rml.ttl>`` → exit 1 on violations."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write("usage: python -m asterism_step0.rml_check <mapping.rml.ttl>\n")
        return 2
    with open(args[0], encoding="utf-8") as f:
        rml_ttl = f.read()
    try:
        violations = check_rml_closed_set(rml_ttl)
    except ImportError:
        sys.stderr.write("T9 skipped: asterism (ingest) not importable in this env.\n")
        return 0
    if violations:
        sys.stderr.write("T9 FAIL — RML references out-of-set functions:\n")
        for iri in violations:
            sys.stderr.write(f"  {iri}\n")
        return 1
    sys.stdout.write("T9 OK — RML references only Tier 0 functions.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
