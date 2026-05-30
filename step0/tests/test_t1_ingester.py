"""Tests for csv2rdf_step0.t1_ingester (ingester IRI-builder key recovery)."""
from __future__ import annotations

from textwrap import dedent

from csv2rdf_step0.t1_ingester import IngesterKey, extract_ingester_keys


def _keys(src: str, columns: list[str] | None = None) -> dict[str, IngesterKey]:
    """Index recovered keys by entity for terse assertions."""
    return {k.entity: k for k in extract_ingester_keys(dedent(src), columns)}


# ----------------------------------------------------------------------------
# Builder-function style (LLM / proposal output)
# ----------------------------------------------------------------------------


def test_builder_style_call_site_maps_params_to_columns() -> None:
    src = """
    SDR = None
    def sample_iri(sid, sample_id):
        return SDR[f"sample/{sid}-{sample_id}"]
    def emit(row):
        return sample_iri(row["SID"], row["sample_id"])
    """
    keys = _keys(src, ["SID", "sample_id"])
    assert keys["sample"].columns == ("SID", "sample_id")
    assert keys["sample"].fully_resolved
    assert keys["sample"].func == "sample_iri"


def test_builder_style_header_fallback_when_no_call_site() -> None:
    """When no call site reads a row column, fall back to case-insensitive
    matching of the parameter name against the CSV header (sid -> SID)."""
    src = """
    SDR = None
    def paper_iri(sid, doi):
        return SDR[f"paper/{sid}-{doi}"]
    """
    keys = _keys(src, ["SID", "DOI", "title"])
    assert keys["paper"].columns == ("SID", "DOI")
    assert keys["paper"].fully_resolved


def test_builder_style_unwraps_slug_and_str_transforms() -> None:
    src = """
    SDR = None
    def _slug(s):
        return s.replace("/", "-")
    def paper_iri(sid, doi):
        return SDR[f"paper/{sid}-{_slug(doi)}"]
    def emit(row):
        return paper_iri(row["SID"], row.get("DOI", ""))
    """
    keys = _keys(src, ["SID", "DOI"])
    assert keys["paper"].columns == ("SID", "DOI")
    assert keys["paper"].fully_resolved


# ----------------------------------------------------------------------------
# Inline style (Phase 1 hand-written)
# ----------------------------------------------------------------------------


def test_inline_style_traces_local_fstring_chain() -> None:
    src = """
    def emit(row, sdr):
        sample_id = row.get("sample_id", "").strip()
        paper_sid = row.get("SID", "").strip()
        sample_key = f"{paper_sid}-{sample_id}"
        sample = sdr[f"sample/{sample_key}"]
        return sample
    """
    keys = _keys(src, ["SID", "sample_id"])
    assert keys["sample"].columns == ("SID", "sample_id")
    assert keys["sample"].fully_resolved


def test_inline_style_three_way_composite() -> None:
    src = """
    def emit(row, sdr):
        fig_id = row.get("figure_id", "").strip()
        sample_id = row.get("sample_id", "").strip()
        paper_sid = row.get("SID", "").strip()
        curve_key = f"{paper_sid}-{fig_id}-{sample_id}"
        curve = sdr[f"curve/{curve_key}"]
    """
    keys = _keys(src, ["SID", "figure_id", "sample_id"])
    assert keys["curve"].columns == ("SID", "figure_id", "sample_id")
    assert keys["curve"].fully_resolved


# ----------------------------------------------------------------------------
# Conservative behaviour: untraceable placeholders stay unresolved
# ----------------------------------------------------------------------------


def test_loop_index_left_unresolved_not_guessed() -> None:
    """A secondary resource keyed by a loop index must NOT be reported as a
    fully-resolved key (it would otherwise trigger a false uniqueness failure)."""
    src = """
    def emit(row, sdr):
        sample_id = row.get("sample_id", "").strip()
        paper_sid = row.get("SID", "").strip()
        sample_key = f"{paper_sid}-{sample_id}"
        for i, d in enumerate(items):
            descriptor = sdr[f"descriptor/{sample_key}/{i}"]
    """
    keys = _keys(src, ["SID", "sample_id"])
    desc = keys["descriptor"]
    assert not desc.fully_resolved
    assert "i" in desc.unresolved
    assert desc.columns == ("SID", "sample_id")  # the resolvable part is still recovered


def test_single_key_is_recovered_as_safety_net() -> None:
    """The whole point: a wrong single-key ingester exposes (sample_id,) so a
    full-CSV validate can catch it even if the MIE looks composite."""
    src = """
    def emit(row, sdr):
        sample_id = row.get("sample_id", "").strip()
        sample = sdr[f"sample/{sample_id}"]
    """
    keys = _keys(src, ["SID", "sample_id"])
    assert keys["sample"].columns == ("sample_id",)
    assert keys["sample"].fully_resolved


def test_broken_source_returns_empty() -> None:
    assert extract_ingester_keys("def oops(:\n    pass", ["SID"]) == []


def test_deduplicates_same_entity_and_columns() -> None:
    """sample IRI minted in two functions with the same key → one entry."""
    src = """
    def a(row, sdr):
        sid = row["SID"]; s = row["sample_id"]
        return sdr[f"sample/{sid}-{s}"]
    def b(row, sdr):
        sid = row["SID"]; s = row["sample_id"]
        return sdr[f"sample/{sid}-{s}"]
    """
    all_keys = extract_ingester_keys(dedent(src), ["SID", "sample_id"])
    sample_keys = [k for k in all_keys if k.entity == "sample"]
    assert len(sample_keys) == 1
