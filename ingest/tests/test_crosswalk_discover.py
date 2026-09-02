"""Crosswalk discovery (kantan-mode ADR): find the joins that actually exist.

Two halves. The judgement — which predicates can carry a join, which normalizer to
use, how slots cluster into concepts, how candidates rank — is pure and tested without
any store. The scan itself runs the REAL SPARQL against an in-memory ``rdflib.Dataset``
(the ``test_crosswalk_runtime`` idiom), so graph resolution, the caps, and the
read-only promise are exercised end-to-end.
"""
from __future__ import annotations

import json

import pytest
import rdflib

from asterism import substrate
from asterism.crosswalk_discover import (
    DiscoverDataset,
    DiscoverLimits,
    Slot,
    choose_normalizer,
    classify_predicate,
    cluster_candidates,
    concept_terms,
    derive_concept_name,
    discover,
    filter_values,
    perspective_id_for,
    pick_samples,
    score_candidate,
    value_is_null,
)
from asterism.crosswalk_runtime import parse_config

NS = "https://kumagallium.github.io/asterism/x/ontology#"
XSD = "http://www.w3.org/2001/XMLSchema#"
LIMITS = DiscoverLimits()


# ---------------------------------------------------------------------------
# Pure: which predicates can carry a join
# ---------------------------------------------------------------------------


def _classify(values, datatypes=frozenset(), too_long=0, limits=LIMITS):
    return classify_predicate(list(values), frozenset(datatypes), too_long, limits=limits)


def test_numeric_predicate_is_excluded_by_declared_datatype() -> None:
    # A cast datatype is stronger evidence than what the string looks like.
    assert _classify(["1", "2", "3"], {f"{XSD}decimal"}) == "numeric"


def test_numeric_predicate_is_excluded_by_the_values_when_untyped() -> None:
    assert _classify(["301.5", "2.0", "-3e4", "17"]) == "numeric"


def test_temporal_predicate_is_excluded() -> None:
    assert _classify(["2026-01-02", "2024-11-30"], {f"{XSD}date"}) == "temporal"
    assert _classify(["2026-01-02", "2024-11-30", "2020-05-05"]) == "temporal"


def test_constant_and_empty_predicates_are_excluded() -> None:
    # One value would join every row to every row: no information, huge damage.
    assert _classify(["same", "same", "same"]) == "constant"
    assert _classify([]) == "empty"


def test_free_text_predicate_is_excluded_by_the_length_ratio() -> None:
    assert _classify(["ok", "fine"], too_long=6) == "free_text"


def test_identifier_like_and_categorical_predicates_are_both_kept() -> None:
    # The two ends of the uniqueness range. A per-row unique DOI is the BEST join key
    # there is, so "too unique" must never be an exclusion rule; a 7-value crystal
    # system is legitimate too (it only earns a low_cardinality caution).
    dois = [f"10.1234/abc{i}" for i in range(500)]
    assert _classify(dois) is None
    assert _classify(["cubic", "hexagonal", "tetragonal", "trigonal"]) is None


def test_null_tokens_never_become_join_keys() -> None:
    assert value_is_null("") and value_is_null(" N/A ") and value_is_null("null")
    assert not value_is_null("0")  # a legitimate id in some domains
    kept, too_long = filter_values(["", " ", "NA", "n/a", "-", "Bi2Te3"], max_length=120)
    assert kept == ["Bi2Te3"]
    assert too_long == 0


# ---------------------------------------------------------------------------
# Pure: the normalizer ladder
# ---------------------------------------------------------------------------


def test_ladder_picks_the_most_conservative_of_the_best() -> None:
    # Folding more than necessary is a silent correctness risk, so a looser rung only
    # wins when it actually matches more.
    ladder = LIMITS.ladder
    assert choose_normalizer({"identity": 2, "nfkc": 2, "loose_text": 2}, ladder) == "identity"
    assert choose_normalizer({"identity": 0, "nfkc": 0, "loose_text": 5}, ladder) == "loose_text"


# ---------------------------------------------------------------------------
# Pure: clustering
# ---------------------------------------------------------------------------


def _slot(ds_index: int, dsid: str, predicate: str, values: list[str]) -> Slot:
    """A slot with its keys already normalized per rung (as `discover` does)."""
    from asterism.crosswalk import resolve_normalizer

    slot = Slot(
        ds_index=ds_index,
        dataset=DiscoverDataset(dataset_id=dsid, label=dsid, name=dsid),
        predicate=predicate,
        statements=len(values),
        distinct=len(set(values)),
        values=values,
        values_truncated=False,
    )
    for rung in LIMITS.ladder:
        normalize = resolve_normalizer(rung)
        keys, raw_by_key = set(), {}
        for raw in values:
            key = normalize(raw)
            kid = hash(key)
            keys.add(kid)
            raw_by_key.setdefault(kid, raw)
        slot.keys[rung] = frozenset(keys)
        slot.raw_by_key[rung] = raw_by_key
    return slot


def test_cluster_is_concept_level_not_pairwise() -> None:
    # Three datasets sharing a value space is ONE connection with three participants,
    # not three pair cards — that is what a hub actually builds.
    slots = [
        _slot(0, "a", f"{NS}comp", ["Bi2Te3", "PbTe", "SnSe"]),
        _slot(1, "b", f"{NS}formula", ["Bi2Te3", "PbTe", "ZnO"]),
        _slot(2, "c", f"{NS}composition", ["Bi2Te3", "PbTe"]),
    ]
    clusters, truncated = cluster_candidates(slots, limits=LIMITS)
    assert not truncated
    assert len(clusters) == 1
    assert len(clusters[0].slots) == 3
    assert clusters[0].matched == 2  # Bi2Te3, PbTe


def test_cluster_drops_a_subset_of_a_bigger_cluster() -> None:
    slots = [
        _slot(0, "a", f"{NS}comp", ["x1", "x2", "x3"]),
        _slot(1, "b", f"{NS}comp", ["x1", "x2", "x3"]),
        _slot(2, "c", f"{NS}comp", ["x1", "x2"]),
    ]
    clusters, _ = cluster_candidates(slots, limits=LIMITS)
    sigs = [c.slots for c in clusters]
    assert (0, 1, 2) in sigs
    assert (0, 1) not in sigs


def test_independent_concepts_stay_separate_clusters() -> None:
    slots = [
        _slot(0, "a", f"{NS}comp", ["Bi2Te3", "PbTe"]),
        _slot(1, "b", f"{NS}formula", ["Bi2Te3", "PbTe"]),
        _slot(0, "a", f"{NS}author", ["Kumagai", "Katsura"]),
        _slot(2, "c", f"{NS}creator", ["Kumagai", "Katsura"]),
    ]
    clusters, _ = cluster_candidates(slots, limits=LIMITS)
    assert len(clusters) == 2
    assert {c.slots for c in clusters} == {(0, 1), (2, 3)}


def test_one_dataset_contributes_at_most_one_slot_per_cluster() -> None:
    # A participant declares ONE predicate per concept, so the weaker of a dataset's
    # two overlapping columns must not also join.
    slots = [
        _slot(0, "a", f"{NS}strong", ["x1", "x2", "x3", "x4"]),
        _slot(0, "a", f"{NS}weak", ["x1", "x2"]),
        _slot(1, "b", f"{NS}comp", ["x1", "x2", "x3", "x4"]),
    ]
    clusters, _ = cluster_candidates(slots, limits=LIMITS)
    assert clusters[0].slots == (0, 2)
    assert all(len({slots[m].ds_index for m in c.slots}) == len(c.slots) for c in clusters)


def test_a_tiny_accidental_overlap_ranks_below_a_real_join() -> None:
    # The "0 joins everything" trap: a couple of collisions in a big id space must not
    # outrank a genuine 200-value join.
    real = [
        _slot(0, "a", f"{NS}comp", [f"m{i}" for i in range(200)]),
        _slot(1, "b", f"{NS}formula", [f"m{i}" for i in range(200)]),
    ]
    noise = [
        _slot(2, "c", f"{NS}code", [f"n{i}" for i in range(3000)] + ["0", "1"]),
        _slot(3, "d", f"{NS}ref", [f"q{i}" for i in range(3000)] + ["0", "1"]),
    ]
    slots = real + noise
    clusters, _ = cluster_candidates(slots, limits=LIMITS)
    ranked = sorted(clusters, key=lambda c: -score_candidate(c, slots))
    assert ranked[0].slots == (0, 1)


def test_single_value_overlap_is_flagged() -> None:
    from asterism.crosswalk_discover import candidate_flags

    slots = [
        _slot(0, "a", f"{NS}comp", ["only", "a1", "a2"]),
        _slot(1, "b", f"{NS}comp", ["only", "b1", "b2"]),
    ]
    limits = DiscoverLimits(min_shared_keys=1)
    clusters, _ = cluster_candidates(slots, limits=limits)
    assert "single_value_overlap" in candidate_flags(clusters[0], slots, limits=limits)


# ---------------------------------------------------------------------------
# Pure: naming (K13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("predicates", "expected"),
    [
        ([f"{NS}composition", f"{NS}composition"], "composition"),
        ([f"{NS}hasComposition", f"{NS}composition"], "composition"),
        ([f"{NS}compositionString", f"{NS}compositionFormula"], "composition"),
        ([f"{NS}crystal_system", f"{NS}crystalSystem"], "crystal_system"),
        # Nothing in common: the spelled-out term beats the abbreviation.
        ([f"{NS}comp", f"{NS}formula", f"{NS}composition"], "composition"),
        ([f"{NS}組成", f"{NS}組成式"], "shared_value_1"),
    ],
)
def test_concept_name_is_derived_from_the_predicate_names(predicates, expected) -> None:
    assert derive_concept_name(predicates, taken=[], rank=0) == expected


def test_concept_name_disambiguates_against_names_already_used() -> None:
    assert derive_concept_name([f"{NS}comp"], taken=["comp"], rank=1) == "comp_2"


def test_concept_terms_and_perspective_id_follow_the_mint_rule() -> None:
    cls, link = concept_terms("crystal_system")
    assert cls.endswith("#CrystalSystem")
    assert link.endswith("#hasCrystalSystem")
    assert perspective_id_for("crystal_system") == "crystal-system"


def test_samples_show_the_disagreeing_spellings_first() -> None:
    # "Bi₂Te₃ over here, Bi2Te3 over there" is what makes a candidate obvious.
    slots = [
        _slot(0, "a", f"{NS}comp", ["Bi₂Te₃", "PbTe"]),
        _slot(1, "b", f"{NS}formula", ["Bi2Te3", "PbTe"]),
    ]
    clusters, _ = cluster_candidates(slots, limits=LIMITS)
    cluster = clusters[0]
    intern = {}
    for slot in slots:
        for kid, raw in slot.raw_by_key[cluster.normalizer].items():
            intern.setdefault(kid, raw)
    samples = pick_samples(cluster, slots, intern, limit=5)
    assert set(samples[0]["raw"].values()) == {"Bi₂Te₃", "Bi2Te3"}


def test_samples_put_punctuation_led_values_last() -> None:
    """記号始まりの値（データ入力の削除標識など）は同点の最後尾。

    素の辞書順昇順では `!` が英数字より先に来て、starrydata の「!!! Delete」
    「!!! del 0」…ばかりが証拠チップに並んだ（利用者報告 2026-09-02:
    composition の候補なのに化学組成が 1 つも見えない）。結合や件数は不変 —
    見せる順だけの規則。
    """
    junk = ["!!! Delete", "!!! del 0", "!!! del 1"]
    real = ["Bi2Te3", "PbTe"]
    slots = [
        _slot(0, "a", f"{NS}comp", junk + real),
        _slot(1, "b", f"{NS}formula", junk + real),
    ]
    clusters, _ = cluster_candidates(slots, limits=LIMITS)
    cluster = clusters[0]
    intern = {}
    for slot in slots:
        for kid, raw in slot.raw_by_key[cluster.normalizer].items():
            intern.setdefault(kid, raw)
    samples = pick_samples(cluster, slots, intern, limit=3)
    shown = [next(iter(s["raw"].values())) for s in samples]
    assert shown[:2] == ["Bi2Te3", "PbTe"]
    # 記号始まりも消えはしない（実データ）— 後ろに並ぶだけ。
    assert shown[2].startswith("!!!")


# ---------------------------------------------------------------------------
# I/O: the scan against a real rdflib store
# ---------------------------------------------------------------------------


class _DatasetClient:
    """OxigraphClient stand-in over an rdflib Dataset. Records writes so a test can
    prove discovery never performs one."""

    def __init__(self, ds: rdflib.Dataset) -> None:
        self.ds = ds
        self.writes: list[str] = []

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:  # pragma: no cover - must not run
        self.writes.append(update)
        self.ds.update(update)

    async def post_turtle_bytes(
        self, payload: bytes, graph_iri: str | None = None
    ) -> int:  # pragma: no cover - must not run
        self.writes.append(graph_iri or "")
        return len(payload)


def _seed(ds: rdflib.Dataset, dataset_id: str, predicate: str, values: list[str]) -> str:
    key = substrate.canonical_graph_iri(dataset_id)
    g = ds.graph(rdflib.URIRef(key))
    for i, raw in enumerate(values):
        g.add(
            (
                rdflib.URIRef(f"urn:{dataset_id}:{i}"),
                rdflib.URIRef(predicate),
                rdflib.Literal(raw),
            )
        )
    ds.update(
        f"INSERT DATA {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f'<{key}> <{substrate.STATUS_PREDICATE}> "promoted" }} }}'
    )
    return key


def _ds(*ids: str) -> list[DiscoverDataset]:
    return [DiscoverDataset(dataset_id=i, label=i, name=i.upper()) for i in ids]


async def test_discover_finds_the_join_across_three_datasets() -> None:
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe", "SnSe"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe", "ZnO"])
    _seed(store, "ds-c", f"{NS}composition", ["Bi2Te3", "PbTe"])

    result = await discover(_DatasetClient(store), _ds("ds-a", "ds-b", "ds-c"))

    assert len(result["candidates"]) == 1
    cand = result["candidates"][0]
    assert {p["dataset_id"] for p in cand["participants"]} == {"ds-a", "ds-b", "ds-c"}
    assert cand["matched"] == 2
    assert cand["concept"] == "composition"
    assert cand["samples"]  # the values themselves, as evidence
    # Every rung is reported, so the UI can say what folding bought.
    assert [t["normalizer"] for t in cand["normalizer_trials"]] == list(DiscoverLimits().ladder)
    # Nothing was folded here, so the conservative rung wins.
    assert cand["normalizer"] == "identity"


async def test_discover_resolves_predicate_and_concept_labels_when_all_agree() -> None:
    # XW-01: the human word the design chose, not the predicate IRI's local name —
    # and when every participant agrees on that word, it becomes the concept_label.
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe", "SnSe"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe", "ZnO"])

    def labels(dataset_id: str, predicate: str) -> str | None:
        return "組成" if predicate in (f"{NS}comp", f"{NS}formula") else None

    result = await discover(
        _DatasetClient(store), _ds("ds-a", "ds-b"), predicate_label_of=labels
    )

    cand = result["candidates"][0]
    assert cand["concept_label"] == "組成"
    assert {p["predicate_label"] for p in cand["participants"]} == {"組成"}


async def test_discover_predicate_label_falls_back_to_local_name_when_unresolved() -> None:
    # No resolver at all (today's behavior) — and a resolver present but returning
    # nothing for THIS predicate — both fall back to the local name, never a blank.
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe", "SnSe"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe", "ZnO"])

    result = await discover(_DatasetClient(store), _ds("ds-a", "ds-b"))
    cand = result["candidates"][0]
    assert {p["predicate_label"] for p in cand["participants"]} == {"comp", "formula"}
    assert cand["concept_label"] == ""

    result2 = await discover(
        _DatasetClient(store), _ds("ds-a", "ds-b"), predicate_label_of=lambda d, p: None
    )
    cand2 = result2["candidates"][0]
    assert {p["predicate_label"] for p in cand2["participants"]} == {"comp", "formula"}
    assert cand2["concept_label"] == ""


async def test_discover_concept_label_joins_disagreeing_participant_labels() -> None:
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe", "SnSe"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe", "ZnO"])

    def labels(dataset_id: str, predicate: str) -> str | None:
        return {"ds-a": "組成", "ds-b": "化学式"}.get(dataset_id)

    result = await discover(
        _DatasetClient(store), _ds("ds-a", "ds-b"), predicate_label_of=labels
    )
    cand = result["candidates"][0]
    assert cand["concept_label"] == "組成 / 化学式"


async def test_discover_folds_spelling_differences_and_shows_them_as_evidence() -> None:
    # The case the feature exists for: the same composition written two ways. Folding
    # has to actually buy matches (identity finds one, nfkc finds two), and the sample
    # must put the disagreeing spellings side by side — that is what convinces a human.
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}composition", ["Bi₂Te₃", "PbTe", "SnSe"])
    _seed(store, "ds-b", f"{NS}composition", ["Bi2Te3", "PbTe", "ZnO"])

    result = await discover(_DatasetClient(store), _ds("ds-a", "ds-b"))

    cand = result["candidates"][0]
    assert cand["normalizer"] == "nfkc"
    trials = {t["normalizer"]: t["matched"] for t in cand["normalizer_trials"]}
    assert trials["identity"] == 1 and trials["nfkc"] == 2
    assert set(cand["samples"][0]["raw"].values()) == {"Bi₂Te₃", "Bi2Te3"}


RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


def _seed_kinds(ds: rdflib.Dataset, dataset_id: str, rows: list[tuple[str, str, str]]) -> None:
    """``(kind local name, predicate, value)`` rows, one typed entity each."""
    key = substrate.canonical_graph_iri(dataset_id)
    g = ds.graph(rdflib.URIRef(key))
    for i, (kind, predicate, raw) in enumerate(rows):
        e = rdflib.URIRef(f"urn:{dataset_id}:{i}")
        g.add((e, rdflib.RDF.type, rdflib.URIRef(f"{NS}{kind}")))
        g.add((e, rdflib.URIRef(predicate), rdflib.Literal(raw)))
    ds.update(
        f"INSERT DATA {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f'<{key}> <{substrate.STATUS_PREDICATE}> "promoted" }} }}'
    )


async def test_discover_profiles_a_shared_naming_predicate_per_kind() -> None:
    """Every value catalog of a design stores its value under ``rdfs:label``. Read
    per predicate, that field is DOIs + compositions + units at once and the concept
    gets called "label"; read per (kind, predicate) it is the Composition kind's own
    field, named after the kind and joined only on that kind's entities
    (crosswalk-kind-scoped-fields.md)."""
    store = rdflib.Dataset()
    _seed_kinds(
        store,
        "ds-a",
        [
            ("Composition", RDFS_LABEL, "Bi2Te3"),
            ("Composition", RDFS_LABEL, "PbTe"),
            ("Composition", RDFS_LABEL, "SnSe"),
            ("Doi", RDFS_LABEL, "10.1000/x1"),
            ("Doi", RDFS_LABEL, "10.1000/x2"),
        ],
    )
    _seed_kinds(
        store,
        "ds-b",
        [
            ("Sample", f"{NS}composition", "Bi2Te3"),
            ("Sample", f"{NS}composition", "PbTe"),
            ("Sample", f"{NS}composition", "ZnO"),
        ],
    )
    seen: list[tuple[str, str, str | None]] = []

    def field_label_of(dataset_id: str, predicate: str, subject_class: str | None) -> str | None:
        seen.append((dataset_id, predicate, subject_class))
        if subject_class == f"{NS}Composition":
            return "試料化学組成"
        return None

    result = await discover(
        _DatasetClient(store),
        _ds("ds-a", "ds-b"),
        field_label_of=field_label_of,
        class_label_of=lambda _ds, cls: "組成" if cls.endswith("Composition") else None,
    )
    assert len(result["candidates"]) == 1
    cand = result["candidates"][0]
    assert cand["concept"] == "composition"  # the kind's word, never "label"
    by_ds = {p["dataset_id"]: p for p in cand["participants"]}
    a = by_ds["ds-a"]
    assert a["predicate"] == RDFS_LABEL
    assert a["subject_class"] == f"{NS}Composition"
    assert a["subject_class_label"] == "組成"
    assert a["predicate_label"] == "試料化学組成"  # resolved for THIS kind's field
    assert a["distinct_values"] == 3  # the Doi kind's labels never entered the slot
    assert by_ds["ds-b"]["subject_class"] == f"{NS}Sample"
    assert ("ds-a", RDFS_LABEL, f"{NS}Composition") in seen
    # The buildable config carries the kind, so the hub joins only that kind.
    parts = cand["build_config"]["concepts"][0]["participants"]
    assert {p["dataset_id"]: p.get("subject_class") for p in parts} == {
        "ds-a": f"{NS}Composition",
        "ds-b": f"{NS}Sample",
    }
    parse_config(cand["build_config"])  # still a valid config


async def test_discover_keeps_untyped_subjects_as_kindless_slots() -> None:
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe"])
    result = await discover(_DatasetClient(store), _ds("ds-a", "ds-b"))
    cand = result["candidates"][0]
    assert all(p["subject_class"] is None for p in cand["participants"])
    assert all(p["subject_class_label"] is None for p in cand["participants"])
    assert "subject_class" not in cand["build_config"]["concepts"][0]["participants"][0]


async def test_discover_skips_datasets_that_are_not_promoted() -> None:
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe"])
    # ds-b exists but was never promoted: it must not enter a citable hub.
    g = store.graph(rdflib.URIRef(substrate.canonical_graph_iri("ds-b")))
    g.add((rdflib.URIRef("urn:b:1"), rdflib.URIRef(f"{NS}comp"), rdflib.Literal("Bi2Te3")))

    result = await discover(_DatasetClient(store), _ds("ds-a", "ds-b"))

    assert result["candidates"] == []
    assert {s["dataset_id"]: s["reason"] for s in result["scanned"]["datasets_skipped"]} == {
        "ds-b": "not_promoted"
    }


async def test_discover_reads_the_live_graph_not_the_key_graph() -> None:
    # Same resolution build_hub uses: a versioned dataset's values live in the version
    # graph, and reading the key graph would silently compare the wrong data.
    store = rdflib.Dataset()
    key = _seed(store, "ds-a", f"{NS}comp", ["stale-1", "stale-2"])
    live = f"{key}/v2"
    g = store.graph(rdflib.URIRef(live))
    for i, raw in enumerate(["Bi2Te3", "PbTe"]):
        g.add((rdflib.URIRef(f"urn:v2:{i}"), rdflib.URIRef(f"{NS}comp"), rdflib.Literal(raw)))
    store.update(
        f"INSERT DATA {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f"<{key}> <{substrate.LIVE_GRAPH_PREDICATE}> <{live}> . "
        f'<{live}> <{substrate.STATUS_PREDICATE}> "promoted" }} }}'
    )
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe"])

    result = await discover(_DatasetClient(store), _ds("ds-a", "ds-b"))

    assert result["scanned"]["datasets"][0]["live_graph"] == live
    assert result["candidates"][0]["matched"] == 2


async def test_discover_discloses_that_it_stopped_reading_predicates() -> None:
    # "found nothing" and "stopped looking" must never be indistinguishable.
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe", "SnSe"])
    _seed(store, "ds-a", f"{NS}note", ["x", "y"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe", "SnSe"])

    result = await discover(
        _DatasetClient(store),
        _ds("ds-a", "ds-b"),
        limits=DiscoverLimits(max_predicates_per_dataset=1),
    )

    scanned = {d["dataset_id"]: d for d in result["scanned"]["datasets"]}
    assert scanned["ds-a"]["predicates_truncated"] is True
    assert scanned["ds-a"]["predicates_scanned"] == 1


async def test_discover_discloses_that_it_stopped_reading_values() -> None:
    # A match count computed from a partial read is a LOWER BOUND, and the card has
    # to be able to say so.
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe", "SnSe"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe", "SnSe"])

    result = await discover(
        _DatasetClient(store),
        _ds("ds-a", "ds-b"),
        limits=DiscoverLimits(max_values_per_predicate=2),
    )

    cand = result["candidates"][0]
    assert all(p["values_truncated"] for p in cand["participants"])
    assert "values_truncated" in cand["flags"]


async def test_discover_discloses_why_a_predicate_was_excluded() -> None:
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe"])
    _seed(store, "ds-a", f"{NS}zt", ["1.4", "0.9", "2.1"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe"])

    result = await discover(_DatasetClient(store), _ds("ds-a", "ds-b"))

    excluded = {
        e["iri"]: e["reason"]
        for d in result["scanned"]["datasets"]
        for e in d["predicates_excluded"]
    }
    assert excluded[f"{NS}zt"] == "numeric"


async def test_discover_never_writes() -> None:
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe"])
    client = _DatasetClient(store)

    await discover(client, _ds("ds-a", "ds-b"))

    assert client.writes == []


async def test_discover_stops_when_cancelled_and_says_so() -> None:
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe"])
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # allow the first dataset, then stop

    result = await discover(_DatasetClient(store), _ds("ds-a", "ds-b"), should_cancel=cancel)

    assert result["cancelled"] is True
    assert result["candidates"] == []  # partial, and it does not pretend otherwise


async def test_discovered_candidate_config_parses_as_a_crosswalk_config() -> None:
    # The contract that makes "connect these" a one-liner: what discovery returns is
    # already a buildable config, not a hint that needs assembling.
    store = rdflib.Dataset()
    _seed(store, "ds-a", f"{NS}comp", ["Bi2Te3", "PbTe"])
    _seed(store, "ds-b", f"{NS}formula", ["Bi2Te3", "PbTe"])

    result = await discover(_DatasetClient(store), _ds("ds-a", "ds-b"))

    config = parse_config(result["candidates"][0]["build_config"])
    assert [p.dataset_id for p in config.concepts[0].participants] == ["ds-a", "ds-b"]
    assert config.concepts[0].normalizer == result["candidates"][0]["normalizer"]
