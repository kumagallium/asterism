"""Attach grounding candidates to a PROPOSED schema (external-standard-alignment.md §8).

When the AI drafts a schema (step0 propose), this surfaces — for each class/predicate
the design would MINT under its own namespace — the matching famous-standard candidates
(``cmso:CrystalStructure`` etc.), so the designer can see "your data could lean on this
standard". The candidates come ONLY from the deterministic, closed-set grounding search
(:func:`asterism.grounding.catalog.ground_terms`) — never from the LLM's memory — so
nothing is fabricated; adopting one stays a human action (in the catalog, via an
alignment). Terms already under a known external namespace (``schema:``/``prov:`` …) are
reused already and are skipped.

The structured source is the rdf-config ``model.yaml`` (the propose §6 artifact): a list
of entities, each ``a: <prefix:Class>`` plus ``<prefix:predicate>:`` keys. We only need
each term's LOCAL name + kind to query the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from asterism.grounding.catalog import Candidate, ground_terms, load_catalog

# Structural prefixes never worth grounding (carry no domain vocabulary signal).
_STRUCTURAL_PREFIXES = frozenset({"rdf", "rdfs", "owl", "xsd"})

# Only surface reasonably-confident candidates: this keeps exact / token / substring
# matches (catalog.py score tiers) and drops the weak word-overlap tier, which is mostly
# noise for a suggestion panel (e.g. "hasCrystalStructure" ~ "hasUnit").
_MIN_SCORE = 40


@dataclass(frozen=True)
class SchemaTermGrounding:
    """One proposed (minted) term + the standard candidates it could reuse/align to."""

    name: str
    kind: str  # "class" | "property"
    source_curie: str
    candidates: list[Candidate]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "source_curie": self.source_curie,
            "candidates": [c.to_dict() for c in self.candidates],
        }


def _known_prefixes() -> frozenset[str]:
    """Prefixes already covered by a known external vocabulary (so a term under one is
    reused, not minted) plus the structural ones."""
    return frozenset(v.prefix for v in load_catalog()) | _STRUCTURAL_PREFIXES


def _split_curie(curie: str) -> tuple[str, str]:
    """``"mp:CrystalStructure"`` -> ``("mp", "CrystalStructure")``. Drops a trailing
    rdf-config optional marker ("?"); returns ``("", local)`` when there is no prefix."""
    s = curie.strip().rstrip("?").strip()
    if ":" in s:
        prefix, local = s.split(":", 1)
        return prefix.strip(), local.strip()
    return "", s


def _collect_terms(model_yaml: str) -> list[tuple[str, str, str]]:
    """(kind, prefix, local) for every minted class/predicate in the model.yaml, in
    first-seen order, deduped. Classes come from ``a:`` types, predicates from the
    property keys."""
    try:
        data = yaml.safe_load(model_yaml)
    except yaml.YAMLError:
        return []
    if not isinstance(data, list):
        return []
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str]] = []

    def add(kind: str, curie: str) -> None:
        prefix, local = _split_curie(curie)
        if not local:
            return
        key = (kind, prefix, local)
        if key not in seen:
            seen.add(key)
            out.append(key)

    for entity in data:
        if not isinstance(entity, dict):
            continue
        for props in entity.values():
            if not isinstance(props, list):
                continue
            for prop in props:
                if not isinstance(prop, dict):
                    continue
                for pkey, pval in prop.items():
                    if pkey == "a":  # rdf:type — the entity's class(es)
                        for v in pval if isinstance(pval, list) else [pval]:
                            if isinstance(v, str):
                                add("class", v)
                    else:  # a predicate
                        add("property", str(pkey))
    return out


def ground_model_yaml(
    model_yaml: str, *, per_term_limit: int = 3, min_score: int = _MIN_SCORE
) -> list[SchemaTermGrounding]:
    """Grounding candidates for each MINTED term in an rdf-config ``model.yaml``.

    Skips terms already under a known external namespace (reused) and structural ones,
    keeps only candidates scoring at least ``min_score`` (drops weak word-overlap noise),
    and omits terms left with no candidate. Deterministic + closed-set — every candidate
    is a real catalog IRI (a human still confirms by adopting it as an alignment)."""
    known = _known_prefixes()
    out: list[SchemaTermGrounding] = []
    for kind, prefix, local in _collect_terms(model_yaml):
        if prefix in known:  # already reused / structural
            continue
        candidates = [
            c for c in ground_terms(local, kind=kind, limit=per_term_limit) if c.score >= min_score
        ]
        if candidates:
            curie = f"{prefix}:{local}" if prefix else local
            out.append(
                SchemaTermGrounding(
                    name=local, kind=kind, source_curie=curie, candidates=candidates
                )
            )
    return out
