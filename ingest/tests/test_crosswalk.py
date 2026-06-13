"""Crosswalk hub builder (ADR crosswalk-hub.md): pure, multi-concept, provenance.

These tests feed OBSERVATIONS directly (no triplestore) — the builder is I/O-free,
so the join semantics, normalization, growth and provenance are tested in isolation.
"""

from __future__ import annotations

from asterism.crosswalk import (
    NORMALIZERS,
    XW,
    Concept,
    CrosswalkConfig,
    Rule,
    build_turtle,
    normalize_casefold,
    normalize_composition,
    normalize_element_canonical,
    normalize_loose_text,
    normalize_nfkc,
    normalize_whitespace,
)

COMPOSITION = Concept(
    name="composition",
    class_iri=f"{XW}Composition",
    link_predicate=f"{XW}hasComposition",
    normalizer="composition",
    rules=(Rule("starrydata", "sd:comp"), Rule("materials_project", "mp:formula")),
)


def _build(config, observations):
    return build_turtle(
        config, observations, activity_iri="urn:act", built_at="2026-06-10T00:00:00+00:00"
    )


def test_normalize_folds_subscripts_and_whitespace() -> None:
    assert normalize_composition("Bi₂Te₃") == "Bi2Te3"
    assert normalize_composition(" Bi2 Te3 ") == "Bi2Te3"
    # conservative: case kept, no element reorder
    assert normalize_composition("Te3Bi2") == "Te3Bi2"


def test_generic_text_normalizers_fold_one_variation_each() -> None:
    # casefold: case-insensitive, trimmed; case-distinct -> NOT for compositions.
    assert normalize_casefold(" Iron Oxide ") == "iron oxide"
    assert normalize_casefold("FeO") == normalize_casefold("feo")
    # whitespace: collapse internal runs + trim; nothing else folded.
    assert normalize_whitespace("  Bi2   Te3 ") == "Bi2 Te3"
    assert normalize_whitespace("Bi2Te3") == "Bi2Te3"
    # nfkc: full-width <-> half-width / compatibility forms + trim. The full-/half-
    # width inputs are intentional (RUF001 ambiguous-char warning suppressed).
    assert normalize_nfkc("ＡＢＣ１２３") == "ABC123"  # noqa: RUF001
    assert normalize_nfkc("ｶﾀｶﾅ") == "カタカナ"
    # loose_text: NFKC + casefold + collapse whitespace, combined.
    assert normalize_loose_text("  Ｔａ２Ｏ５  ") == "ta2o5"  # noqa: RUF001
    assert normalize_loose_text("Bi2  Te3") == normalize_loose_text("bi2 te3")


def test_generic_text_normalizers_do_not_reorder_or_drop_tokens() -> None:
    # Conservative: distinct strings stay distinct (no token reorder / drop).
    assert normalize_loose_text("Te3Bi2") != normalize_loose_text("Bi2Te3")
    assert normalize_casefold("AB") != normalize_casefold("BA")
    assert normalize_whitespace("Bi2Te3") != normalize_whitespace("Bi3Te2")


def test_all_named_normalizers_are_registered() -> None:
    # The closed, vetted set the UI offers (generic core + materials pack).
    assert set(NORMALIZERS) == {
        "identity",
        "casefold",
        "whitespace",
        "nfkc",
        "loose_text",
        "composition",
        "element_canonical",
    }


def test_mints_one_shared_entity_with_links_from_both_datasets() -> None:
    obs = {
        ("composition", "starrydata"): [("sd:s1", "Bi₂Te₃"), ("sd:s2", "PbTe")],
        ("composition", "materials_project"): [("mp:m1", "Bi2Te3")],
    }
    b = _build(CrosswalkConfig((COMPOSITION,)), obs)
    # Bi2Te3 is shared (both datasets, even across the subscript variant); PbTe isn't.
    assert b.shared["composition"] == ["Bi2Te3"]
    assert "/crosswalk/resource/composition/Bi2Te3>" in b.turtle
    has = f"<{XW}hasComposition>"
    assert f"<sd:s1> {has}" in b.turtle
    assert f"<mp:m1> {has}" in b.turtle
    # PbTe (only starrydata) is not minted
    assert "/composition/PbTe>" not in b.turtle
    assert b.links["composition"] == {"starrydata": 1, "materials_project": 1}


def test_provenance_activity_and_generation() -> None:
    obs = {
        ("composition", "starrydata"): [("sd:s1", "ZnO")],
        ("composition", "materials_project"): [("mp:m1", "ZnO")],
    }
    b = _build(CrosswalkConfig((COMPOSITION,)), obs)
    assert "prov:Activity" in b.turtle
    assert 'xw:participatingDatasets "materials_project, starrydata"' in b.turtle
    assert "prov:wasGeneratedBy <urn:act>" in b.turtle


def test_growth_adding_a_dataset_grows_the_same_hub() -> None:
    # v1: starrydata x MP share exactly Bi2Te3.
    v1 = _build(
        CrosswalkConfig((COMPOSITION,)),
        {
            ("composition", "starrydata"): [("sd:s1", "Bi2Te3"), ("sd:c1", "Ba8Ge43")],
            ("composition", "materials_project"): [("mp:m1", "Bi2Te3")],
        },
    )
    assert v1.shared["composition"] == ["Bi2Te3"]

    # v2: add a third dataset (rule) sharing Ba8Ge43 with starrydata -> hub grows.
    grown = Concept(
        name="composition",
        class_iri=COMPOSITION.class_iri,
        link_predicate=COMPOSITION.link_predicate,
        normalizer="composition",
        rules=(*COMPOSITION.rules, Rule("demo", "sd:comp")),
    )
    v2 = _build(
        CrosswalkConfig((grown,)),
        {
            ("composition", "starrydata"): [("sd:s1", "Bi2Te3"), ("sd:c1", "Ba8Ge43")],
            ("composition", "materials_project"): [("mp:m1", "Bi2Te3")],
            ("composition", "demo"): [("demo:d1", "Ba8Ge43")],
        },
    )
    assert v2.shared["composition"] == ["Ba8Ge43", "Bi2Te3"]  # grew from 1 -> 2
    assert v2.links["composition"]["demo"] == 1


def test_multi_concept_mints_each_concept() -> None:
    space_group = Concept(
        name="space_group",
        class_iri=f"{XW}SpaceGroup",
        link_predicate=f"{XW}hasSpaceGroup",
        normalizer="identity",
        rules=(Rule("materials_project", "mp:sg"), Rule("other", "o:sg")),
    )
    config = CrosswalkConfig((COMPOSITION, space_group))
    obs = {
        ("composition", "starrydata"): [("sd:s1", "PbTe")],
        ("composition", "materials_project"): [("mp:m1", "PbTe")],
        ("space_group", "materials_project"): [("mp:m1", "Fm-3m")],
        ("space_group", "other"): [("o:x1", "Fm-3m")],
    }
    b = _build(config, obs)
    assert b.shared["composition"] == ["PbTe"]
    assert b.shared["space_group"] == ["Fm-3m"]
    assert f"<{XW}SpaceGroup>" in b.turtle
    assert "/crosswalk/resource/space_group/Fm-3m>" in b.turtle


def test_singleton_value_is_not_shared() -> None:
    obs = {
        ("composition", "starrydata"): [("sd:s1", "OnlyHere")],
        ("composition", "materials_project"): [("mp:m1", "Different")],
    }
    b = _build(CrosswalkConfig((COMPOSITION,)), obs)
    assert b.shared["composition"] == []
    assert b.links["composition"] == {}


def test_per_link_provenance_records_raw_string_and_normalizer() -> None:
    # The SAME normalized composition is reached from DIFFERENT raw spellings; the
    # per-link provenance must record EACH raw (the audit-relevant join claim), not
    # just the normalized key.
    obs = {
        ("composition", "starrydata"): [("sd:s1", "Bi₂Te₃")],  # unicode subscripts
        ("composition", "materials_project"): [("mp:m1", "Bi2Te3")],  # ascii
    }
    b = _build(CrosswalkConfig((COMPOSITION,)), obs)
    t = b.turtle
    assert "a xw:CrosswalkLink" in t
    # Each link keeps its ORIGINAL raw spelling + the normalizer that joined it.
    assert 'xw:sourceValue "Bi₂Te₃"' in t  # starrydata's raw, not the "Bi2Te3" key
    assert 'xw:sourceValue "Bi2Te3"' in t  # materials_project's raw
    assert 'xw:normalizer "composition"' in t
    assert "xw:linkSubject <sd:s1>" in t
    assert "xw:linkSubject <mp:m1>" in t
    # The link node points at the shared composition entity + the build activity.
    assert "/crosswalk/resource/composition/Bi2Te3>" in t  # linkObject target exists
    assert "xw:linkObject <" in t
    assert t.count("prov:wasGeneratedBy <urn:act>") >= 3  # 1 comp + 2 link nodes
    # Deterministic link-node IRI per (key, entity).
    assert "/crosswalk/resource/composition/link/Bi2Te3/sd%3As1>" in t


def test_per_link_provenance_can_be_disabled() -> None:
    obs = {
        ("composition", "starrydata"): [("sd:s1", "Bi₂Te₃")],
        ("composition", "materials_project"): [("mp:m1", "Bi2Te3")],
    }
    b = _build(CrosswalkConfig((COMPOSITION,), per_link_provenance=False), obs)
    t = b.turtle
    # No per-link nodes; the primary queryable link + per-build provenance remain.
    assert "xw:CrosswalkLink" not in t
    assert "xw:sourceValue" not in t
    assert f"<sd:s1> <{XW}hasComposition>" in t
    assert "prov:wasGeneratedBy <urn:act>" in t  # the minted composition still has it


# --- element-canonical normalizer (productization ①: Bi2Te3 == Te3Bi2) -----------

ELEMENT_CANONICAL = Concept(
    name="composition",
    class_iri=f"{XW}Composition",
    link_predicate=f"{XW}hasComposition",
    normalizer="element_canonical",
    rules=(Rule("starrydata", "sd:comp"), Rule("materials_project", "mp:formula")),
)


def test_element_canonical_reorders_to_a_stable_key() -> None:
    # A chemical formula is a multiset — element order does not change the compound.
    assert normalize_element_canonical("Te3Bi2") == "Bi2Te3"
    assert normalize_element_canonical("Bi2Te3") == "Bi2Te3"
    assert normalize_element_canonical("Bi₂Te₃") == "Bi2Te3"  # folds subscripts too
    assert normalize_element_canonical("TePb") == normalize_element_canonical("PbTe")
    assert normalize_element_canonical("Sb1.5Bi0.5Te3") == "Bi0.5Sb1.5Te3"  # decimals kept


def test_element_canonical_is_case_safe_and_conservative() -> None:
    # Co (cobalt) must NOT collapse with C+O — case is significant.
    assert normalize_element_canonical("Co") == "Co"
    assert normalize_element_canonical("CO") == "CO"
    assert normalize_element_canonical("Co") != normalize_element_canonical("OC")
    # Counts are preserved verbatim — stoichiometry is NOT reduced.
    assert normalize_element_canonical("Bi4Te6") != normalize_element_canonical("Bi2Te3")
    # A non-formula / unparseable string falls back to composition (no reorder), so it
    # can never be wrongly merged with another.
    assert normalize_element_canonical("(Bi,Sb)2Te3") == normalize_composition("(Bi,Sb)2Te3")
    assert normalize_element_canonical("Xx2Yy3") == normalize_composition("Xx2Yy3")  # not elements
    assert normalize_element_canonical("sample-001") == normalize_composition("sample-001")


def test_element_canonical_joins_reordered_formulas_across_datasets() -> None:
    # With the plain composition key these would NOT share; element_canonical does.
    obs = {
        ("composition", "starrydata"): [("sd:s1", "Te3Bi2")],
        ("composition", "materials_project"): [("mp:m1", "Bi2Te3")],
    }
    plain = _build(CrosswalkConfig((COMPOSITION,)), obs)
    assert plain.shared["composition"] == []  # Te3Bi2 != Bi2Te3 under composition

    canon = _build(CrosswalkConfig((ELEMENT_CANONICAL,)), obs)
    assert canon.shared["composition"] == ["Bi2Te3"]  # joined via the canonical key
    assert canon.links["composition"] == {"starrydata": 1, "materials_project": 1}
    # per-link provenance records the ORIGINAL raw + the normalizer used
    assert 'xw:sourceValue "Te3Bi2"' in canon.turtle
    assert 'xw:normalizer "element_canonical"' in canon.turtle
