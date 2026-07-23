"""Unit tests for the instance IRI base (ADR instance-iri-base.md).

Pure functions — no LLM, no environment. The design-prompt injection and the
parse-time guard are covered where they live (test_staged_propose /
test_propose / test_mapping_ir); this file pins the primitives.
"""
from __future__ import annotations

from asterism_step0.instance_iri import (
    DEFAULT_IRI_BASE,
    dataset_namespace_block,
    dataset_namespace_info,
    derive_prefix_pair,
    normalize_dataset_namespace,
    normalize_iri_base,
    placeholder_prefix_issue,
    slugify_dataset_name,
)


def test_normalize_falls_back_to_invalid_default() -> None:
    assert normalize_iri_base(None) == DEFAULT_IRI_BASE
    assert normalize_iri_base("") == DEFAULT_IRI_BASE
    assert normalize_iri_base("   ") == DEFAULT_IRI_BASE
    assert DEFAULT_IRI_BASE.endswith(".invalid")  # RFC 2606: never resolves


def test_normalize_strips_trailing_slash() -> None:
    assert normalize_iri_base("https://data.lab.jp/asterism/") == "https://data.lab.jp/asterism"
    assert normalize_iri_base("https://data.lab.jp") == "https://data.lab.jp"


def test_namespace_block_pins_base_and_shape() -> None:
    block = dataset_namespace_block("https://data.lab.jp/asterism/")
    assert "https://data.lab.jp/asterism/datasets/<slug>/ontology#" in block
    assert "https://data.lab.jp/asterism/datasets/<slug>/resource/" in block
    assert "example.org" in block  # the explicit NEVER rule


def test_namespace_block_unset_uses_default() -> None:
    block = dataset_namespace_block(None)
    assert f"{DEFAULT_IRI_BASE}/datasets/<slug>/ontology#" in block


def test_placeholder_detects_example_domains_and_localhost() -> None:
    assert placeholder_prefix_issue("sd", "https://example.org/xrd-ontology#")
    assert placeholder_prefix_issue("sdr", "http://www.example.com/resource/")
    assert placeholder_prefix_issue("x", "https://sub.example.net/ns#")
    assert placeholder_prefix_issue("x", "http://localhost:8080/ns#")


def test_placeholder_allows_real_and_invalid_namespaces() -> None:
    # Real namespaces pass.
    assert placeholder_prefix_issue("sd", "https://kumagallium.github.io/asterism/x#") is None
    assert placeholder_prefix_issue("schema", "https://schema.org/") is None
    # The unconfigured-instance default is deliberate, not a placeholder.
    assert placeholder_prefix_issue("sd", f"{DEFAULT_IRI_BASE}/datasets/x/ontology#") is None
    # A host that merely CONTAINS the word keeps working (example ≠ example.org).
    assert placeholder_prefix_issue("x", "https://exampleuniversity.edu/ns#") is None


def test_placeholder_ignores_unparseable_iris() -> None:
    # Structural checks own malformed IRIs; the guard must not throw or fire.
    assert placeholder_prefix_issue("x", "http://[bad") is None


# ---------------------------------------------------------------------------
# Deterministic dataset-namespace naming (kantan ADR K13).
# ---------------------------------------------------------------------------


def test_slugify_dataset_name() -> None:
    assert slugify_dataset_name("Al3V-SPS-2_analysis") == "al3v-sps-2-analysis"
    assert slugify_dataset_name("ZEM 熱電測定") == "zem"
    assert slugify_dataset_name("") == "dataset"
    assert slugify_dataset_name(None) == "dataset"


def test_derive_prefix_pair_basic() -> None:
    assert derive_prefix_pair("al3v-sps2") == ("al3v", "al3vr")
    assert derive_prefix_pair("zem-al3v") == ("zem", "zemr")
    assert derive_prefix_pair("xrd") == ("xrd", "xrdr")


def test_derive_prefix_pair_avoids_reserved_and_taken() -> None:
    # "schema" is a standard vocabulary — extend with the next token.
    assert derive_prefix_pair("schema-dump") == ("schemadump", "schemadumpr")
    # Already taken in this design — extend instead of shadowing.
    assert derive_prefix_pair("al3v-sps2", taken=["al3v"]) == ("al3vsps2", "al3vsps2r")
    # The pair must be free as a PAIR (ontology name colliding via the 'r' twin).
    assert derive_prefix_pair("zem", taken=["zemr"]) == ("ds", "dsr")


def test_derive_prefix_pair_ncname_safe_and_last_resort() -> None:
    # CURIE prefixes are NCNames — a digit head gets a 'd'.
    assert derive_prefix_pair("3v-alloy")[0][0].isdigit() is False
    assert derive_prefix_pair("3v-alloy") == ("d3v", "d3vr")
    # Nothing usable at all → ds, then ds2, ds3, …
    assert derive_prefix_pair("") == ("ds", "dsr")
    assert derive_prefix_pair("", taken=["ds"]) == ("ds2", "ds2r")


def test_dataset_namespace_info_detects_minted_pair() -> None:
    prefixes = {
        "al3v": f"{DEFAULT_IRI_BASE}/datasets/al3v-sps2/ontology#",
        "al3vr": f"{DEFAULT_IRI_BASE}/datasets/al3v-sps2/resource/",
        "schema": "https://schema.org/",
    }
    info = dataset_namespace_info(prefixes, None)
    assert info == {
        "slug": "al3v-sps2",
        "base": DEFAULT_IRI_BASE,
        "base_configured": False,
        "ontology_prefix": "al3v",
        "resource_prefix": "al3vr",
    }


def test_dataset_namespace_info_configured_base_and_none() -> None:
    base = "https://data.lab.jp/asterism"
    prefixes = {"x": f"{base}/datasets/xrd/ontology#"}
    info = dataset_namespace_info(prefixes, base)
    assert info is not None
    assert info["base_configured"] is True
    assert info["ontology_prefix"] == "x"
    assert info["resource_prefix"] is None  # pair half-missing is reported as-is
    # No minted namespace at all → None (the gate falls back to the raw view).
    assert dataset_namespace_info({"schema": "https://schema.org/"}, base) is None


def _skeleton(prefixes: dict, template: str, classes: list[str]) -> dict:
    return {
        "version": 1,
        "prefixes": prefixes,
        "maps": [
            {
                "name": "measurement",
                "source": "a.csv",
                "subject": {"template": template, "classes": classes},
            }
        ],
    }


def test_normalize_is_idempotent_on_canonical_input() -> None:
    sk = _skeleton(
        {
            "al3v": f"{DEFAULT_IRI_BASE}/datasets/al3v-sps2/ontology#",
            "al3vr": f"{DEFAULT_IRI_BASE}/datasets/al3v-sps2/resource/",
            "schema": "https://schema.org/",
        },
        "al3vr:measurement/{T}",
        ["al3v:Measurement"],
    )
    assert normalize_dataset_namespace(sk, None) == sk


def test_normalize_renames_model_chosen_prefixes_to_derived_pair() -> None:
    # The model minted the right IRIs but named them arbitrarily — the name is
    # not its judgment to make: derived pair wins, CURIEs follow in lockstep.
    sk = _skeleton(
        {
            "myonto": f"{DEFAULT_IRI_BASE}/datasets/al3v-sps2/ontology#",
            "myres": f"{DEFAULT_IRI_BASE}/datasets/al3v-sps2/resource/",
            "schema": "https://schema.org/",
        },
        "myres:measurement/{T}",
        ["myonto:Measurement", "schema:Observation"],
    )
    out = normalize_dataset_namespace(sk, None)
    assert set(out["prefixes"]) == {"al3v", "al3vr", "schema"}
    subject = out["maps"][0]["subject"]
    assert subject["template"] == "al3vr:measurement/{T}"
    assert subject["classes"] == ["al3v:Measurement", "schema:Observation"]


def test_normalize_repairs_wrong_base_to_instance_base() -> None:
    # Right shape, wrong owner (the upstream author's domain) — re-minted under
    # THIS instance's base; slug survives.
    base = "https://data.lab.jp/asterism"
    sk = _skeleton(
        {
            "x": "https://kumagallium.github.io/asterism/datasets/xrd-powder/ontology#",
            "xr": "https://kumagallium.github.io/asterism/datasets/xrd-powder/resource/",
        },
        "xr:card/{ID}",
        ["x:Card"],
    )
    out = normalize_dataset_namespace(sk, base)
    assert out["prefixes"]["xrd"] == f"{base}/datasets/xrd-powder/ontology#"
    assert out["prefixes"]["xrdr"] == f"{base}/datasets/xrd-powder/resource/"
    assert out["maps"][0]["subject"]["classes"] == ["xrd:Card"]


def test_normalize_rescues_placeholder_mints_by_use() -> None:
    # example.org mints don't match the canonical shape — classified by USE
    # (classes → ontology, subject template → resource) and repaired.
    sk = _skeleton(
        {
            "sd": "https://example.org/xrd#",
            "sdx": "https://example.org/xrd/resource/",
            "schema": "https://schema.org/",
        },
        "sdx:peak/{ID}",
        ["sd:Peak"],
    )
    out = normalize_dataset_namespace(sk, None, fallback_slug="xrd-powder")
    assert out["prefixes"]["xrd"] == f"{DEFAULT_IRI_BASE}/datasets/xrd-powder/ontology#"
    assert out["prefixes"]["xrdr"] == f"{DEFAULT_IRI_BASE}/datasets/xrd-powder/resource/"
    assert "sd" not in out["prefixes"] and "sdx" not in out["prefixes"]
    subject = out["maps"][0]["subject"]
    assert subject["template"] == "xrdr:peak/{ID}"
    assert subject["classes"] == ["xrd:Peak"]


def test_normalize_leaves_unrecognized_skeletons_untouched() -> None:
    # Only standard vocabularies, nothing minted: pass through (the placeholder
    # gate still guards genuinely broken designs).
    sk = _skeleton({"schema": "https://schema.org/"}, "schema:obs/{ID}", ["schema:Observation"])
    assert normalize_dataset_namespace(sk, None, fallback_slug="x") == sk
