"""Crosswalk HUB builder — a thin, GROWING bridge across datasets (ADR
``docs/architecture/crosswalk-hub.md``).

Not a field-wide "ultimate ontology" and not N^2 pairwise bridges: a thin hub with
a few shared CONCEPTS (e.g. ``xw:Composition``). Each participating dataset declares
"my ``<predicate>`` carries this concept's value" (a :class:`Rule`); the builder
mints ONE shared entity per *normalized* value shared by >= ``min_datasets`` datasets
and links each dataset's entities to it (``<link_predicate>``). N datasets map into
ONE hub; adding a dataset (a rule) grows the SAME hub.

This module is PURE and MULTI-CONCEPT: it takes *observations* (``(entity, raw value)``
per concept+dataset) and returns the hub Turtle + per-concept stats + build
provenance. All I/O (reading the store, writing the named graph, the control flag)
is the caller's job, so this is unit-testable without a triplestore and reusable by
the substrate / api / a CLI. The trust model is the Tier-0 one: the normalization
(the join key) is a vetted, named function; nothing is generated at runtime.
"""
from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field

# Crosswalk namespaces (stable — see the rename invariant in CLAUDE.md).
XW = "https://kumagallium.github.io/asterism/crosswalk/ontology#"
XW_RESOURCE = "https://kumagallium.github.io/asterism/crosswalk/resource/"
PROV = "http://www.w3.org/ns/prov#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
XSD = "http://www.w3.org/2001/XMLSchema#"

_SUBS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def normalize_composition(value: str) -> str:
    """Composition join key: fold unicode subscripts -> ascii, strip whitespace.

    Conservative on purpose — keeps element case (``Co`` != ``CO``) and does NOT
    reorder elements (``Bi2Te3`` != ``Te3Bi2`` for now; an order-canonical key is a
    future, separately-vetted normalizer). The key is the CLAIM that two raw strings
    denote the same composition; over-normalizing would wrongly merge distinct ones.
    """
    return value.translate(_SUBS).replace(" ", "")


def normalize_identity(value: str) -> str:
    """Exact-match key (only whitespace-trimmed). For values already canonical."""
    return value.strip()


# Named normalizers (a step toward Tier-0 functions): a concept references one by
# name, so the join key is explicit, vetted, and recorded in provenance.
NORMALIZERS = {
    "composition": normalize_composition,
    "identity": normalize_identity,
}


@dataclass(frozen=True)
class Rule:
    """One dataset's participation in a concept: which predicate carries the value."""

    dataset: str
    predicate: str


@dataclass(frozen=True)
class Concept:
    """A shared hub concept (e.g. composition): a class + a link predicate + the
    per-dataset rules that map into it + the normalizer that is its join key."""

    name: str
    class_iri: str
    link_predicate: str
    normalizer: str = "identity"
    rules: tuple[Rule, ...] = ()

    def resource_base(self) -> str:
        return f"{XW_RESOURCE}{self.name}/"

    def datasets(self) -> list[str]:
        return sorted({r.dataset for r in self.rules})


@dataclass(frozen=True)
class CrosswalkConfig:
    """The whole growing hub: a set of shared concepts. Adding a dataset = adding a
    Rule to a concept (or a new concept). Adding a concept = a new shared axis."""

    concepts: tuple[Concept, ...]
    min_datasets: int = 2
    # Per-link provenance: record, for EACH crosswalk link, the raw string that was
    # normalized and the normalizer that produced the join key (a ``xw:CrosswalkLink``
    # node). The link is the unit that must be vetted ("I claim sd:s1, raw 'Bi₂Te₃',
    # is the same composition as mp:m1, raw 'Bi2Te3', because normalizer 'composition'
    # maps both to 'Bi2Te3'"), so the raw spelling is the audit-relevant fact. Off =>
    # provenance stays per build Activity only (the prior, lighter model).
    per_link_provenance: bool = True


@dataclass
class CrosswalkBuild:
    """Result of a build: the hub Turtle + per-concept shared keys + link counts."""

    turtle: str
    shared: dict[str, list[str]] = field(default_factory=dict)
    links: dict[str, dict[str, int]] = field(default_factory=dict)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


# observations[(concept_name, dataset_label)] -> iterable of (entity_iri, raw_value)
Observations = dict[tuple[str, str], Iterable[tuple[str, str]]]


def build_turtle(
    config: CrosswalkConfig,
    observations: Observations,
    *,
    activity_iri: str,
    built_at: str,
) -> CrosswalkBuild:
    """Build the hub graph Turtle from observations (pure, multi-concept).

    For each concept: normalize every dataset's raw values with the concept's
    normalizer, mint one shared entity per value present in >= ``min_datasets``
    datasets, and emit a crosswalk link for each entity of a shared value. Records a
    ``prov:Activity`` (participating datasets, time); every minted entity is
    ``prov:wasGeneratedBy`` it (the hub is a derived, dated claim). With
    ``config.per_link_provenance`` (default on), every link also gets a
    ``xw:CrosswalkLink`` node recording the *raw* string it normalized and the
    normalizer used — so each cross-dataset join is independently auditable.
    """
    all_datasets = sorted({r.dataset for c in config.concepts for r in c.rules})
    lines = [
        f"@prefix xw: <{XW}> .",
        f"@prefix rdfs: <{RDFS}> .",
        f"@prefix owl: <{OWL}> .",
        f"@prefix prov: <{PROV}> .",
        "",
        "# --- build provenance (the crosswalk is a derived, dated claim) ---",
        f'<{activity_iri}> a prov:Activity ; rdfs:label "crosswalk hub build" ; '
        f'xw:participatingDatasets "{_esc(", ".join(all_datasets))}" ; '
        f'prov:endedAtTime "{built_at}"^^<{XSD}dateTime> .',
        "",
    ]
    build = CrosswalkBuild(turtle="")
    for concept in config.concepts:
        normalize = NORMALIZERS.get(concept.normalizer, normalize_identity)
        # per dataset: normalized value -> [(entity IRI, raw value)]. Keep the raw
        # spelling (not just the entity) so per-link provenance can record what was
        # normalized — the audit-relevant fact for the join claim.
        per_ds: dict[str, dict[str, list[tuple[str, str]]]] = {}
        for rule in concept.rules:
            bucket = per_ds.setdefault(rule.dataset, {})
            for entity, raw in observations.get((concept.name, rule.dataset), []):
                bucket.setdefault(normalize(raw), []).append((entity, raw))
        # shared = a value present in >= min_datasets participating datasets
        counts: dict[str, int] = {}
        for bucket in per_ds.values():
            for key in bucket:
                counts[key] = counts.get(key, 0) + 1
        shared = sorted(k for k, n in counts.items() if n >= config.min_datasets)
        build.shared[concept.name] = shared
        build.links[concept.name] = {}

        lines.append(f"# --- concept: {concept.name} (normalizer: {concept.normalizer}) ---")
        lines.append(
            f'<{concept.class_iri}> a owl:Class ; '
            f'rdfs:label "{_esc(concept.name)} (crosswalk)" .'
        )
        base = concept.resource_base()
        for key in shared:
            iri = f"{base}{urllib.parse.quote(key, safe='')}"
            lines.append(
                f'<{iri}> a <{concept.class_iri}> ; rdfs:label "{_esc(key)}" ; '
                f"prov:wasGeneratedBy <{activity_iri}> ."
            )
            for dataset in all_datasets:
                for entity, raw in per_ds.get(dataset, {}).get(key, []):
                    lines.append(f"<{entity}> <{concept.link_predicate}> <{iri}> .")
                    build.links[concept.name][dataset] = (
                        build.links[concept.name].get(dataset, 0) + 1
                    )
                    if config.per_link_provenance:
                        # Deterministic link-node IRI per (key, entity): the link is
                        # the unit of provenance — "<entity> was joined to this shared
                        # composition by normalizing <raw> with <normalizer>".
                        link_iri = (
                            f"{base}link/{urllib.parse.quote(key, safe='')}/"
                            f"{urllib.parse.quote(entity, safe='')}"
                        )
                        lines.append(
                            f"<{link_iri}> a xw:CrosswalkLink ; "
                            f"xw:linkSubject <{entity}> ; xw:linkObject <{iri}> ; "
                            f'xw:sourceValue "{_esc(raw)}" ; '
                            f'xw:normalizer "{_esc(concept.normalizer)}" ; '
                            f"prov:wasGeneratedBy <{activity_iri}> ."
                        )
        lines.append("")
    build.turtle = "\n".join(lines) + "\n"
    return build
