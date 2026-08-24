"""Tests for skeleton_annotate — deterministic evidence for the skeleton gate."""

from __future__ import annotations

from pathlib import Path

from asterism_step0.dialect import SourceDialect
from asterism_step0.skeleton_annotate import annotate_skeleton, apply_key_safety_fix

_PREFIXES = {
    "xr": "https://example.org/xrd/resource/",
    "xo": "https://example.org/xrd/ontology#",
}


def _skeleton(template: str, *, classes: list[str] | None = None, source: str = "xrd.csv") -> dict:
    return {
        "version": 1,
        "prefixes": dict(_PREFIXES),
        "maps": [
            {
                "name": "point",
                "source": source,
                "subject": {"template": template, "classes": classes or ["xo:DataPoint"]},
            }
        ],
    }


def _write_xrd(tmp_path: Path) -> Path:
    # 2θ repeats across scans — the production shape that collapses rows.
    p = tmp_path / "xrd.csv"
    p.write_text(
        "2θ (deg),intensity,scan_id\n"
        "10.00,120,S1\n"
        "10.02,135,S1\n"
        "10.00,98,S2\n"
        "10.02,101,S2\n"
        "10.04,77,S2\n",
        encoding="utf-8",
    )
    return p


def test_unique_key_passes_with_previews(tmp_path: Path) -> None:
    p = tmp_path / "samples.csv"
    p.write_text("sample_id,alloy\nS-1,WC\nS-2,TiN\n", encoding="utf-8")
    skeleton = _skeleton("xr:sample/{sample_id}", source="samples.csv")
    ann = annotate_skeleton(skeleton, [p])["maps"]["point"]
    assert ann["checkable"] is True
    assert ann["is_unique"] is True
    assert ann["colliding_rows"] == 0
    assert ann["collision_examples"] == []
    assert ann["key_candidates"] == []
    # Previews are prefix-expanded real IDs, in file order.
    assert ann["id_previews"] == [
        "https://example.org/xrd/resource/sample/S-1",
        "https://example.org/xrd/resource/sample/S-2",
    ]
    assert ann["expanded_template"] == "https://example.org/xrd/resource/sample/{sample_id}"


def test_non_unique_key_reports_collisions_and_candidates(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/{2θ (deg)}"), [p])["maps"]["point"]
    assert ann["checkable"] is True
    assert ann["is_unique"] is False
    assert ann["total_rows"] == 5
    assert ann["distinct_ids"] == 3
    assert ann["colliding_rows"] == 2
    # Concrete colliding rows, numbered as the file reads (header = line 1).
    first = ann["collision_examples"][0]
    assert first["key_values"] == {"2θ (deg)": "10.00"}
    assert first["line_numbers"] == [2, 4]
    # The duplicate ID is visible in the previews (rows 1 and 3 mint the same IRI).
    assert ann["id_previews"][0] == ann["id_previews"][2]
    # Fix candidates exist, and measurement-only ones (intensity) never rank first.
    candidates = ann["key_candidates"]
    assert candidates, "expected unique key candidates"
    assert candidates[0]["measurement_only"] is False


def test_unique_measurement_key_carries_caution(tmp_path: Path) -> None:
    """K7: a key of measurement values that happens to be unique TODAY is flagged
    (real dogfood: an AI-minted ID from 3.6E+1-style readings passed the green
    band on 13 accidentally-distinct rows) — and safer candidates still show."""
    p = tmp_path / "xrd.csv"
    p.write_text(
        "2θ (deg),intensity,scan_id\n10.00,120,S1\n10.02,135,S1\n10.04,98,S2\n",
        encoding="utf-8",
    )
    ann = annotate_skeleton(_skeleton("xr:point/{2θ (deg)}"), [p])["maps"]["point"]
    assert ann["is_unique"] is True
    assert ann["key_measurement_caution"] is True
    # Unlike the plain-unique case, the proven alternatives are still offered.
    assert ann["key_candidates"], "expected safer key candidates alongside the caution"
    assert any(not c["measurement_only"] for c in ann["key_candidates"])


def test_unique_text_key_has_no_measurement_caution(tmp_path: Path) -> None:
    p = tmp_path / "samples.csv"
    p.write_text("sample_id,alloy\nS-1,WC\nS-2,TiN\n", encoding="utf-8")
    ann = annotate_skeleton(_skeleton("xr:sample/{sample_id}", source="samples.csv"), [p])["maps"][
        "point"
    ]
    assert ann["is_unique"] is True
    assert ann["key_measurement_caution"] is False
    assert ann["key_candidates"] == []


def test_composite_key_template_is_checked_as_a_tuple(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/{scan_id}/{2θ (deg)}"), [p])["maps"]["point"]
    assert ann["key_columns"] == ["scan_id", "2θ (deg)"]
    assert ann["is_unique"] is True
    assert ann["id_previews"][0] == "https://example.org/xrd/resource/point/S1/10.00"


def test_missing_column_is_reported_not_guessed(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/{two_theta}"), [p])["maps"]["point"]
    assert ann["checkable"] is False
    assert ann["reason"] == "missing-columns"
    assert ann["missing_columns"] == ["two_theta"]


def test_undeclared_prefix_is_flagged(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    skeleton = _skeleton("xr:point/{scan_id}", classes=["prov:Entity", "xo:DataPoint"])
    ann = annotate_skeleton(skeleton, [p])["maps"]["point"]
    assert ann["undeclared_prefixes"] == ["prov"]
    expanded = {e["curie"]: e["iri"] for e in ann["expanded_classes"]}
    assert expanded["xo:DataPoint"] == "https://example.org/xrd/ontology#DataPoint"
    assert expanded["prov:Entity"] == "prov:Entity"  # unexpandable stays as-is


def test_constant_subject_is_out_of_scope(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    skeleton = {
        "version": 1,
        "prefixes": dict(_PREFIXES),
        "maps": [
            {
                "name": "doc",
                "source": "xrd.csv",
                "subject": {"constant": "xr:doc/1", "classes": ["xo:Doc"]},
            }
        ],
    }
    ann = annotate_skeleton(skeleton, [p])["maps"]["doc"]
    assert ann["checkable"] is False
    assert ann["reason"] == "constant"
    assert ann["expanded_template"] == "https://example.org/xrd/resource/doc/1"


def test_placeholderless_template_collapses_everything(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/only-one"), [p])["maps"]["point"]
    assert ann["checkable"] is True
    assert ann["is_unique"] is False
    assert ann["distinct_ids"] == 1
    assert ann["colliding_rows"] == 4  # 5 rows → 1 ID


def test_source_not_found_degrades(tmp_path: Path) -> None:
    p = _write_xrd(tmp_path)
    ann = annotate_skeleton(_skeleton("xr:point/{scan_id}", source="other.csv"), [p])
    assert ann["maps"]["point"]["checkable"] is False
    assert ann["maps"]["point"]["reason"] == "source-not-found"


def test_dialect_read_and_line_numbers_include_preamble(tmp_path: Path) -> None:
    # Instrument text: 2 preamble lines + tab-separated table (the user's case).
    p = tmp_path / "xrd-scan.txt"
    p.write_text(
        "# Instrument: XRD-9000\n"
        "# Date: 2026-07-13\n"
        "2θ (deg)\tintensity\tscan_id\n"
        "10.00\t120\tS1\n"
        "10.02\t135\tS1\n"
        "10.00\t98\tS2\n",
        encoding="utf-8",
    )
    dialect = SourceDialect(delimiter="\t", skip_rows=2)
    skeleton = _skeleton("xr:point/{2θ (deg)}", source="xrd-scan.txt")
    ann = annotate_skeleton(skeleton, [p], dialects={"xrd-scan.txt": dialect})["maps"]["point"]
    assert ann["checkable"] is True
    assert ann["is_unique"] is False
    # File line numbers count the 2 preamble lines + the header: data starts at 4.
    assert ann["collision_examples"][0]["line_numbers"] == [4, 6]
    assert ann["id_previews"][0] == "https://example.org/xrd/resource/point/10.00"


def test_placeholder_prefixes_flagged_at_top_level(tmp_path: Path) -> None:
    """ADR instance-iri-base.md: the gate evidence names prefixes minted on a
    placeholder domain (this file's fixtures deliberately sit on example.org),
    skeleton-level — the gate shows it before the paid continue run."""
    p = tmp_path / "samples.csv"
    p.write_text("sample_id,alloy\nS-1,WC\n", encoding="utf-8")
    out = annotate_skeleton(_skeleton("xr:sample/{sample_id}", source="samples.csv"), [p])
    flagged = {e["prefix"]: e["iri"] for e in out["placeholder_prefixes"]}
    assert set(flagged) == {"xr", "xo"}
    assert flagged["xr"] == "https://example.org/xrd/resource/"


def test_instance_and_invalid_namespaces_not_flagged(tmp_path: Path) -> None:
    p = tmp_path / "samples.csv"
    p.write_text("sample_id,alloy\nS-1,WC\n", encoding="utf-8")
    skeleton = _skeleton("xr:sample/{sample_id}", source="samples.csv")
    skeleton["prefixes"] = {
        "xr": "https://asterism.invalid/datasets/xrd/resource/",
        "xo": "https://data.lab.jp/asterism/datasets/xrd/ontology#",
        "schema": "https://schema.org/",
    }
    out = annotate_skeleton(skeleton, [p])
    assert out["placeholder_prefixes"] == []


def test_class_named_after_numeric_key_column_carries_caution(tmp_path: Path) -> None:
    """The ZEM naming trap: a measurement-only key whose CLASS is named after
    the numeric key column ("Temperature" over key {Measurement temp.(C)}) —
    the row identity mislabeled as one of its measurements. Token match is
    prefix-tolerant (temp ≈ temperature)."""
    p = tmp_path / "zem.csv"
    p.write_text(
        "Measurement temp.(C),Resistivity(Ohm m)\n"
        "3.636740E+1,1.294886E-6\n"
        "6.029985E+1,1.381926E-6\n",
        encoding="utf-8",
    )
    ann = annotate_skeleton(
        _skeleton("xr:t/{Measurement temp.(C)}", classes=["xo:Temperature"], source="zem.csv"),
        [p],
    )["maps"]["point"]
    assert ann["key_measurement_caution"] is True
    assert ann["class_numeric_key_caution"] == [
        {"class": "xo:Temperature", "column": "Measurement temp.(C)", "token": "temp"}
    ]


def test_row_class_over_mixed_key_has_no_class_caution(tmp_path: Path) -> None:
    """A legitimate row class over a mixed (text+numeric) key never triggers the
    naming caution — even though "Measurement" shares a token with the numeric
    column ("Measurement temp.(C)"), the key is not measurement-only."""
    p = tmp_path / "zem.csv"
    p.write_text(
        "Sample,Measurement temp.(C)\nA,3.6E+1\nA,6.0E+1\n",
        encoding="utf-8",
    )
    ann = annotate_skeleton(
        _skeleton(
            "xr:m/{Sample}/{Measurement temp.(C)}",
            classes=["xo:Measurement"],
            source="zem.csv",
        ),
        [p],
    )["maps"]["point"]
    assert ann["key_measurement_caution"] is False
    assert ann["class_numeric_key_caution"] == []


def test_unrelated_class_name_over_numeric_key_has_no_class_caution(tmp_path: Path) -> None:
    """K7 alone (numeric-only key) does not imply the naming trap: a class name
    sharing no token with the key column stays clean (only the K7 caution shows)."""
    p = tmp_path / "xrd.csv"
    p.write_text("2theta,intensity\n10.00,120\n10.02,135\n", encoding="utf-8")
    ann = annotate_skeleton(
        _skeleton("xr:p/{2theta}", classes=["xo:DiffractionPoint"], source="xrd.csv"),
        [p],
    )["maps"]["point"]
    assert ann["key_measurement_caution"] is True
    assert ann["class_numeric_key_caution"] == []


def _write_reference_card(tmp_path: Path) -> Path:
    """The XRD reference-card shape: file-scoped metadata columns (No, Name)
    repeated on every row + per-row peak columns (2theta, d, I, (hkl))."""
    p = tmp_path / "card.csv"
    p.write_text(
        "No,Name,2theta,d,I,(hkl)\n"
        '03-065-2664,Aluminum Vanadium,21.34,4.161,5.0,"(0,0,2)"\n'
        '03-065-2664,Aluminum Vanadium,25.87,3.441,11.5,"(1,0,1)"\n'
        '03-065-2664,Aluminum Vanadium,33.51,2.672,2.7,"(1,1,0)"\n',
        encoding="utf-8",
    )
    return p


def _card_skeleton() -> dict:
    return {
        "version": 1,
        "prefixes": dict(_PREFIXES),
        "maps": [
            {
                "name": "sample",
                "source": "card.csv",
                "subject": {"template": "xr:sample/{No}", "classes": ["xo:Material"]},
            },
            {
                "name": "peak",
                "source": "card.csv",
                "subject": {"template": "xr:peak/{2theta}", "classes": ["xo:DiffractionPeak"]},
            },
        ],
    }


def test_singleton_collapse_classified_with_entity_card(tmp_path: Path) -> None:
    """All rows merging into ONE ID is the file-scoped metadata pattern, not the
    collision accident: classified ``singleton``, and the card shows the merge —
    stable columns as properties, per-row columns named as another map's turf."""
    p = _write_reference_card(tmp_path)
    ann = annotate_skeleton(_card_skeleton(), [p])["maps"]["sample"]
    assert ann["is_unique"] is False  # unchanged raw fact (backward compat)
    assert ann["collapse_kind"] == "singleton"
    card = ann["entity_preview"]
    assert card["id"] == "https://example.org/xrd/resource/sample/03-065-2664"
    assert card["row_count"] == 3
    assert card["entity_count"] == 1
    # Key column leads, then the other stable value; nothing conflicts.
    assert card["properties"] == [
        {"column": "No", "value": "03-065-2664"},
        {"column": "Name", "value": "Aluminum Vanadium"},
    ]
    # Per-row columns are NOT conflicts on a singleton — they belong elsewhere.
    assert card["varying_columns"] == ["2theta", "d", "I", "(hkl)"]
    assert card["omitted_columns"] == 0


def test_partial_collapse_shows_overwrite_conflict(tmp_path: Path) -> None:
    """The real accident: a key that merges SOME rows. The card picks the largest
    colliding group and shows the fighting values with file line numbers."""
    p = tmp_path / "scans.csv"
    p.write_text(
        "scan,operator,2theta\nS1,Alice,10.0\nS1,Bob,10.2\nS2,Carol,10.4\n",
        encoding="utf-8",
    )
    skeleton = _skeleton("xr:scan/{scan}", source="scans.csv")
    ann = annotate_skeleton(skeleton, [p])["maps"]["point"]
    assert ann["collapse_kind"] == "partial"
    card = ann["entity_preview"]
    assert card["id"] == "https://example.org/xrd/resource/scan/S1"
    assert card["row_count"] == 2
    assert card["entity_count"] == 2
    by_col = {prop["column"]: prop for prop in card["properties"]}
    assert by_col["scan"] == {"column": "scan", "value": "S1"}
    assert by_col["operator"]["conflict"] is True
    assert by_col["operator"]["values"] == [
        {"value": "Alice", "line": 2},
        {"value": "Bob", "line": 3},
    ]
    assert card["varying_columns"] == []


def test_unique_map_card_shows_first_entity_and_count(tmp_path: Path) -> None:
    p = tmp_path / "samples.csv"
    p.write_text("sample_id,alloy\nS-1,WC\nS-2,TiN\n", encoding="utf-8")
    ann = annotate_skeleton(_skeleton("xr:sample/{sample_id}", source="samples.csv"), [p])["maps"][
        "point"
    ]
    assert ann["collapse_kind"] == "unique"
    card = ann["entity_preview"]
    assert card["id"] == "https://example.org/xrd/resource/sample/S-1"
    assert card["row_count"] == 1
    assert card["entity_count"] == 2
    assert card["properties"] == [
        {"column": "sample_id", "value": "S-1"},
        {"column": "alloy", "value": "WC"},
    ]


def test_measurement_key_carries_reference_risk(tmp_path: Path) -> None:
    """The citation consequence of a measured-value ID, machine-readable."""
    p = tmp_path / "xrd.csv"
    p.write_text(
        "2θ (deg),intensity,scan_id\n10.00,120,S1\n10.02,135,S1\n10.04,98,S2\n",
        encoding="utf-8",
    )
    ann = annotate_skeleton(_skeleton("xr:point/{2θ (deg)}"), [p])["maps"]["point"]
    assert {"kind": "measurement-id", "columns": ["2θ (deg)"]} in ann["reference_risks"]


def test_scope_missing_risk_and_scoped_candidates(tmp_path: Path) -> None:
    """Append-safety across maps of one source: the singleton map's key (No) is
    the file's namespace; a row-level key without it gets the scope-missing risk
    and every candidate rewritten parent-first — `{No}/{(hkl)}` ranks first
    (fewest measurement columns), so the one-click fix IS the correct design."""
    p = _write_reference_card(tmp_path)
    out = annotate_skeleton(_card_skeleton(), [p])["maps"]
    peak = out["peak"]
    assert peak["collapse_kind"] == "unique"
    risks = {r["kind"]: r for r in peak["reference_risks"]}
    assert "measurement-id" in risks  # 2theta is a measured value
    scope = risks["scope-missing"]
    assert scope["parent_map"] == "sample"
    assert scope["parent_columns"] == ["No"]
    assert scope["parent_classes"] == ["xo:Material"]
    candidates = peak["key_candidates"]
    assert candidates, "expected scoped candidates"
    assert all(c["scoped"] is True for c in candidates)
    assert all(c["columns"][0] == "No" for c in candidates)
    assert candidates[0]["columns"] == ["No", "(hkl)"]
    assert candidates[0]["measurement_only"] is False
    # The singleton parent itself keeps its candidates untouched (no risk).
    assert out["sample"]["reference_risks"] == []


def test_scoped_key_already_containing_parent_has_no_scope_risk(tmp_path: Path) -> None:
    p = _write_reference_card(tmp_path)
    skeleton = _card_skeleton()
    skeleton["maps"][1]["subject"]["template"] = "xr:peak/{No}/{(hkl)}"
    ann = annotate_skeleton(skeleton, [p])["maps"]["peak"]
    assert ann["collapse_kind"] == "unique"
    assert ann["reference_risks"] == []


def test_dataset_namespace_info_in_annotations(tmp_path: Path) -> None:
    """The gate's namespace card rides annotations: which prefixes are THIS
    dataset's minted pair, under which base, configured or not (ADR K13)."""
    p = tmp_path / "samples.csv"
    p.write_text("sample_id\nS-1\n", encoding="utf-8")
    skeleton = _skeleton("xr:sample/{sample_id}", source="samples.csv")
    skeleton["prefixes"] = {
        "al3v": "https://asterism.invalid/datasets/al3v-sps2/ontology#",
        "al3vr": "https://asterism.invalid/datasets/al3v-sps2/resource/",
        "schema": "https://schema.org/",
    }
    out = annotate_skeleton(skeleton, [p])
    assert out["dataset_namespace"] == {
        "slug": "al3v-sps2",
        "base": "https://asterism.invalid",
        "base_configured": False,
        "ontology_prefix": "al3v",
        "resource_prefix": "al3vr",
    }
    # Configured base flips the flag (the Settings value arrives resolved).
    out2 = annotate_skeleton(skeleton, [p], iri_base="https://data.lab.jp/asterism")
    assert out2["dataset_namespace"]["base_configured"] is True
    # No minted pair (this file's example.org fixtures) → explicit None.
    out3 = annotate_skeleton(_skeleton("xr:sample/{sample_id}", source="samples.csv"), [p])
    assert out3["dataset_namespace"] is None


# ---------------------------------------------------------------------------
# ADR column-ownership-and-growth: who owns each column, and what the next
# file does to this design.
# ---------------------------------------------------------------------------


def test_child_map_marks_columns_borrowed_from_the_parent(tmp_path: Path) -> None:
    """The peak rows physically carry No/Name, but those are decided by the
    card, not by the peak: they are flagged as borrowed (with the owner) so the
    gate can say "this comes from sample" instead of leaving 47 silent copies.
    """
    p = _write_reference_card(tmp_path)
    out = annotate_skeleton(_card_skeleton(), [p])["maps"]
    borrowed = {b["column"]: b["owner_map"] for b in out["peak"]["borrowed_columns"]}
    assert borrowed == {"Name": "sample"}  # No is the parent KEY (a join, exempt)
    # ... and the card's own property row is stamped, so the UI can grey it out.
    props = {p["column"]: p for p in out["peak"]["entity_preview"]["properties"]}
    assert props["Name"]["owner_map"] == "sample"
    assert "owner_map" not in props["2theta"]  # genuinely the peak's own value
    # The parent owns them, so it has nothing borrowed itself.
    assert "borrowed_columns" not in out["sample"]


def test_parent_map_names_where_its_per_row_columns_go(tmp_path: Path) -> None:
    """The mirror of `borrowed` (G12). The parent card already knows WHICH
    columns it cannot carry (`varying_columns`); without the destination it can
    only state an absence, while the child states the same relation with rows.
    """
    p = _write_reference_card(tmp_path)
    out = annotate_skeleton(_card_skeleton(), [p])["maps"]
    assert out["sample"]["entity_preview"]["varying_columns"] == ["2theta", "d", "I", "(hkl)"]
    delegated = {d["column"]: d["owner_map"] for d in out["sample"]["delegated_columns"]}
    assert delegated == {"2theta": "peak", "d": "peak", "I": "peak", "(hkl)": "peak"}
    # The child carries them itself, so it delegates nothing.
    assert "delegated_columns" not in out["peak"]


def test_card_keeps_its_own_values_when_metadata_fills_the_cap(tmp_path: Path) -> None:
    """The column cap must not be spent on columns identical in every row.

    Real XRD card: 13 file-scoped metadata columns come FIRST in file order, so
    the peak card showed its key and then CSD/Name/Formula/… while its own
    2theta/d/I fell into "…and N more" — the card for a peak showed no peak.
    """
    p = tmp_path / "wide.csv"
    meta = [f"M{i}" for i in range(10)]
    p.write_text(
        ",".join([*meta, "2theta", "d", "I", "(hkl)"])
        + "\n"
        + "\n".join(
            ",".join([*(f"m{i}" for i in range(10)), f"2{n}.5", f"4.{n}", f"{n}.0", f'"(0,0,{n})"'])
            for n in range(1, 5)
        )
        + "\n",
        encoding="utf-8",
    )
    skeleton = _skeleton("xr:peak/{(hkl)}", source="wide.csv")
    card = annotate_skeleton(skeleton, [p])["maps"]["point"]["entity_preview"]
    shown = [prop["column"] for prop in card["properties"]]
    assert shown[0] == "(hkl)"  # the key still leads
    assert {"2theta", "d", "I"} <= set(shown)  # this row's own values survive
    # 7 slots after the key: the 3 own values, then 4 of the 10 metadata columns.
    assert card["omitted_columns"] == 6


def test_ownership_is_silent_without_a_finer_map(tmp_path: Path) -> None:
    """One map alone determines everything it carries; calling those columns
    "borrowed" would be nonsense. No second map → no verdict."""
    p = _write_reference_card(tmp_path)
    skeleton = _card_skeleton()
    skeleton["maps"] = [skeleton["maps"][0]]
    out = annotate_skeleton(skeleton, [p])["maps"]
    assert "borrowed_columns" not in out["sample"]


def test_growth_preview_forecasts_the_next_file(tmp_path: Path) -> None:
    """A singleton map is "one entity per file", so the design's behaviour on
    the SECOND file is knowable from one file: the card multiplies, and the
    columns it describes get recorded independently per file."""
    p = _write_reference_card(tmp_path)
    out = annotate_skeleton(_card_skeleton(), [p])["maps"]
    growth = out["sample"]["growth_preview"]
    assert growth["per_source_entities"] == 1
    assert growth["source_count"] == 1
    assert growth["row_maps"] == ["peak"]
    assert "Name" in growth["described_columns"]
    assert "No" not in growth["described_columns"]  # the key IS the identity
    assert "shared_values" not in growth  # nothing to measure with one file
    # A row-level map never claims to be one-per-file.
    assert "growth_preview" not in out["peak"]


def test_growth_preview_measures_overlap_across_sibling_files(tmp_path: Path) -> None:
    """Two cards of the SAME substance: the forecast becomes a measurement —
    `Name` already repeats across files, so it is the column worth splitting
    out (it would fork into two copies instead of merging)."""
    first = _write_reference_card(tmp_path)
    second = tmp_path / "card2.csv"
    second.write_text(
        "No,Name,2theta,d,I,(hkl)\n"
        '03-065-5860,Aluminum Vanadium,21.40,4.150,6.0,"(0,0,2)"\n'
        '03-065-5860,Aluminum Vanadium,25.90,3.430,12.0,"(1,0,1)"\n',
        encoding="utf-8",
    )
    skeleton = _card_skeleton()
    skeleton["maps"].append(
        {
            "name": "sample2",
            "source": "card2.csv",
            "subject": {"template": "xr:sample/{No}", "classes": ["xo:Material"]},
        }
    )
    out = annotate_skeleton(skeleton, [first, second])["maps"]
    growth = out["sample"]["growth_preview"]
    assert growth["source_count"] == 2
    shared = {s["column"]: s for s in growth["shared_values"]}
    assert shared["Name"]["value"] == "Aluminum Vanadium"
    assert shared["Name"]["files"] == 2
    # The card number differs between the files, so it is NOT a merge candidate.
    assert "No" not in shared


def test_missing_row_kind_offers_the_map_that_does_not_exist(tmp_path: Path) -> None:
    """Round-0 returning ONE map for a card + its rows leaves the per-row values
    homeless. The card already says they "belong to the row-level kind" — so the
    gate names that missing kind and hands over the one-click repair."""
    p = _write_reference_card(tmp_path)
    skeleton = _card_skeleton()
    skeleton["maps"] = [skeleton["maps"][0]]  # only 'sample' — the observed round-0
    ann = annotate_skeleton(skeleton, [p])["maps"]["sample"]
    gap = ann["missing_row_kind"]
    assert gap["columns"] == ["2theta", "d", "I", "(hkl)"]
    # Parent-scoped and NOT measurement-only: {No}/{(hkl)} beats {No}/{2theta}.
    assert gap["suggested_key"] == ["No", "(hkl)"]
    assert gap["entity_count"] == 3
    assert gap["suggested_name"] == "sample_detail"
    # Minted in the parent's namespace — never an invented one.
    # A CURIE, like the skeleton it goes back into (never a half-expanded IRI).
    assert gap["suggested_template"] == "xr:sample_detail/{No}/{(hkl)}"
    # A starter class in the parent's own vocabulary — an empty "what is this
    # row?" column is what makes a machine-added map meaningless.
    assert gap["suggested_classes"] == ["xo:SampleDetail"]


def test_no_missing_row_kind_once_a_row_level_map_exists(tmp_path: Path) -> None:
    """With both maps present nothing is homeless — the gate stays silent."""
    p = _write_reference_card(tmp_path)
    out = annotate_skeleton(_card_skeleton(), [p])["maps"]
    assert "missing_row_kind" not in out["sample"]
    assert "missing_row_kind" not in out["peak"]


# ---------------------------------------------------------------------------
# ADR G15: splitting a shared concept out — the human's `owns` wins, and the
# gate keeps working with two singletons on one source.
# ---------------------------------------------------------------------------


def _split_skeleton() -> dict:
    """The card design after the human split the substance out: `sample`
    (the card, keyed by No) + `substance` (keyed by Name, `owns` Name) + `peak`."""
    sk = _card_skeleton()
    sk["maps"][1]["subject"]["template"] = "xr:peak/{No}/{(hkl)}"  # already scoped
    sk["maps"].append(
        {
            "name": "substance",
            "source": "card.csv",
            "subject": {"template": "xr:substance/{Name}", "classes": ["xo:Substance"]},
            "owns": ["Name"],
        }
    )
    return sk


def test_human_owns_wins_the_singleton_tie(tmp_path: Path) -> None:
    """Within ONE file `sample/{No}` and `substance/{Name}` both mint one entity,
    so the machine cannot say who owns `Name` (a tie → silence). The human's
    `owns` is world knowledge: substance owns it, and the card now BORROWS it."""
    p = _write_reference_card(tmp_path)
    out = annotate_skeleton(_split_skeleton(), [p])["maps"]
    card_borrowed = {b["column"]: b["owner_map"] for b in out["sample"].get("borrowed_columns", [])}
    assert card_borrowed == {"Name": "substance"}
    # …and the peak, which used to borrow Name from the card, now borrows it
    # from its real owner.
    peak_borrowed = {b["column"]: b["owner_map"] for b in out["peak"]["borrowed_columns"]}
    assert peak_borrowed["Name"] == "substance"
    # The substance's own key is never "borrowed" by itself.
    assert "borrowed_columns" not in out["substance"] or not any(
        b["column"] == "Name" for b in out["substance"]["borrowed_columns"]
    )


def test_split_keeps_the_parent_and_the_forecast_alive(tmp_path: Path) -> None:
    """G7 wanted exactly one singleton; a split adds a second ON PURPOSE. The
    map carrying `owns` is never the parent, so the growth forecast still hangs
    off the card — and now describes only what stayed on it."""
    p = _write_reference_card(tmp_path)
    out = annotate_skeleton(_split_skeleton(), [p])["maps"]
    growth = out["sample"]["growth_preview"]
    assert "Name" not in growth["described_columns"]  # it left with the substance
    assert "growth_preview" not in out["substance"]  # the split map is not the parent
    # The offer is always there; with one file nothing is pre-checked.
    assert growth["split_default"] == {"columns": [], "key": None}


def test_split_default_prefills_from_measured_overlap(tmp_path: Path) -> None:
    """Two cards of the same substance: the pre-check IS the measured overlap,
    and the suggested key is the identity-like shared column (Name), not a
    number the files happen to agree on."""
    first = _write_reference_card(tmp_path)
    second = tmp_path / "card2.csv"
    second.write_text(
        "No,Name,2theta,d,I,(hkl)\n"
        '03-065-5860,Aluminum Vanadium,21.40,4.150,6.0,"(0,0,2)"\n',
        encoding="utf-8",
    )
    sk = _card_skeleton()
    sk["maps"].append(
        {"name": "sample2", "source": "card2.csv",
         "subject": {"template": "xr:sample/{No}", "classes": ["xo:Material"]}}
    )
    growth = annotate_skeleton(sk, [first, second])["maps"]["sample"]["growth_preview"]
    assert growth["split_default"] == {"columns": ["Name"], "key": "Name"}


def test_missing_row_kind_survives_a_split(tmp_path: Path) -> None:
    """A split (two singletons) must not silence the "peaks have nowhere to go"
    repair — the parent is still the original card."""
    p = _write_reference_card(tmp_path)
    sk = _split_skeleton()
    sk["maps"] = [m for m in sk["maps"] if m["name"] != "peak"]  # no row-level map
    out = annotate_skeleton(sk, [p])["maps"]
    assert out["sample"]["missing_row_kind"]["suggested_key"] == ["No", "(hkl)"]
    assert "missing_row_kind" not in out["substance"]


# ---------------------------------------------------------------------------
# apply_key_safety_fix — a machine rewrite of the caution key BEFORE the human
# ever sees it (the "safe key before the gate" follow-up to K7).
# ---------------------------------------------------------------------------


def _write_xrd_unique(tmp_path: Path) -> Path:
    """Same shape as ``_write_xrd`` but with a key that IS unique today — the
    K7 caution state (unique now, measurement-valued, unsafe long-term)."""
    p = tmp_path / "xrd.csv"
    p.write_text(
        "2θ (deg),intensity,scan_id\n10.00,120,S1\n10.02,135,S1\n10.04,98,S2\n",
        encoding="utf-8",
    )
    return p


def test_measurement_key_is_replaced_by_the_top_safe_candidate(tmp_path: Path) -> None:
    p = _write_xrd_unique(tmp_path)
    skeleton = _skeleton("xr:point/{2θ (deg)}")
    out = annotate_skeleton(skeleton, [p])
    ann = out["maps"]["point"]
    assert ann["key_measurement_caution"] is True
    top_safe = next(c for c in ann["key_candidates"] if c["measurement_only"] is False)

    fixed, fixes = apply_key_safety_fix(skeleton, out)

    assert fixes.keys() == {"point"}
    record = fixes["point"]
    assert record["from"] == ["2θ (deg)"]
    assert record["to"] == top_safe["columns"]
    assert record["reason"] == "measurement-id"
    assert record["template_from"] == "xr:point/{2θ (deg)}"
    # The head up to the first `{` is preserved verbatim (same rule as the
    # UI's one-click "apply candidate").
    assert record["template_to"].startswith("xr:point/")
    assert record["template_to"] == record["template_from"].split("{")[0] + "/".join(
        f"{{{c}}}" for c in top_safe["columns"]
    )
    new_template = fixed["maps"][0]["subject"]["template"]
    assert new_template == record["template_to"]
    # Re-annotating with the new key proves it out — no more caution.
    reann = annotate_skeleton(fixed, [p])["maps"]["point"]
    assert reann["key_measurement_caution"] is False


def test_empty_candidate_columns_are_refused_even_if_offered(tmp_path: Path) -> None:
    """Defensive: today's candidate generator never proves an empty key unique,
    but if a hand-built (or future) annotation ever offered one, the function
    must refuse it rather than collapse every row onto one constant ID."""
    p = _write_xrd_unique(tmp_path)
    skeleton = _skeleton("xr:point/{2θ (deg)}")
    out = annotate_skeleton(skeleton, [p])
    # Poison the real candidates with an empty-key one ranked first.
    out["maps"]["point"]["key_candidates"] = [
        {"columns": [], "rows_considered": 3, "measurement_only": False},
        *out["maps"]["point"]["key_candidates"],
    ]

    fixed, fixes = apply_key_safety_fix(skeleton, out)

    assert fixes == {}
    assert fixed["maps"][0]["subject"]["template"] == "xr:point/{2θ (deg)}"


def test_no_safe_candidate_leaves_the_key_untouched(tmp_path: Path) -> None:
    """A pure numeric instrument sweep (every column measured): no candidate can
    be safe, so the caution stays and nothing is rewritten."""
    p = tmp_path / "sweep.csv"
    p.write_text(
        "field(T),voltage(V)\n0.10,1.234E-3\n0.20,2.468E-3\n0.30,3.702E-3\n",
        encoding="utf-8",
    )
    skeleton = _skeleton("xr:pt/{field(T)}", source="sweep.csv")
    out = annotate_skeleton(skeleton, [p])
    ann = out["maps"]["point"]
    assert ann["key_measurement_caution"] is True
    assert all(c["measurement_only"] for c in ann["key_candidates"])

    fixed, fixes = apply_key_safety_fix(skeleton, out)

    assert fixes == {}
    assert fixed["maps"][0]["subject"]["template"] == "xr:pt/{field(T)}"


def test_non_measurement_key_is_never_touched_even_with_scope_missing(tmp_path: Path) -> None:
    """A text key is never rewritten — including when the ONLY risk showing is
    scope-missing, which is a human call about how the dataset grows, not one
    the machine gets to make for them."""
    p = _write_reference_card(tmp_path)
    skeleton = _card_skeleton()
    skeleton["maps"][1]["subject"]["template"] = "xr:peak/{(hkl)}"  # text-ish key column
    out = annotate_skeleton(skeleton, [p])
    peak = out["maps"]["peak"]
    assert peak["key_measurement_caution"] is False
    risk_kinds = {r["kind"] for r in peak["reference_risks"]}
    assert risk_kinds == {"scope-missing"}

    fixed, fixes = apply_key_safety_fix(skeleton, out)

    assert fixes == {}
    assert fixed["maps"][1]["subject"]["template"] == "xr:peak/{(hkl)}"


def test_apply_key_safety_fix_is_idempotent(tmp_path: Path) -> None:
    p = _write_xrd_unique(tmp_path)
    skeleton = _skeleton("xr:point/{2θ (deg)}")
    out = annotate_skeleton(skeleton, [p])
    fixed, fixes = apply_key_safety_fix(skeleton, out)
    assert fixes  # sanity: the first pass did change something

    reann = annotate_skeleton(fixed, [p])
    fixed_again, fixes_again = apply_key_safety_fix(fixed, reann)

    assert fixes_again == {}
    assert fixed_again["maps"][0]["subject"]["template"] == fixed["maps"][0]["subject"]["template"]


def test_apply_key_safety_fix_does_not_mutate_the_original_skeleton(tmp_path: Path) -> None:
    p = _write_xrd_unique(tmp_path)
    skeleton = _skeleton("xr:point/{2θ (deg)}")
    out = annotate_skeleton(skeleton, [p])

    fixed, fixes = apply_key_safety_fix(skeleton, out)

    assert fixes  # sanity
    assert skeleton["maps"][0]["subject"]["template"] == "xr:point/{2θ (deg)}"
    assert fixed is not skeleton
    assert fixed["maps"][0]["subject"]["template"] != skeleton["maps"][0]["subject"]["template"]


def test_apply_key_safety_fix_only_touches_the_offending_map(tmp_path: Path) -> None:
    xrd = _write_xrd_unique(tmp_path)
    samples = tmp_path / "samples.csv"
    samples.write_text("sample_id,alloy\nS-1,WC\nS-2,TiN\n", encoding="utf-8")
    skeleton = {
        "version": 1,
        "prefixes": dict(_PREFIXES),
        "maps": [
            {
                "name": "point",
                "source": "xrd.csv",
                "subject": {"template": "xr:point/{2θ (deg)}", "classes": ["xo:DataPoint"]},
            },
            {
                "name": "sample",
                "source": "samples.csv",
                "subject": {"template": "xr:sample/{sample_id}", "classes": ["xo:Sample"]},
            },
        ],
    }
    out = annotate_skeleton(skeleton, [xrd, samples])

    fixed, fixes = apply_key_safety_fix(skeleton, out)

    assert fixes.keys() == {"point"}
    assert fixed["maps"][0]["subject"]["template"] != "xr:point/{2θ (deg)}"
    assert fixed["maps"][1]["subject"]["template"] == "xr:sample/{sample_id}"
    # The untouched map's entry is shared (not deep-copied) — a light-weight
    # non-mutation guarantee is enough since it is never written to.
    assert fixed["maps"][1] is skeleton["maps"][1]
