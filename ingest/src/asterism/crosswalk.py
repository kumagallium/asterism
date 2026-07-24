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

import re
import unicodedata
import urllib.parse
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from typing import TypeVar

# Crosswalk namespaces (stable — see the rename invariant in CLAUDE.md).
XW = "https://kumagallium.github.io/asterism/crosswalk/ontology#"
XW_RESOURCE = "https://kumagallium.github.io/asterism/crosswalk/resource/"
PROV = "http://www.w3.org/ns/prov#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
XSD = "http://www.w3.org/2001/XMLSchema#"

_SUBS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")

# A join key: a normalized value (single part) or a tuple of them (compound key).
_K = TypeVar("_K", bound=Hashable)


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


# --- Generic text normalizers (domain-neutral join keys) -----------------------
# These cover the LONG TAIL of non-materials concepts (labels, author / material /
# place names, ids). They are stateless, deterministic, and CONSERVATIVE: each only
# folds a single, well-understood text variation, so distinct strings are never
# reordered or token-dropped (no wrong merges). Domain normalizers that need real
# knowledge (composition, element_canonical) stay as separately-vetted functions —
# the closed library grows by curation (crosswalk-normalizer-recipes.md).


def normalize_casefold(value: str) -> str:
    """Case-insensitive key: Unicode case-fold + trim. For text where case is not
    meaningful (labels, author / material names). NOT for compositions — element
    case is significant there (``Co`` cobalt != ``CO`` carbon+oxygen)."""
    return value.strip().casefold()


def normalize_whitespace(value: str) -> str:
    """Whitespace-insensitive key: collapse internal runs of whitespace to one space
    and trim. For values that differ only in spacing."""
    return " ".join(value.split())


def normalize_nfkc(value: str) -> str:
    """Unicode-compatibility key: NFKC normalize (full-width <-> half-width, ligatures,
    compatibility forms) + trim. For text that mixes full-/half-width or compat
    characters (common in Japanese-authored data)."""
    return unicodedata.normalize("NFKC", value).strip()


def normalize_loose_text(value: str) -> str:
    """General fuzzy-text key: NFKC + case-fold + collapse whitespace — the domain-
    neutral "same-ish text" join key (a sensible default for non-materials concepts).
    Composes the three text folds above; still carries no domain knowledge, so it never
    reorders or drops tokens (distinct strings stay distinct)."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


# --- Declarative normalizer recipes (crosswalk-normalizer-recipes.md, tier 3) ------
# A recipe is an ORDERED list of CLOSED, vetted primitive ids applied in sequence — a
# user-composable normalizer that is DATA, not code (authored/saved like a typed query
# tool, no runtime code execution). Each primitive folds exactly one well-understood
# text variation and never reorders or drops tokens, so any composition stays safe
# (distinct strings cannot be wrongly merged). Complex, domain-knowledge normalizers
# (element_canonical) are NOT recipes — they remain separately-vetted functions a recipe
# could reference as a single step, never logic a user writes.
RECIPE_PRIMITIVES: dict[str, Callable[[str], str]] = {
    "casefold": str.casefold,
    "strip": str.strip,
    "collapse_ws": lambda s: " ".join(s.split()),
    "nfkc": lambda s: unicodedata.normalize("NFKC", s),
    "fold_subscripts": lambda s: s.translate(_SUBS),
    "remove_ws": lambda s: "".join(s.split()),
}


def apply_recipe(steps: Iterable[str], value: str) -> str:
    """Apply a recipe (ordered primitive ids) to ``value``. Raises ``ValueError`` on an
    unknown primitive — the closed set is the safety gate (no arbitrary ops)."""
    for op in steps:
        fn = RECIPE_PRIMITIVES.get(op)
        if fn is None:
            raise ValueError(
                f"unknown recipe primitive {op!r}; allowed: {sorted(RECIPE_PRIMITIVES)}"
            )
        value = fn(value)
    return value


def recipe_label(name: str, recipe: tuple[str, ...]) -> str:
    """The join-key label recorded in provenance: a readable recipe spec when a recipe
    is used (``recipe(nfkc>casefold)``), else the named normalizer."""
    return f"recipe({'>'.join(recipe)})" if recipe else name


def resolve_normalizer(name: str, recipe: tuple[str, ...] = ()) -> Callable[[str], str]:
    """The join-key function for a concept: the recipe applier when a recipe is given,
    else the named normalizer (falling back to identity for an unknown name)."""
    if recipe:
        return lambda v: apply_recipe(recipe, v)
    return NORMALIZERS.get(name, normalize_identity)


def shared_keys(
    per_dataset_keys: Iterable[Iterable[_K]],
    *,
    min_datasets: int = 2,
) -> set[_K]:
    """The join keys reported by >= ``min_datasets`` DATASETS.

    This is the hub's join core: :func:`build_turtle` mints one shared entity per
    normalized key that ``min_datasets`` or more datasets report — a threshold, NOT a
    requirement that every participant agree. Counting is per dataset (a key repeated
    within one dataset still counts once), which is exactly the predicate the builder
    applies. Extracted so the same intersection can be computed WITHOUT building
    (discovery scores candidate joins with it, and must report the number a build
    would actually produce).

    ``per_dataset_keys`` is a POSITIONAL sequence of key collections, not a mapping by
    label: :func:`asterism.crosswalk_runtime.build_hub` counts two participants that
    share a label twice, and folding into a dict would silently change that.
    """
    counts: dict[_K, int] = {}
    for keys in per_dataset_keys:
        for key in set(keys):
            counts[key] = counts.get(key, 0) + 1
    return {key for key, n in counts.items() if n >= min_datasets}


# IUPAC element symbols (H..Og). Validating against the real set is what keeps the
# element-canonical normalizer SAFE: only well-formed chemical formulas are reordered,
# so a non-formula string (an id, a label) can never be silently merged with another.
_ELEMENT_SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
    "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
    "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl "
    "Mc Lv Ts Og"
)
_ELEMENTS = frozenset(_ELEMENT_SYMBOLS.split())
# An element symbol followed by an optional (possibly decimal) count.
_ELEMENT_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*\.?\d*)")


def normalize_element_canonical(value: str) -> str:
    """Order-canonical composition key: fold subscripts + strip, then sort elements
    so ``Bi2Te3`` == ``Te3Bi2`` (a chemical formula is a multiset; element order does
    not change the compound). Productization ① of the ADR — a richer, opt-in join key
    for heterogeneous sources that write the same composition in different orders.

    SAFE by construction: it only reorders a string that parses **entirely** into known
    element symbols with their counts; anything else (an id, a label, a formula with
    parentheses / dopant commas / charges) falls back to :func:`normalize_composition`
    (subscript-fold + strip, no reorder) so distinct strings are never wrongly merged.
    Case is significant (``Co`` cobalt != ``CO`` carbon+oxygen). Counts are preserved
    verbatim — it does NOT reduce stoichiometry (``Bi4Te6`` != ``Bi2Te3``); a reducing
    key would be a further, separately-vetted normalizer.
    """
    s = value.translate(_SUBS).replace(" ", "")
    if not s:
        return s
    parts: list[tuple[str, str]] = []
    pos = 0
    for m in _ELEMENT_TOKEN.finditer(s):
        if m.start() != pos or m.group(1) not in _ELEMENTS:
            return normalize_composition(value)  # gap / non-element -> not a clean formula
        parts.append((m.group(1), m.group(2)))
        pos = m.end()
    if pos != len(s) or not parts:
        return normalize_composition(value)  # trailing junk / nothing parsed
    parts.sort(key=lambda p: p[0])
    return "".join(el + cnt for el, cnt in parts)


# Named normalizers (a step toward Tier-0 functions): a concept references one by
# name, so the join key is explicit, vetted, and recorded in provenance.
NORMALIZERS = {
    # Domain-neutral (the generic core — cover the long tail of any concept).
    "identity": normalize_identity,
    "casefold": normalize_casefold,
    "whitespace": normalize_whitespace,
    "nfkc": normalize_nfkc,
    "loose_text": normalize_loose_text,
    # Materials chemistry (separately-vetted domain functions).
    "composition": normalize_composition,
    "element_canonical": normalize_element_canonical,
}


@dataclass(frozen=True)
class Rule:
    """One dataset's participation in a concept: which predicate carries the value."""

    dataset: str
    predicate: str


@dataclass(frozen=True)
class KeyPart:
    """One component of a (possibly compound) join key: a name + its normalizer (named
    or a recipe). A concept's join key is the TUPLE of its parts' normalized values
    (crosswalk-compound-keys.md). A single-part concept is the legacy single-value join."""

    name: str
    normalizer: str = "identity"
    normalizer_recipe: tuple[str, ...] = ()


# Tuple-key delimiters (compound keys). The KEY delimiter is a control char that never
# occurs in real values (collision-safe); the DISPLAY delimiter is human-readable (used
# in labels + provenance). Single-part keys use neither (byte-identical to the legacy
# single-value hub).
_TUPLE_KEY_DELIM = "\x1f"  # ASCII Unit Separator
_TUPLE_DISPLAY_DELIM = " | "


@dataclass(frozen=True)
class Concept:
    """A shared hub concept (e.g. composition): a class + a link predicate + the
    per-dataset rules that map into it + the normalizer(s) that form its join key.

    The join key is one value by default; a concept may instead declare ``key_parts``
    (≥ 1) whose normalized TUPLE is the join key — two entities coincide iff every part
    matches (crosswalk-compound-keys.md). A single-part concept == the legacy behavior."""

    name: str
    class_iri: str
    link_predicate: str
    normalizer: str = "identity"
    # An optional declarative recipe (ordered primitive ids). When non-empty it IS the
    # join key (resolve_normalizer prefers it over the named normalizer above).
    normalizer_recipe: tuple[str, ...] = ()
    rules: tuple[Rule, ...] = ()
    # Compound key: ordered parts whose normalized tuple is the join key. Empty = a
    # single implicit part from ``normalizer`` / ``normalizer_recipe`` (back-compat).
    key_parts: tuple[KeyPart, ...] = ()

    def resource_base(self) -> str:
        return f"{XW_RESOURCE}{self.name}/"

    def datasets(self) -> list[str]:
        return sorted({r.dataset for r in self.rules})

    def parts(self) -> tuple[KeyPart, ...]:
        """The effective key parts: the explicit ``key_parts`` or a single implicit part
        from the concept's own normalizer (so single-part is the legacy 1-value join)."""
        return self.key_parts or (
            KeyPart(self.name, self.normalizer, self.normalizer_recipe),
        )


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


# observations[(concept_name, dataset_label)] -> iterable of (entity_iri, raw_value).
# raw_value is a string (single-part join) or a tuple of strings (a compound key, one
# per concept key part — crosswalk-compound-keys.md).
Observations = dict[tuple[str, str], Iterable[tuple[str, "str | tuple[str, ...]"]]]


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
        parts = concept.parts()
        n_parts = len(parts)
        normalizers = [resolve_normalizer(p.normalizer, p.normalizer_recipe) for p in parts]
        part_labels = [recipe_label(p.normalizer, p.normalizer_recipe) for p in parts]
        # The join key is one value (single part = legacy) or a TUPLE (compound). The KEY
        # joins normalized parts with a collision-safe control char (single-part = the
        # value itself, byte-identical to before); LABEL / raw display use " | ".
        norm_label = part_labels[0] if n_parts == 1 else _TUPLE_DISPLAY_DELIM.join(part_labels)
        # per dataset: join key -> [(entity IRI, raw display)]. Keep the raw spelling so
        # per-link provenance records what was normalized (the join claim).
        per_ds: dict[str, dict[str, list[tuple[str, str]]]] = {}
        key_labels: dict[str, str] = {}  # join key -> human-readable label
        for rule in concept.rules:
            bucket = per_ds.setdefault(rule.dataset, {})
            for entity, raw in observations.get((concept.name, rule.dataset), []):
                raw_tuple = raw if isinstance(raw, tuple) else (raw,)
                if len(raw_tuple) != n_parts:
                    continue  # arity mismatch (missing/extra part) — never half-join
                norm = tuple(normalizers[i](raw_tuple[i]) for i in range(n_parts))
                if n_parts == 1:
                    key, label, raw_disp = norm[0], norm[0], raw_tuple[0]
                else:
                    key = _TUPLE_KEY_DELIM.join(norm)
                    label = _TUPLE_DISPLAY_DELIM.join(norm)
                    raw_disp = _TUPLE_DISPLAY_DELIM.join(raw_tuple)
                key_labels[key] = label
                bucket.setdefault(key, []).append((entity, raw_disp))
        # shared = a key present in >= min_datasets participating datasets
        shared = sorted(shared_keys(per_ds.values(), min_datasets=config.min_datasets))
        build.shared[concept.name] = [key_labels[k] for k in shared]
        build.links[concept.name] = {}

        lines.append(f"# --- concept: {concept.name} (normalizer: {norm_label}) ---")
        lines.append(
            f'<{concept.class_iri}> a owl:Class ; rdfs:label "{_esc(concept.name)} (crosswalk)" .'
        )
        base = concept.resource_base()
        for key in shared:
            iri = f"{base}{urllib.parse.quote(key, safe='')}"
            lines.append(
                f'<{iri}> a <{concept.class_iri}> ; rdfs:label "{_esc(key_labels[key])}" ; '
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
                            f'xw:normalizer "{_esc(norm_label)}" ; '
                            f"prov:wasGeneratedBy <{activity_iri}> ."
                        )
        lines.append("")
    build.turtle = "\n".join(lines) + "\n"
    return build
