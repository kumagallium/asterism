"""Find crosswalk candidates from the data itself — no LLM, no key, read-only.

Authoring a crosswalk used to mean seven decisions before anything happened (which
datasets, a concept key in ascii, an API key, a predicate per dataset, a normalizer, a
compound key, a name). This module removes all of them but one: it reads the promoted
datasets, looks for values that ACTUALLY overlap, and hands back ranked candidates with
the evidence. The human decides which one to build (ADR ``kantan-mode-two-tier-ux.md``
K13 — machines take the decisions that carry no meaning).

Deterministic all the way down: the join keys come from the closed, vetted
``asterism.crosswalk.NORMALIZERS``; the overlap is :func:`asterism.crosswalk.shared_keys`
— the same predicate a build applies, so a candidate's ``matched`` equals the
``shared_total`` its ``build_config`` would produce. Nothing is generated, nothing is
executed, nothing is written. Every cap it hits is reported rather than silently
applied: a discovery that quietly dropped a join key would be worse than one that
found nothing.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from asterism import substrate
from asterism.crosswalk import XW, resolve_normalizer, shared_keys

logger = logging.getLogger(__name__)

# The normalizer ladder, conservative first. Each rung folds strictly more than the
# one before for text; `element_canonical` additionally reorders a string that parses
# ENTIRELY as element symbols and otherwise falls back to `composition`, so it
# subsumes `composition` and that name is not tried separately. Every rung is a member
# of the closed NORMALIZERS set — no branching on what the data "looks like".
DISCOVER_LADDER: tuple[str, ...] = ("identity", "nfkc", "loose_text", "element_canonical")

# Values that mean "no value". Letting these become join keys is the classic way a
# discovery reports a huge, meaningless overlap (every dataset has blanks). Compared
# against `value.strip().casefold()`. Reported in the response so the exclusion is
# never invisible. "0" is deliberately NOT here — it is a legitimate identifier in
# some domains; numeric predicates are excluded as a whole instead (see
# `classify_predicate`), and a lone numeric overlap is demoted by the score.
NULL_TOKENS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        "---",
        "?",
        "n/a",
        "n.a.",
        "na",
        "null",
        "none",
        "nan",
        "nil",
        "#n/a",
        "unknown",
    }
)

_XSD = "http://www.w3.org/2001/XMLSchema#"
NUMERIC_DATATYPES: frozenset[str] = frozenset(
    f"{_XSD}{t}"
    for t in (
        "decimal",
        "double",
        "float",
        "integer",
        "int",
        "long",
        "short",
        "byte",
        "nonNegativeInteger",
        "nonPositiveInteger",
        "negativeInteger",
        "positiveInteger",
        "unsignedInt",
        "unsignedLong",
        "unsignedShort",
        "unsignedByte",
    )
)
TEMPORAL_DATATYPES: frozenset[str] = frozenset(
    f"{_XSD}{t}" for t in ("date", "dateTime", "time", "gYear", "gYearMonth", "duration")
)
BOOLEAN_DATATYPE = f"{_XSD}boolean"

# Untyped plain literals need a fallback judgement. Deliberately strict: a value must
# be ENTIRELY a number / a date to count toward the share.
_NUMERIC_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_TEMPORAL_RE = re.compile(
    r"^\d{4}([-/]\d{1,2}([-/]\d{1,2})?)?([T ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)

# Splitting an identifier into words: camelCase boundaries + non-alphanumerics.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_PERSPECTIVE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class DiscoverLimits:
    """Every bound discovery works under. Echoed back in the result so a caller can
    tell "there is nothing more" from "we stopped looking"."""

    max_datasets: int = 12
    max_predicates_per_dataset: int = 12
    max_values_per_predicate: int = 2000
    max_value_length: int = 120
    min_datasets: int = 2
    min_shared_keys: int = 2
    max_candidates: int = 12
    max_clusters_per_rung: int = 24
    low_cardinality_distinct: int = 8
    high_fanout_ratio: float = 1000.0
    numeric_share: float = 0.95
    temporal_share: float = 0.95
    free_text_drop_share: float = 0.5
    sample_limit: int = 5
    ladder: tuple[str, ...] = DISCOVER_LADDER

    def to_dict(self) -> dict:
        return {
            "max_datasets": self.max_datasets,
            "max_predicates_per_dataset": self.max_predicates_per_dataset,
            "max_values_per_predicate": self.max_values_per_predicate,
            "max_value_length": self.max_value_length,
            "min_datasets": self.min_datasets,
            "min_shared_keys": self.min_shared_keys,
            "max_candidates": self.max_candidates,
            "max_clusters_per_rung": self.max_clusters_per_rung,
            "low_cardinality_distinct": self.low_cardinality_distinct,
            "high_fanout_ratio": self.high_fanout_ratio,
            "numeric_share": self.numeric_share,
            "temporal_share": self.temporal_share,
            "free_text_drop_share": self.free_text_drop_share,
            "ladder": list(self.ladder),
            "stop_values": sorted(NULL_TOKENS),
        }


@dataclass(frozen=True)
class DiscoverDataset:
    """A dataset discovery may scan. The caller resolves these from its registry — this
    module never reads the filesystem (mirrors ``crosswalk_runtime``'s api independence)."""

    dataset_id: str
    label: str
    name: str


@dataclass
class PredicateProfile:
    iri: str
    statements: int
    sample: str


@dataclass
class Slot:
    """One (dataset, predicate) pair that survived the exclusion rules — a possible
    participant. A dataset contributes at most one slot to any candidate, because a
    crosswalk participant declares exactly one predicate per concept."""

    ds_index: int
    dataset: DiscoverDataset
    predicate: str
    statements: int
    distinct: int
    values: list[str]
    values_truncated: bool
    # rung -> the normalized keys this slot reports (interned to ints; see `discover`).
    keys: dict[str, frozenset[int]] = field(default_factory=dict)
    # rung -> normalized key -> a raw spelling that produced it (evidence).
    raw_by_key: dict[str, dict[int, str]] = field(default_factory=dict)


@dataclass
class Cluster:
    """A candidate join: the slots that participate, the rung chosen, and the counts."""

    slots: tuple[int, ...]
    normalizer: str
    trials: dict[str, int]
    shared: frozenset[int]

    @property
    def matched(self) -> int:
        return len(self.shared)


# ---------------------------------------------------------------------------
# Pure: value / predicate judgement
# ---------------------------------------------------------------------------


def value_is_null(value: str) -> bool:
    """True for a value that means "nothing here" and must never become a join key."""
    return value.strip().casefold() in NULL_TOKENS


def filter_values(values: Iterable[str], *, max_length: int) -> tuple[list[str], int]:
    """Drop null tokens; drop values longer than ``max_length``. Returns the kept
    values and how many were dropped for length (free-text detection uses the ratio)."""
    kept: list[str] = []
    too_long = 0
    for v in values:
        if value_is_null(v):
            continue
        if len(v) > max_length:
            too_long += 1
            continue
        kept.append(v)
    return kept, too_long


def _share(values: Sequence[str], pattern: re.Pattern[str]) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if pattern.match(v.strip())) / len(values)


def classify_predicate(
    values: Sequence[str],
    datatypes: frozenset[str],
    too_long: int,
    *,
    limits: DiscoverLimits,
) -> str | None:
    """Why this predicate cannot carry a join, or ``None`` when it can.

    The order is the specification: the reported reason has to be reproducible.
    Declared RDF datatypes are trusted before any pattern matching — a Morph-KGC cast
    is stronger evidence than what a string looks like.

    Deliberately NOT a rule here: uniqueness ratio. A column unique per row (a DOI, a
    sample id, a lot number) is the BEST join key there is; excluding "too unique"
    would throw away the strongest candidates. The other end is already covered by
    ``constant``. Uniqueness feeds the flags and the score instead.
    """
    total = len(values) + too_long
    if total and too_long / total >= limits.free_text_drop_share:
        return "free_text"
    if not values:
        return "empty"
    if len(set(values)) < 2:
        return "constant"  # one value joins everything to everything = no information
    if datatypes and datatypes <= NUMERIC_DATATYPES:
        return "numeric"
    if datatypes and datatypes <= TEMPORAL_DATATYPES:
        return "temporal"
    if datatypes and datatypes == {BOOLEAN_DATATYPE}:
        return "boolean"
    if _share(values, _NUMERIC_RE) >= limits.numeric_share:
        return "numeric"
    if _share(values, _TEMPORAL_RE) >= limits.temporal_share:
        return "temporal"
    return None


def choose_normalizer(trials: dict[str, int], ladder: Sequence[str]) -> str:
    """The rung that matches the most — and, among ties, the most conservative one.
    Folding more than necessary is a silent correctness risk, so a looser rung has to
    actually earn its place with a bigger overlap."""
    best = max(trials.values(), default=0)
    for rung in ladder:
        if trials.get(rung, 0) == best:
            return rung
    return ladder[0]


# ---------------------------------------------------------------------------
# Pure: naming (K13 — the machine derives what carries no meaning)
# ---------------------------------------------------------------------------


def _tokens(local: str) -> list[str]:
    """An identifier's words, lowercased: ``hasCompositionString`` -> composition,
    string (a leading has/is is a linking verb, not part of the concept)."""
    parts = [p for p in _NON_ALNUM.split(_CAMEL_BOUNDARY.sub(" ", local)) if p]
    words = [w.lower() for chunk in parts for w in chunk.split()]
    if len(words) > 1 and words[0] in {"has", "is"}:
        words = words[1:]
    return words


def local_name(iri: str) -> str:
    """The last path/fragment segment of an IRI (display + naming input)."""
    for sep in ("#", "/"):
        if sep in iri:
            tail = iri.rsplit(sep, 1)[-1]
            if tail:
                return tail
    return iri


def derive_concept_name(predicates: Sequence[str], *, taken: Iterable[str], rank: int) -> str:
    """An ascii concept key from the participants' predicate names.

    Identical names win; otherwise the words they have in common, in the first
    participant's order; otherwise the most frequent local name. A predicate named
    only in Japanese leaves nothing ascii, so the key falls back to a positional one
    rather than minting an IRI out of percent-escapes.
    """
    used = set(taken)
    per_pred = [_tokens(local_name(p)) for p in predicates]
    per_pred = [t for t in per_pred if t]
    name = ""
    if per_pred:
        if all(t == per_pred[0] for t in per_pred):
            name = "_".join(per_pred[0])
        else:
            common = set(per_pred[0]).intersection(*(set(t) for t in per_pred[1:]))
            if common:
                name = "_".join(w for w in per_pred[0] if w in common)
            else:
                # No word in common (comp / formula / composition). Take the most
                # frequent local name and, among ties, the longest — an abbreviation
                # should lose to the spelled-out term. Alphabetical last, for a total
                # order (the same store must always name a concept the same way).
                counts: dict[str, int] = {}
                for p in predicates:
                    key = local_name(p)
                    counts[key] = counts.get(key, 0) + 1
                top = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[0][0]
                name = "_".join(_tokens(top))
    name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    if not name:
        name = f"shared_value_{rank + 1}"
    if name in used:
        n = 2
        while f"{name}_{n}" in used:
            n += 1
        name = f"{name}_{n}"
    return name


def _pascal(name: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in name.split("_") if w)


def concept_terms(name: str) -> tuple[str, str]:
    """The hub class + link predicate minted for a concept key — the same rule the
    authoring UI uses (``xw:Composition`` / ``xw:hasComposition``)."""
    p = _pascal(name)
    return f"{XW}{p}", f"{XW}has{p}"


def perspective_id_for(name: str) -> str:
    """A crosswalk id from a concept key. Must satisfy the runtime's id shape, so a
    key that cannot be expressed falls back to a stable generic one."""
    pid = name.replace("_", "-").strip("-")
    return pid if _PERSPECTIVE_ID_RE.match(pid) else "crosswalk"


# ---------------------------------------------------------------------------
# Pure: clustering, flags, score, samples
# ---------------------------------------------------------------------------


def cluster_candidates(
    slots: Sequence[Slot], *, limits: DiscoverLimits
) -> tuple[list[Cluster], bool]:
    """Group slots into CONCEPT-level candidates: "these N datasets connect on this
    value", not a card per pair. A hub joins any number of datasets on one concept, so
    three datasets sharing compositions must come back as one candidate with three
    participants.

    Greedy: start from the strongest cross-dataset pair, then keep adding the slot that
    overlaps the cluster's value space most. The membership test is against the UNION,
    which is exactly the hub's own rule (a key counts when >= min_datasets datasets
    report it — not when everyone does), so ``matched`` equals what a build produces.
    A slot is consumed once used: one predicate carries one value space, hence one
    concept, and that also bounds the search.

    Every ordering is total (overlap desc, then ids), so the same store always yields
    the same candidates.
    """
    found: dict[tuple[int, ...], Cluster] = {}
    truncated = False

    for rung in limits.ladder:
        keys = {i: slots[i].keys[rung] for i in range(len(slots))}
        pairs: list[tuple[int, tuple[int, int]]] = []
        for i, j in combinations(range(len(slots)), 2):
            if slots[i].ds_index == slots[j].ds_index:
                continue
            overlap = len(keys[i] & keys[j])
            if overlap >= limits.min_shared_keys:
                pairs.append((overlap, (i, j)))
        # Strongest first; ties by slot index so the walk is reproducible.
        pairs.sort(key=lambda pv: (-pv[0], pv[1]))

        used_slots: set[int] = set()
        emitted = 0
        for _, (i, j) in pairs:
            if i in used_slots or j in used_slots:
                continue
            if emitted >= limits.max_clusters_per_rung:
                truncated = True
                break
            members = [i, j]
            used_ds = {slots[i].ds_index, slots[j].ds_index}
            union = keys[i] | keys[j]
            while True:
                best: tuple[tuple[int, str, str], int] | None = None
                for t in range(len(slots)):
                    if t in used_slots or t in members or slots[t].ds_index in used_ds:
                        continue
                    gain = len(keys[t] & union)
                    if gain < limits.min_shared_keys:
                        continue
                    rank = (-gain, slots[t].dataset.dataset_id, slots[t].predicate)
                    if best is None or rank < best[0]:
                        best = (rank, t)
                if best is None:
                    break
                t = best[1]
                members.append(t)
                used_ds.add(slots[t].ds_index)
                union |= keys[t]
            shared = shared_keys((keys[m] for m in members), min_datasets=limits.min_datasets)
            if len(shared) < limits.min_shared_keys:
                continue
            used_slots.update(members)
            emitted += 1
            sig = tuple(sorted(members))
            existing = found.get(sig)
            if existing is None:
                found[sig] = Cluster(sig, rung, {rung: len(shared)}, frozenset(shared))
            else:
                existing.trials[rung] = len(shared)

    # Fill in every rung's count for each cluster (the key sets are already in hand),
    # so the UI can say "as-is 12, ignoring case and width 210" from real data.
    for sig, cluster in found.items():
        for rung in limits.ladder:
            if rung in cluster.trials:
                continue
            cluster.trials[rung] = len(
                shared_keys(
                    (slots[m].keys[rung] for m in sig), min_datasets=limits.min_datasets
                )
            )
        cluster.normalizer = choose_normalizer(cluster.trials, limits.ladder)
        cluster.shared = frozenset(
            shared_keys(
                (slots[m].keys[cluster.normalizer] for m in sig),
                min_datasets=limits.min_datasets,
            )
        )

    # A cluster fully contained in a bigger one is the same join, seen with fewer
    # participants — keep the bigger card only.
    sigs = list(found)
    keep = [
        s
        for s in sigs
        if not any(other != s and set(s) < set(other) for other in sigs)
    ]
    return [found[s] for s in keep], truncated


def score_candidate(cluster: Cluster, slots: Sequence[Slot]) -> float:
    """Rank candidates. Raw overlap alone lets an accidental collision in a huge id
    space outrank a real join, so size is damped, coverage is rewarded, and joining
    more datasets — the whole point of a hub — counts.

    Coverage takes the BEST-covered participant, not the worst: a 100-value dataset of
    your own matching 90 rows of a 100k-row reference is the case this feature exists
    for, and a worst-case rule would bury it.
    """
    if not cluster.slots:
        return 0.0
    size = math.log10(1 + cluster.matched)
    reach = max(
        (
            len(cluster.shared & slots[m].keys[cluster.normalizer]) / slots[m].distinct
            for m in cluster.slots
            if slots[m].distinct
        ),
        default=0.0,
    )
    span = len({slots[m].ds_index for m in cluster.slots}) - 1
    return size * reach * span


def candidate_flags(
    cluster: Cluster, slots: Sequence[Slot], *, limits: DiscoverLimits
) -> list[str]:
    """Closed set of cautions. Ids only — the wording lives in the UI's locales."""
    flags: list[str] = []
    members = [slots[m] for m in cluster.slots]
    if cluster.matched == 1:
        flags.append("single_value_overlap")
    if min((s.distinct for s in members), default=0) < limits.low_cardinality_distinct:
        flags.append("low_cardinality")
    if any(s.distinct and s.statements / s.distinct > limits.high_fanout_ratio for s in members):
        flags.append("high_fanout")
    if any(s.values_truncated for s in members):
        flags.append("values_truncated")
    if cluster.normalizer != "identity" and cluster.trials.get("identity", 0) == 0:
        flags.append("fold_only_match")
    reaches = [
        len(cluster.shared & s.keys[cluster.normalizer]) / s.distinct
        for s in members
        if s.distinct
    ]
    if reaches and min(reaches) < 0.01 and max(reaches) > 0.5:
        flags.append("asymmetric_coverage")
    return flags


def pick_samples(
    cluster: Cluster, slots: Sequence[Slot], intern: dict[int, str], *, limit: int
) -> list[dict]:
    """Shared values as evidence, spellings that DISAGREE first: seeing ``Bi₂Te₃`` and
    ``Bi2Te3`` side by side is what makes the candidate obvious to a human."""
    members = [slots[m] for m in cluster.slots]
    rows: list[tuple[int, str, dict[str, str]]] = []
    for key in cluster.shared:
        raw: dict[str, str] = {}
        for s in members:
            spelling = s.raw_by_key.get(cluster.normalizer, {}).get(key)
            if spelling is not None:
                raw[s.dataset.dataset_id] = spelling
        rows.append((-len(set(raw.values())), intern.get(key, ""), raw))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [{"key": key, "raw": raw} for _, key, raw in rows[:limit]]


def build_config_of(
    cluster: Cluster, slots: Sequence[Slot], name: str, *, limits: DiscoverLimits
) -> dict:
    """The candidate as a crosswalk config — POSTable to ``/api/crosswalk/{id}/build``
    with no edits. Discovery hands over a buildable thing, not a hint."""
    class_iri, link_predicate = concept_terms(name)
    return {
        "min_datasets": max(2, limits.min_datasets),
        "concepts": [
            {
                "name": name,
                "class_iri": class_iri,
                "link_predicate": link_predicate,
                "normalizer": cluster.normalizer,
                "participants": [
                    {
                        "dataset_id": slots[m].dataset.dataset_id,
                        "label": slots[m].dataset.label,
                        "predicate": slots[m].predicate,
                    }
                    for m in cluster.slots
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# I/O: bounded reads
# ---------------------------------------------------------------------------


async def _select_bindings(client, query: str) -> list[dict]:
    data = await client.sparql_select(query)
    results = data.get("results", {}) if isinstance(data, dict) else {}
    return results.get("bindings", []) if isinstance(results, dict) else []


async def profile_literal_predicates(
    client, graph: str, *, limit: int
) -> tuple[list[PredicateProfile], bool]:
    """A graph's literal-valued predicates, most-used first, with a sample value.

    ``COUNT(DISTINCT ?v)`` is deliberately not asked for: it materializes a value set
    per predicate and blows up on a large graph, while ``COUNT(*)`` streams. The exact
    distinct count comes from the value read that follows. ``LIMIT n+1`` is how
    truncation is detected rather than guessed.
    """
    q = (
        f"SELECT ?p (COUNT(*) AS ?n) (SAMPLE(?v) AS ?ex) WHERE {{ GRAPH <{graph}> {{ "
        f"?e ?p ?v FILTER(isLiteral(?v)) }} }} GROUP BY ?p ORDER BY DESC(?n) LIMIT {limit + 1}"
    )
    rows = await _select_bindings(client, q)
    out: list[PredicateProfile] = []
    for b in rows:
        p = b.get("p", {})
        if p.get("type") != "uri":
            continue
        try:
            n = int(b.get("n", {}).get("value", "0"))
        except (TypeError, ValueError):
            n = 0
        out.append(
            PredicateProfile(iri=p["value"], statements=n, sample=b.get("ex", {}).get("value", ""))
        )
    return out[:limit], len(out) > limit


async def fetch_distinct_values(
    client, graph: str, predicate: str, *, limit: int
) -> tuple[list[str], frozenset[str], bool]:
    """Distinct literal values of one predicate, plus the datatypes seen.

    ``?p`` is bound, so this walks one predicate's slice and stops as soon as the cap
    is reached — a per-row unique id costs the cap, not the graph.
    """
    q = (
        f"SELECT DISTINCT ?v WHERE {{ GRAPH <{graph}> {{ ?e <{predicate}> ?v "
        f"FILTER(isLiteral(?v)) }} }} LIMIT {limit + 1}"
    )
    rows = await _select_bindings(client, q)
    values: list[str] = []
    datatypes: set[str] = set()
    for b in rows:
        v = b.get("v", {})
        if "value" not in v:
            continue
        values.append(v["value"])
        dt = v.get("datatype")
        if dt:
            datatypes.add(dt)
    return values[:limit], frozenset(datatypes), len(values) > limit


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


async def discover(
    client,
    datasets: Sequence[DiscoverDataset],
    *,
    limits: DiscoverLimits | None = None,
    datasets_truncated: bool = False,
    skipped_datasets: Sequence[dict] = (),
    progress: Callable[[str, dict], None] | None = None,
    should_cancel: Callable[[], bool] | Callable[[], Awaitable[bool]] | None = None,
) -> dict:
    """Scan the promoted graphs and rank the joins that actually exist.

    Read-only: only ``SELECT`` runs. Returns a JSON-ready dict whose ``candidates``
    each carry a ``build_config`` that builds as-is.
    """
    lim = limits or DiscoverLimits()
    cancelled = False
    queries = 0

    def emit(phase: str, **payload) -> None:
        if progress is not None:
            progress(phase, payload)

    async def stop() -> bool:
        if should_cancel is None:
            return False
        result = should_cancel()
        if isinstance(result, bool):
            return result
        return await result

    promoted = set(await substrate.canonical_graphs(client))
    queries += 1

    scanned: list[dict] = []
    # Datasets the caller filtered out before the scan (not promoted, a crosswalk hub,
    # not requested, over the cap) travel with the ones dropped here.
    skipped: list[dict] = list(skipped_datasets)
    slots: list[Slot] = []

    for ds_index, ds in enumerate(datasets):
        if await stop():
            cancelled = True
            break
        emit("scan", dataset_id=ds.dataset_id, name=ds.name, done=ds_index, total=len(datasets))

        key_graph = substrate.canonical_graph_iri(ds.dataset_id)
        live_graph = await substrate.live_graph_of(client, key_graph) or key_graph
        queries += 1
        if live_graph not in promoted:
            skipped.append({"dataset_id": ds.dataset_id, "reason": "not_promoted"})
            continue

        profiles, preds_truncated = await profile_literal_predicates(
            client, live_graph, limit=lim.max_predicates_per_dataset
        )
        queries += 1
        excluded: list[dict] = []

        for profile in profiles:
            raw_values, datatypes, values_truncated = await fetch_distinct_values(
                client, live_graph, profile.iri, limit=lim.max_values_per_predicate
            )
            queries += 1
            values, too_long = filter_values(raw_values, max_length=lim.max_value_length)
            reason = classify_predicate(values, datatypes, too_long, limits=lim)
            if reason is not None:
                excluded.append(
                    {
                        "iri": profile.iri,
                        "reason": reason,
                        "sample": profile.sample,
                        "distinct": len(set(values)),
                    }
                )
                continue
            slots.append(
                Slot(
                    ds_index=ds_index,
                    dataset=ds,
                    predicate=profile.iri,
                    statements=profile.statements,
                    distinct=len(set(values)),
                    values=values,
                    values_truncated=values_truncated,
                )
            )

        scanned.append(
            {
                "dataset_id": ds.dataset_id,
                "label": ds.label,
                "name": ds.name,
                "live_graph": live_graph,
                "predicates_scanned": len(profiles),
                "predicates_truncated": preds_truncated,
                "predicates_excluded": excluded,
            }
        )

    # Normalize once per rung, interning keys to ints: the clustering does set algebra
    # over up to 96 slots x 4 rungs, and int sets keep that cheap in time and memory.
    intern: dict[str, int] = {}
    unintern: dict[int, str] = {}
    for slot in slots:
        for rung in lim.ladder:
            normalize = resolve_normalizer(rung)
            ids: set[int] = set()
            raw_by_key: dict[int, str] = {}
            for raw in slot.values:
                key = normalize(raw)
                if not key or value_is_null(key):
                    continue
                kid = intern.get(key)
                if kid is None:
                    kid = len(intern)
                    intern[key] = kid
                    unintern[kid] = key
                ids.add(kid)
                raw_by_key.setdefault(kid, raw)
            slot.keys[rung] = frozenset(ids)
            slot.raw_by_key[rung] = raw_by_key

    emit("cluster", slots=len(slots))
    clusters, clusters_truncated = cluster_candidates(slots, limits=lim)
    clusters.sort(
        key=lambda c: (
            -score_candidate(c, slots),
            -c.matched,
            -len({slots[m].ds_index for m in c.slots}),
            lim.ladder.index(c.normalizer) if c.normalizer in lim.ladder else len(lim.ladder),
            sorted(slots[m].dataset.dataset_id for m in c.slots),
            sorted(slots[m].predicate for m in c.slots),
        )
    )
    candidates_truncated = len(clusters) > lim.max_candidates
    clusters = clusters[: lim.max_candidates]

    taken: list[str] = []
    out: list[dict] = []
    for rank, cluster in enumerate(clusters):
        preds = [slots[m].predicate for m in cluster.slots]
        name = derive_concept_name(preds, taken=taken, rank=rank)
        taken.append(name)
        class_iri, link_predicate = concept_terms(name)
        out.append(
            {
                "id": f"c{rank + 1}",
                "concept": name,
                "name": name,
                "perspective_id": perspective_id_for(name),
                "class_iri": class_iri,
                "link_predicate": link_predicate,
                "normalizer": cluster.normalizer,
                "normalizer_trials": [
                    {"normalizer": rung, "matched": cluster.trials.get(rung, 0)}
                    for rung in lim.ladder
                ],
                "matched": cluster.matched,
                "score": round(score_candidate(cluster, slots), 4),
                "participants": [
                    {
                        "dataset_id": slots[m].dataset.dataset_id,
                        "label": slots[m].dataset.label,
                        "name": slots[m].dataset.name,
                        "predicate": slots[m].predicate,
                        "predicate_label": local_name(slots[m].predicate),
                        "distinct_values": slots[m].distinct,
                        "matched": len(cluster.shared & slots[m].keys[cluster.normalizer]),
                        "coverage": round(
                            len(cluster.shared & slots[m].keys[cluster.normalizer])
                            / slots[m].distinct,
                            4,
                        )
                        if slots[m].distinct
                        else 0.0,
                        "statements": slots[m].statements,
                        "values_truncated": slots[m].values_truncated,
                    }
                    for m in cluster.slots
                ],
                "samples": pick_samples(cluster, slots, unintern, limit=lim.sample_limit),
                "flags": candidate_flags(cluster, slots, limits=lim),
                "build_config": build_config_of(cluster, slots, name, limits=lim),
            }
        )

    return {
        "candidates": out,
        "scanned": {
            "datasets": scanned,
            "datasets_skipped": skipped,
            "datasets_truncated": datasets_truncated,
            "clusters_truncated": clusters_truncated,
            "candidates_truncated": candidates_truncated,
        },
        "limits": lim.to_dict(),
        "cancelled": cancelled,
        "queries": queries,
    }
